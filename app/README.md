# 译宝 · 桌面壳（Tauri v2 + Vue 3）

译宝的桌面外壳：全局快捷键唤起、置顶透明形象窗、文字输入，经 stdio 行分隔 JSON 桥接 Python 大脑 sidecar（`../sidecar`），渲染对话与形象状态，高风险（L3+）操作弹窗确认。

- 架构与设计：`../docs/superpowers/specs/2026-07-16-desktop-agent-design.md`
- IPC 协议与本计划：`../docs/superpowers/plans/2026-07-16-yibao-v1-plan2-shell-and-ipc.md`

## 目录约定

- 大脑 sidecar 在仓库根 `../sidecar`（相对 `app/src-tauri`）。dev 期由 Rust 拉起 `sidecar/.venv/bin/python -u -m yibao_brain.server`。
- 前端在 `src/`（Vue3 + TS，Vite）；Rust 在 `src-tauri/`。

## 开发

前置：Node、Rust（`cargo`）、Tauri v2 CLI（随依赖安装）、以及 sidecar 的 `.venv`（在 `../sidecar` 执行 `uv sync --extra dev`）。

```bash
npm install
npm run tauri dev
```

启动后：
- vite 在 `http://localhost:1420`，Rust app 自动拉起 sidecar。
- 按 `Super+Shift+Y`（macOS 即 `Cmd+Shift+Y`）显隐主窗。
- 输入文字 → 触发大脑 `run` → 事件流回显；高风险技能（L3+）弹确认框。

> 若 sidecar 未拉起：确认 `../sidecar/.venv` 存在；或设 `YIBAO_SIDECAR_DIR` 指向 sidecar 绝对路径；无 `.venv` 时回退 `uv run`（依赖 PATH 中有 `uv`）。

## 构建

```bash
npm run tauri build              # release 打包
npm run tauri -- build --debug   # debug 打包（较快）
```

产物在 `src-tauri/target/release/bundle/`（macOS 为 `.app` / `.dmg`）。bundle 标识 `com.dennyxiao.yibao`。

### ⚠️ sidecar 打包（生产，尚未实现）

当前打包出的 app **不含** Python sidecar，启动后大脑拉起会失败。生产分发需：
1. 用 PyInstaller 把 `sidecar` 打成单可执行（含 Python 解释器与依赖）。
2. 在 `tauri.conf.json` 配 `bundle.externalBin` 指向该二进制（按 target-triple 命名）。
3. Rust 侧改用 `app.shell().sidecar("yibao-brain")` 拉起，取代 dev 期的 `.venv/bin/python -m ...`。

见设计文档第 3 节「故障隔离」与 Plan 2 Task B4。

## 平台踩坑

### macOS
- 透明窗使用 `macOSPrivateApi: true`（`tauri.conf.json`）→ **不能上架 Mac App Store**；需开发者证书签名 + 公证后站外分发。
- 首次运行需在「系统设置 → 隐私与安全性」授予：
  - **辅助功能**（Accessibility，读控件树）
  - **屏幕录制**（Screen Recording，截图感知）
  - **输入监控**（Input Monitoring，全局热键/键盘注入需要）
- 无边框透明窗 `decorations:false`；窗口靠 `set_focus` 接收键盘。

### Windows
- 透明窗偶现黑/灰底：在窗口配置加 `"background_color": "#00000000"`，或对 webview 关 GPU 加速后调优。
- webview 首帧白闪：窗口先 `visible:false`，加载完成再 `show`（已如此配置）。

### 全局热键
- `Super+Shift+Y`：macOS 上 `Super`=`Cmd`，Windows 上为 `Win`。若与系统/输入法冲突，改 `src-tauri/src/lib.rs` 中 `register(...)` 的字符串。

## 已知限制（v1 / Plan 2 范围）
- 一次只处理一个 `run`（单对话）；并发对话需改 server。
- 形象为状态驱动 emoji 占位；Live2D 留待后续 Plan。
- 暂无 STT/TTS（后续 Plan）。
- sidecar 打包未接入（见上）。
