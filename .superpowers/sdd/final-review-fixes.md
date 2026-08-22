# R4 阶段二 终审收口修复报告

日期：2026-08-18 ｜ 分支：feat/r4-module-panel-runtime（worktree .worktrees/r4-stage1）

## 逐项修复

1. **stale 注释（plugin_proto.rs:193）** — 注释改引 `csp_meta(pid)`（原引用已删除的 `CSP_META` 常量）。
2. **发布管线 panel-build 硬检查（desktop/scripts/prepare-dist.sh）** — 脚本尾部新增硬检查：`plugins/coding/panel/dist/index.html` 缺失时现构建（`node scripts/panel-build/build.mjs coding`，REPO_ROOT 由脚本自身位置推算）。node 缺失立即报错误出；构建依赖（两处 node_modules）缺失时尝试 pnpm install 补齐，pnpm 不可用则报错并给出手动命令；构建完仍缺产物同样报错。原 uv 已就绪时的 `exit 0` 提前退出已改为 if/else，保证 panel 检查必达。已实机冒烟：移走 dist 后跑脚本，uv 复用 + panel 重建 + 「panel 就绪」全链路通过。
3. **CI panel job（.github/workflows/ci.yml）** — 新增 `panel` job：pnpm/action-setup@v4（version 9，对齐 lockfileVersion 9.0）+ setup-node 22（cache pnpm，双 lockfile），在 scripts/panel-build 与 plugins/coding/panel 各 `pnpm install --frozen-lockfile`，然后 `pnpm test`。注：rust job 调 prepare-dist.sh 在干净 runner 上会触发 panel 构建——脚本内已自备依赖补齐路径，无需改 rust job。
4. **恢复/attach 成功反馈（panel/src/App.vue）** — onAttachCc / onAttachCodex / onResumeRow 三条成功路径各加一行 tip（`onComposerStatus("已恢复会话 <sid>，发消息将在同一上下文继续", false)`，措辞逐字对齐原 chat.html:2594 setStatus）。resumeSession 失败返 0 且置 state.error、顺延返 -1，故门控 `n >= 0 && !state.error`，失败仍由错误状态行承载，不会被 tip 盖住。
5. **WebviewPayload 类型统一** — 五处内联 `{ html?: string } | null` 全部换成 `import type { WebviewPayload }`（brain.ts PanelPayload.webview、types.ts SurfacePanel.webview、surface.ts isPanel cast、PanelApp.vue:167、HomePlugins.vue:415；后两处文件本已 import 该类型）。brain.ts 字段注释同步改写（module 面板 {url,v} / 旧 html 面板 srcdoc）。`pnpm build`（含 vue-tsc --noEmit）通过。
6. **panel typecheck 闸门** — package.json 加 `"typecheck": "vue-tsc --noEmit"`，devDep `vue-tsc: ^2.1.10`（解析 2.2.12，与 desktop 的 ^2.1.10 / typescript ^5.6 同代）。首跑暴露 1 个既存类型错误：session.test.ts:651 对 makeDeps 返回的联合类型直接取 `.mock`——trivial，已修为 `as ReturnType<typeof vi.fn>` 收窄（测试行为不变）。修后 typecheck 全绿，无遗留错误上报。
7. **store 头部注释（stores/session.ts:1-3）** — 删去不实的「内部按 sid 分槽」，改为：单会话视图 + 事件按 sid 过滤（陈旧 sid 丢弃，无分槽）；stage 3 多工位 = App 层多实例化本 store + App 做事件 demux 分发。

## 验证闸门（全绿）

- `sidecar .venv/bin/pytest tests/ -q`：1089 passed
- `app pnpm test`：vitest 12 文件 115 passed；`pnpm build`（vue-tsc --noEmit + vite build）通过
- `plugins/coding/panel pnpm test`：9 文件 126 passed；`pnpm build` 通过；`pnpm typecheck` 通过
- `desktop/src-tauri cargo test`：40 passed
- `bash -n desktop/scripts/prepare-dist.sh` 通过；ci.yml PyYAML 解析通过

## 遗留/备注

- `sidecar/uv.lock` 在工作树里被阶段二 `uv sync` 重建 venv 时改写为 tuna 镜像源（仅 registry URL，版本/hash 不变）——非本次修复内容，未纳入提交，留在工作树。
- CI panel job 按评审意见只接 `pnpm test`；typecheck 现已全绿，后续可考虑把 `pnpm typecheck` 一并挂进该 job。
- desktop/src-tauri/target、sidecar/.venv 等未跟踪噪音未入库。
