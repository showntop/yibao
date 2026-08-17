# @ 文件引用 chips（coding 面板 + 译宝主输入框）

日期：2026-08-17 ｜ 分支：feat/at-ref-chips ｜ 来源：用户验收反馈（对标 Cursor 输入框 chips 交互）

## 目标

@ 引用文件从「插入纯文本」升级为「输入框内 chips」（可删除、可视化），两个面：

1. **coding 面板自有输入框**（chat.html，非 takeover）：@ 选中 → chip 进输入框内 chips 行；
   发送时组装进 prompt（`@path` 列表）。
2. **译宝主输入框**（InputBar.vue，全场景）：输入 @ 触发文件搜索浮层，选中 → file chip
   进现有 pendingContexts；takeover（coding 接管）时 chips 随文本转发 iframe 组装进 prompt。

## 关键决策（已和用户确认）

- @ 搜索根目录：上一次 @ 引用的目录（sticky 记忆）；没有则最近 coding 会话 cwd；都没有 → 空态提示。
- 跨引擎接续已在上轮落地（20ef7f3），本特性不动引擎逻辑。
- InputBar.vue / HomeChat.vue / PanelApp.vue 在 main 工作区被 quick-dock 并行会话占用（未提交 WIP），
  本特性走 worktree 隔离，合并时 git 正常解决冲突。

## 实现点

### A. chat.html（plugins/coding/panel/chat.html）
- `atRefs` 状态（rel path 数组，去重）；chips 行渲染在 textarea 上方（同一输入容器内），× 删除。
- 内联 @ 菜单 + @ 按钮浮层选中 → `addAtRef(rel)` 替代插文本（非 takeover）。
- takeover 态 @ 按钮维持 insert-draft（文本注入 InputBar 草稿）——chips 由 InputBar 侧自己管。
- send()：prompt = userText + "\n\n引用文件：\n@p1\n@p2"；发送后清 atRefs；newChat/resumeSession 清。
- onHostMessage takeover-input 消息增加可选 refs 数组 → 同样组装进 prompt。

### B. InputBar.vue（app/src/components/InputBar.vue）
- `InputContext` 增加 kind "file"（label=文件名，path=相对路径）。
- textarea @ 触发内联浮层：纯函数抽 `app/src/lib/at-mention.ts`（解析 @ 起锚点/query、
  组装 refs 后缀），vitest 覆盖。
- 文件搜索：`panelAction("coding.files", {cwd, q}, rid)` + onBrainEvent 关联 `pa_<rid>`
  （WebviewPanel 既有模式）；cwd = sticky（localStorage `yibao.atRoot`）→ 缺省最近 coding cwd
  （`coding.list` rows[0].cwd）→ 空态提示。选中后 sticky 更新。
- 浮层键盘：↑↓/Enter/Esc；IME 守卫沿用现有。

### C. 消费侧
- HomeChat.submit：contexts 前缀加 file 分支（`【文件：path】`）。
- PanelApp.submit（takeover）：file contexts 的 path 数组随 takeover-input 转发 iframe。

### D. 后端
- 无改动（coding.files / coding.list 既有）。

## 验证

- `cd app && pnpm install && ./node_modules/.bin/vitest run`（含新增 at-mention 测试）
- `./node_modules/.bin/vue-tsc --noEmit`
- chat.html：python3 提取 script 块 + node --check
- `cd sidecar && uv run pytest -q`（确认零回归）

## 验收路径（用户）

1. coding 面板（插件页内输入框）：@ 选中文件 → chip 出现在输入框；× 可删；发送后引用进 prompt。
2. 主窗/面板工作台 InputBar：输入 @ → 浮层搜文件 → 选中成 chip；发送带【文件：path】。
3. coding  takeover：InputBar 里 @ chips + 文本 → 编码会话 prompt 含 @refs。
