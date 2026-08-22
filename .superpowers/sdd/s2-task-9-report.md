# S2-T9 报告：coding:studio 上线——panel ref 切换与 chat.html 退役

分支：`feat/r4-module-panel-runtime`(worktree `.worktrees/r4-stage1`)
范围:T8 评审尾项①②修复 + 全仓 `coding:chat` → `coding:studio` 切换 + chat.html/vendor 退役。

---

## 1. T8 评审尾项修复(panel 工程)

根因:原 chat.html 的 takeover 直发与队列泄放都汇入 `send()`,由 `send()` 内联完成
cwd/文本校验(:1879-1880)与跨引擎交接分支(:1884-1887);重写后 `store.takeoverInput`
直驱 `store.send`,两条都被绕过。

### ① 跨引擎 handoff 守卫补回(直发 + 泄放两路径)

- `panel/src/App.vue`
  - 抽出 `runHandoff(prompt, refs)`(`onSend` 与 takeover 直发共用):判据
    `state.currentSession && switchAgent && switchAgent !== state.curSessAgent`,
    编排 `store.handoffSend`(briefTarget 状态行 / isStale / onHandedOff 清待定+同步
    curAgent),brief 失败亮状态行「交接摘要生成失败:…」(原仅状态行不进 errbar)。
    返回 `null` = 无待定切换,调用方走普通 send。
  - `onSend` 改为调 `runHandoff`(行为不变,`sent` 才清 composer)。
  - 新增 `onTakeoverInput(msg)`:busy → 照旧 `store.takeoverInput` 入队(状态行
    「已排队…」);空闲 → 先 `runHandoff`,无交接再 `takeoverInput` 直发。
  - `setQueueContext` 快照增加 `switchAgent`(watch 源同步增加)——泄放路径判据。
  - store deps 新增 `onQueueHandoff(agent)` 回调:泄放路径交接落定后 App 清
    switchAgent + `drivers.setCurAgent`(同 onSend 的 onHandedOff 语义)。
- `panel/src/stores/session.ts`
  - `SessionDeps.onQueueHandoff?` 新增;`queueContext` 增 `switchAgent?: string | null`。
  - `pendingSendFromQueue` 加同一守卫:`currentSession && sw && sw !== curSessAgent`
    → `handoffSend`(isStale 读快照漂移 `queueContext.switchAgent !== sw`;
    onHandedOff 经 `deps.onQueueHandoff` 回告 App);brief 失败泄放路径无状态行通道,
    落 `state.error`(errbar)兜底,文案「交接摘要生成失败:…」。

### ② takeover 路径校验补回

`onTakeoverInput` 在 `store.takeoverInput` 之前按 onSend 同序校验:空 cwd →
状态行「请先选择项目目录」+ 开 cwd 浮层(ctx 行 takeover 态仍可见,可选目录);
空文本 → 「请输入任务描述」。拒发不入队(原在 send() 内拒、出队不补发,等价语义,
且比原版反馈更早)。

### 新增测试(panel/src/stores/session.test.ts,「评审回归」块,+2 例)

- 泄放跨引擎守卫:快照带 switchAgent≠curSessAgent → 泄放走 session_brief →
  coding.start(带新引擎/交接 prompt),`onQueueHandoff` 回告,旧会话进 discarded;
  同引擎快照 → 普通 coding.send,不调 session_brief。
- 泄放守卫边界:brief 等待期快照漂移 → stale 丢弃不发 start、旧会话保留;
  brief 失败 → `state.error` 兜底、sending 复位。

---

## 2. `coding:chat` 全仓引用清单与裁定

运行时引用全部改 `coding:studio`;历史文档(docs/)一律不动。

| # | 位置 | 性质 | 裁定 |
|---|------|------|------|
| 1 | `plugins/coding/skills/coding.py:6` 模块 docstring | 运行时注释 | 改 coding:studio |
| 2 | `coding.py:222` `_stream` on_event emit panel | 运行时 | 改 |
| 3 | `coding.py:400` StartSkill data.panel(background 旁路不变) | 运行时 | 改 |
| 4 | `coding.py:471` SendSkill data.panel | 运行时 | 改 |
| 5 | `coding.py:518` StopSkill 陈旧 running 兜底 emit | 运行时 | 改 |
| 6 | `coding.py:564` ListSkill data.panel | 运行时 | 改 |
| 7 | `coding.py:570` AttachSkill docstring | 注释 | 改(并 chat.html handleInitData → studio handleData) |
| 8 | `coding.py:991` RewindSkill `_emit` 兜底 | 运行时 | 改 |
| 9 | `coding.py:132/179/596` chat.html 字样注释 | 注释 | 改为「面板/studio handleData」表述 |
| 10 | `plugins/coding/api.toml:5,11,22,29` start/send/list/attach `panel=` | 运行时配置 | 改(4 处) |
| 11 | `api.toml:24,119` 注释 | 注释 | 改 |
| 12 | `plugins/coding/manifest.toml` chat [[panel]] webview 声明 | 运行时注册 | **删除**(studio module 声明保留) |
| 13 | `desktop/src/components/PanelApp.vue:389` isCoding | 运行时 | **泛化**:`panel.startsWith("coding:") && (webviewHtml \|\| webviewUrl)`;coding:wall 是 schema 面板天然排除 |
| 14 | `PanelApp.vue:415` takeover-state focus 上报 `panel:"chat"` 硬编码 | 运行时 | 改取 `current.panel.split(":")[1]`(兜底 "studio") |
| 15 | `desktop/src/components/HomeFeed.vue:392,397` 任务卡路由注释 | 注释 | 改(coding.attach 行为不变,api.toml panel 声明决定开哪个面板) |
| 16 | `sidecar/tests/test_coding_plugin.py:525,1316` 断言 + 884/1309 注释 | 测试 | 改(4 处) |
| 17 | `test_coding_plugin.py:1400` docstring chat.html init 判别 | 测试注释 | 改 |
| 18 | `sidecar/tests/test_codex_runner.py:459` docstring | 测试注释 | 改 |
| 19 | `sidecar/tests/test_plugins_inject.py::test_placeholder_replaced_with_real_vendor_files` | 测试素材依赖真 vendor 三库 | **重写**:自造 fixture(仿真 marked/DOMPurify/hljs 标志名),不再依赖已删目录;改名 `..._with_vendor_files` |
| 20 | `desktop/src/components/InputBar.vue:143,307`、`desktop/src/lib/at-mention.ts:2` chat.html 字样注释 | 注释 | 改指 coding 面板新出处(refs.ts/Composer.vue) |
| 21 | `HomePlugins.vue:387/399` | 排查 | **无需改**:只有 `coding:wall` 字面量(墙刷新守卫),无 coding:chat |
| 22 | `sidecar/src/yibao_brain/server.py`(`_fulfill_coding_perm`/后台任务卡) | 排查 | **无需改**:无任何 `coding:` 面板字面量;任务卡只带 `kind:"coding"`,attach 路由与 panel 名解耦 |
| 23 | `sidecar/src/yibao_brain/loop.py` | 排查 | **无需改**:`result.panel` 通用透传(panel_payload),无字面量 |
| 24 | `plugins/coding/panel/src/**` 「对齐/移植 chat.html:行号」注释 | 谱系注释 | **保留**(记录行为出处;chat.html 留在 git 历史) |
| 25 | `docs/**` 全部命中(plan/specs 历史文档) | 历史文档 | **不改** |

### 删除项

- `plugins/coding/panel/chat.html`(2943 行,git rm)
- `plugins/coding/panel/vendor/`(README + marked/dompurify/highlight 三库,git rm;
  三库现由 panel 工程 npm 依赖承载:marked ^15 / dompurify ^3.2.4 / highlight.js ^11.11.1)
- `plugins.py:_inline_vendor` 机制本身保留(通用 webview 面板基建,test_plugins_inject.py
  其余用例继续覆盖;toolbox/zimeiti webview 面板透传回归不动)

### 旧 ref 残留行为

`coding:chat` 不再有任何面板注册(manifest 声明已删)。若有陈旧 panel 事件到达:
webview/schema 均空 → PanelApp 走 SchemaPanel `schema:null` 未知降级空态,不炸;
isCoding 不命中(无 webviewHtml/webviewUrl)→ 输入条不接管,行为安全。

---

## 3. 闸门结果(全绿)

| 闸门 | 命令 | 结果 |
|------|------|------|
| sidecar 全量 | `cd sidecar && .venv/bin/python -m pytest -q` | **1089 passed**(35.8s) |
| app 测试 | `cd desktop && pnpm test` | **115 passed**(12 文件) |
| app 构建 | `cd desktop && pnpm build` | OK |
| 面板测试 | `cd plugins/coding/panel && pnpm test` | **126 passed**(9 文件;session.test.ts 27→29) |
| 面板构建 | `cd plugins/coding/panel && pnpm build` | OK(dist 经 scripts/panel-build 产出) |
| cargo | `cd desktop/src-tauri && cargo test` | **40 passed, 0 failed** |

注:worktree 的 `sidecar/.venv` 缺 dev extra,补装了 pytest(`uv pip install --python
.venv/bin/python "pytest>=8.0"` → pytest 9.1.1);`sidecar/uv.lock` 在我开工前已是
dirty(625 行重写),非本次改动,未纳入提交。

---

## 4. 真机验收清单(手动,待阶段二终审执行)

### P1/P2/R3 既有验收不回归

- [ ] 对话:coding.start 发起会话 → studio 面板(面板窗)流式回显正常(module 面板 yibao-plugin:// 路径,非旧 srcdoc)
- [ ] 回放:重开面板/attach 旧会话 → 历史回放 + 「以上为历史」marker;自动回放(进面板带上次会话)
- [ ] 引擎:chip 显示/切换;CC↔Codex 交接双路径(chip 待定切换 handoffSend;Codex→CC 交接卡)
- [ ] rewind:用户气泡 ⏪ 回滚 → rewind_ok marker
- [ ] mode pill:plan ⇄ acceptEdits 切换,运行中同步后端
- [ ] @ chips:文件补全/去重/随发送组装进 prompt
- [ ] 截图粘贴:composer 粘贴图片正常
- [ ] **takeover**:面板窗打开 studio → InputBar 接管(「编码智能体接管中」placeholder);
  输入直送 iframe;takeover-state 驱动团子/上报;退出接管输入条交还大脑
- [ ] **takeover 新守卫(本次修复)**:① takeover 中 chip 选另一引擎后从宿主输入条发送 →
  走交接摘要移植(状态行「生成交接摘要…」);② 空 cwd/空文本 → 状态行
  「请先选择项目目录」/「请输入任务描述」;③ busy 时 takeover 输入排队,泄放时若带
  待定切换同样走交接
- [ ] 逃生口:takeover 中点团子 → mini 输入行直问大脑,不打断编码会话
- [ ] 审批 L2:权限请求卡(面板只读镜像)+ 确认体系裁决双通道
- [ ] 后台任务卡:background 会话终态汇报;Feed 任务卡点击 → coding.attach → studio 打开并 resume
- [ ] 会话墙:coding:wall 总览/接管/停止;coding_sessions 事件实时刷新(isCoding 泛化不误伤墙)

### studio 双 surface

- [ ] 面板窗:经 coding.start/list/attach 触发打开,热加载有效(panel/dist 重建后 v mtime 变 → iframe 重载)
- [ ] 大窗:插件页 coding 子入口「多工位(新)」(open="studio" → coding.studio)打开可用

---

## 5. 遗留/跟进建议(不属本任务范围)

- manifest 里 studio 面板 label 仍是「多工位(新)」——切换后它就是唯一的编码面板,
  建议终审时定夺是否改回「编码对话」(用户可见文案,需产品确认)。
- `plugins/coding/panel/src/**` 的「对齐 chat.html:行号」谱系注释指向已删文件,
  行号只能在 git 历史(本提交之前)查阅;保留是有意为之。
- `sidecar/uv.lock` 开工前已 dirty(非本任务改动),未提交;`desktop/src-tauri/target`
  为本地构建产物,未跟踪。
