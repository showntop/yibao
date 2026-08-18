# R4 阶段五 Task 5 报告：累积小修包（孤儿回收匹配 + 死码/小修 + 注释悬空清理）

## 状态

完成。分支 feat/r4-module-panel-runtime（worktree `.worktrees/r4-stage1`），三个 commit：

- `c90995c` fix: 大脑孤儿回收认入口点 cmdline + codex fallback 清旧 usage 差分基准(R4 阶段五 T5)
- `0c321cf` fix: fileRefPaths 死导出/团子聚焦误中隐藏 input/review 悬停 params + wall 悬空注释清理(R4 阶段五 T5)
- 本报告 + spec 状态行随第三个 docs commit 入库

## 逐项落实（简报 5 项 + 追加 3 项）

1. **大脑孤儿回收匹配修复**（`sidecar/src/yibao_brain/instance.py`）：
   新增 `_BRAIN_ENTRY = "yibao-brain-server"`（uv run 入口点连字符形态，pgrep 正则 `.`
   能匹配但 Python 字面 `in` 不行），`_is_brain_process` 判据改
   `(_BRAIN_PATTERN in cmd or _BRAIN_ENTRY in cmd) and ("python" in cmd or ".venv" in cmd)`。
   `sidecar/tests/test_instance.py` 加 3 用例（mock subprocess.run 伪造 ps 输出）：
   `-m yibao_brain.server` 形态 True、`.venv/bin/python .../yibao-brain-server` 入口点形态
   True（uv run 常驻进程即此形态）、无关进程（node 撞名 / 裸 python http.server）False。
2. **fileRefPaths 死导出**（`app/src/lib/at-mention.ts`）：grep 确认 app/src 仅测试引用，
   删导出 + at-mention.test.ts 对应 describe 块与 import（InputContext 仍被其他用例用，保留）。
3. **PanelApp focusInput 误中**（`app/src/components/PanelApp.vue`）：
   `querySelector("input")` 会命中 InputBar 隐藏 file-input；改经 `inputBarRef` 调 InputBar
   已 expose 的 `focus()`（defineExpose { focus, insertText }，:337）。随之失效的 `barRef`
   （仅 focusInput 使用）声明与模板 ref 一并删。
4. **review tap params**（`plugins/coding/panel/`）：format.ts 加 `permPublicParams(input)`
   镜像后端 `_runner._public_params`——command/file_path/path 按序取一、假值跳过、值截 200 字，
   否则 {}；format.test.ts 加 4 用例（优先级序/截 200/假值跳过/非 dict → {}）；
   App.vue demux tap 的 `review.upsert` 把写死的 `params: {}` 改调 `permPublicParams(ev.input)`
   （ReviewItem.params 类型 Record<string, string> 吻合，悬停不再空）。
5. **spec 状态行**（`docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md`）：
   落地顺序 5 步每步尾加 ✅(2026-08-19)（本地日期经 `date` 核实）；第 5 步留档小修四件中
   takeover 件注「退役即关闭(阶段三 T7)」。
6. **追加（S5-T2 评审留）**（`plugins/coding/skills/coding.py`）：fallback 分支重调
   `runner.run` 前 `_SESSIONS.get(sid)` entry `pop("usage_baseline", None)`——fallback 是
   新会话（usage 从 0 累计），旧 thread 累计基准会把本轮差分钳 0 少报。
   `sidecar/tests/test_codex_runner.py` 加 `test_resume_fallback_clears_usage_baseline` 钉死。
7. **追加（S5-T4 评审留）** panel 3 处 wall 悬空注释：
   format.ts:2 relTime 头注不再指已删的 wall subtitle/_rel_time（改注 rail 行/历史浮层用途）；
   App.vue:132「对齐 wall 刷新惯例」→「rail 刷新惯例;原对齐的会话墙已退役」；
   format.test.ts:45 用例名去「与 wall subtitle 语义一致」。chat.html 整文件已删，
   其余「移植 chat.html:NNNN」为出处注记，按最小改动未动。

## 验证（闸门全绿）

- `cd sidecar && .venv/bin/pytest tests/ -q` → **1090 passed**（基线 1086 + instance 3 + codex fallback 1）
- `cd app && pnpm test` → **114 passed**（基线 115 − at-mention 删块 1）；`pnpm build` ✓
- `cd plugins/coding/panel && pnpm test` → **138 passed**（基线 134 + permPublicParams 4）；
  `pnpm build` ✓、`pnpm typecheck`（vue-tsc --noEmit）✓

## 未入库

- `sidecar/uv.lock` 既有本地改动（pypi → 清华镜像源），与本任务无关，未 stage。
- `app/src-tauri/target` 未跟踪构建产物，未 stage。

## concerns

- `_is_brain_process` 仍要求 cmdline 含 python/.venv：若未来大脑以非 venv 形态
  （如全局 pipx）运行，入口点路径不含 .venv 会漏认——当前启动链路（uv run → venv 子进程）
  无此形态，留作已知边界。
- 裸 `uv run yibao-brain-server` 的 uv 父进程 cmdline 不含 python/.venv 判 False，
  回收目标是其 venv python 子进程（持锁者），符合预期。

---

## R4 终审 fix-before-merge（2026-08-18 修复子代理）

**问题**：fallback 成功后 errbar 永红——codex resume 失败 → error 事件置 `state.error` +
ended="error" → fallback 重跑成功 → done 事件到达，但 `session.ts` applyEvent 的 done 分支
不清 `state.error`；codex 会话无 user_msg 回流（轮内唯一既有清 error 路径），errbar 常显红条、
状态行停在错误而非「✓ 完成」，直到下次发送自愈。

**修法**（最小改动）：
1. `plugins/coding/panel/src/stores/session.ts` applyEvent `case "done"`：加 `state.error = null;`
   （终态 done 覆盖同轮 transient error；先例=user_msg 分支清 error）。`case "stopped"` 不动
   （stopped 后 error 保留语义不变）。
2. `plugins/coding/panel/src/stores/session.test.ts` 加用例「error 后 done：终态清 error、
   ended 覆盖为 done；stopped 后 error 保留（语义不变）」。

**验证（闸门全绿）**：
- `cd plugins/coding/panel && pnpm test` → **139 passed**（基线 138 + 新增 1）
- `pnpm build` ✓（dist 产物正常，仅既有 chunk size 警告）
- `pnpm typecheck`（vue-tsc --noEmit）✓
