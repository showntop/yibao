# Coding 文件夹选择器 + 两路分工 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** ①coding 面板 cwd 药丸点击 → 原生 macOS 文件夹选择器回填；②系统提示 steer：交互式 coding 引导去「编码」面板，`agents.dispatch` 仅后台任务。

**Architecture:** folder picker——加 Tauri `dialog` 插件 + Rust `pick_folder` 命令 + WebviewPanel `native:` 旁路（白名单 pick_folder，不经 sidecar 直调 Tauri）+ chat.html 药丸点击 `window.yibao.invoke("native:pick_folder")`。division——loop.py `SYSTEM_PROMPT` 加 steer 一段。

**Tech Stack:** Rust + Tauri v2 (dialog plugin), Vue/TS (WebviewPanel bridge), HTML (chat.html), Python (system prompt)。

## Global Constraints

- **webview iframe 无 Tauri IPC**：原生对话框必须 Rust 侧开；iframe 经 `window.yibao.invoke("native:pick_folder")` → WebviewPanel 旁路 → Tauri `pick_folder`。`native:` 前缀白名单只放 `pick_folder`（安全，不放开任意原生命令）。
- **保留手动粘贴**：cwd 药丸仍可手输路径；picker 是补充（点击药丸图标弹选择器）。
- **分工用 A（steer，不删能力）**：`agents.dispatch` 保留后台用法；系统提示让译宝把"交互式 coding"引导去面板。
- **vue-tsc/vite build exit 0；cargo check exit 0；sidecar pytest 全绿**（基线 821）。
- **commit**：每任务一 commit，中文 scope，仅 stage 本任务文件，不动 `.gitignore`，提交 main。

## File Structure
**改：**
- `desktop/src-tauri/Cargo.toml`（加 tauri-plugin-dialog）、`desktop/package.json`（@tauri-apps/plugin-dialog）、`desktop/src-tauri/src/lib.rs`（注册插件 + `pick_folder` 命令）、`desktop/src-tauri/capabilities/*.json`（dialog:allow-open 权限）。
- `desktop/src/components/WebviewPanel.vue`（`native:` 旁路）。
- `plugins/coding/panel/chat.html`（药丸点击 → picker）。
- `sidecar/src/yibao_brain/loop.py`（SYSTEM_PROMPT steer）。

---

### Task FP1: Tauri dialog 插件 + pick_folder 命令

**Files:** `desktop/src-tauri/Cargo.toml`、`desktop/package.json`、`desktop/src-tauri/src/lib.rs`、`desktop/src-tauri/capabilities/*.json`

- [ ] **Step 1: 装插件** —— `cd desktop && npm install @tauri-apps/plugin-dialog` + `cd desktop/src-tauri && cargo add tauri-plugin-dialog`。
- [ ] **Step 2: 注册 + 命令（lib.rs）** —— 在 tauri builder `.plugin(tauri_plugin_dialog::init())`；加命令：
```rust
#[tauri::command]
fn pick_folder(app: tauri::AppHandle) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let folder = app.dialog().file()
        .set_title("选择项目文件夹")
        .blocking_pick_folder();
    Ok(folder.and_then(|p| p.into_path().ok()).map(|p| p.to_string_lossy().into_owned()))
}
```
  注册 `pick_folder` 进 `generate_handler!`。
- [ ] **Step 3: 权限** —— 找到 `desktop/src-tauri/capabilities/` 下的能力文件（如 `main.json`/`home.json`），加 `"dialog:allow-open"` 到 permissions（否则运行时拒）。先 `ls desktop/src-tauri/capabilities/` 看结构。
- [ ] **Step 4: 验证** —— `cd desktop && cargo check --manifest-path src-tauri/Cargo.toml` exit 0。
- [ ] **Step 5: commit** —— `feat(coding): Tauri dialog 插件 + pick_folder 命令`

---

### Task FP2: WebviewPanel native: 旁路

**Files:** `desktop/src/components/WebviewPanel.vue`（onMessage，~L98-120）

- [ ] **Step 1: 加 native 旁路** —— 在 `onMessage` 里、命名空间校验**之前**，加：
```typescript
const NATIVE = new Set(["native:pick_folder"]);
if (typeof method === "string" && NATIVE.has(method)) {
  const cmd = method.slice("native:".length);   // "pick_folder"
  invoke(cmd, (d.params as Record<string, unknown>) ?? {})   // from @tauri-apps/api/core
    .then((r) => replyToIframe({ id: bid, ok: true, result: r }))
    .catch((err) => replyToIframe({ id: bid, ok: false, error: String(err) }));
  return;
}
```
（`invoke` 从 `@tauri-apps/api/core` import；白名单 Set 只放 `native:pick_folder`。）
- [ ] **Step 2: 验证** —— `cd desktop && npx vue-tsc --noEmit && npx vite build` exit 0。
- [ ] **Step 3: commit** —— `feat(panel): WebviewPanel native: 命令旁路（白名单 pick_folder）`

---

### Task FP3: chat.html 药丸点击 → 文件夹选择器

**Files:** `plugins/coding/panel/chat.html`

- [ ] **Step 1: 药丸加可点击图标/按钮** —— cwd 药丸旁加一个小文件夹按钮（或在药丸 click），点击 → `window.yibao.invoke("native:pick_folder")` → 回的 `result` 是路径（或 null 取消）→ 非空则 `$("cwd").value = path`。保留药丸本身可手输。示例：
```js
async function pickCwd() {
  try {
    var r = await window.yibao.invoke("native:pick_folder", {});
    if (r && r.result) { $("cwd").value = r.result; }
  } catch (e) { setStatus("选择文件夹失败", true); }
}
```
  绑到药丸上的一个 📁 按钮（`#cwd-pick`）。
- [ ] **Step 2: build** —— `cd desktop && npx vite build` exit 0。
- [ ] **Step 3: commit** —— `feat(coding): cwd 药丸加文件夹选择器（native:pick_folder）`

---

### Task DIV1: 系统提示两路分工 steer

**Files:** `sidecar/src/yibao_brain/loop.py`（SYSTEM_PROMPT，L23）

- [ ] **Step 1: 加 steer 段** —— SYSTEM_PROMPT 末尾加：
```python
"\n\n【coding 分工】用户要做的若是交互式 coding（写功能/修 bug/重构——需要来回对话、看文件改动），"
"引导用户去「编码」面板（插件页 → 编码，选项目后跟 Claude Code 多轮）。"
"`agents.dispatch_task` 仅用于后台 fire-and-forget 长任务（跑完报告，不交互）。"
```
- [ ] **Step 2: 测试** —— 追加一个测：`SYSTEM_PROMPT` 含"编码面板"+"dispatch_task"关键词（断言 steer 在位）。
- [ ] **Step 3: 全量回归** —— 821+1 passed。
- [ ] **Step 4: commit** —— `feat(coding): 系统提示 steer——交互式 coding 引导面板、dispatch 仅后台`

---

### Task V: 验收
- [ ] **Step 1: 自动化** —— sidecar 全量 + vue-tsc/vite + cargo check 全绿。
- [ ] **Step 2: 真机（人工）** —— 重启 tauri dev → 编码面板 → 点 cwd 药丸的 📁 → 弹原生选择器 → 选目录回填 → 能用；主对话说"帮我写个 X"→ 译宝引导去编码面板（steer 生效）。

---

## 自审
- folder picker: dialog 插件+命令(FP1)+桥旁路(FP2)+药丸(FP3) ✅；native 白名单只 pick_folder（安全）✅；保留手输 ✅
- division: SYSTEM_PROMPT steer(DIV1)，不删 agents.dispatch 能力 ✅
- 类型一致：`native:pick_folder` / `pick_folder` / `pickCwd` 跨任务一致 ✅；无占位 ✅
