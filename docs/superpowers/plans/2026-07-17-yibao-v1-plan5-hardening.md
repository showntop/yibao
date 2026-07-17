# 译宝 v1 · Plan 5：加固 — sidecar 守护 + 复合技能 + 权限引导

- **日期**：2026-07-17
- **状态**：已实现（见对应 commit）
- **关联**：`2026-07-16-desktop-agent-design.md` §3（故障隔离）、§6（风险分级）

> 补 v1 四个缺口：①sidecar 无崩溃守护 ④复合技能为零 ⑤权限引导只有文档 ⑥README 过期。打包分发（PyInstaller）、Windows、审计回看 UI 不在本 Plan 范围。

## 1. 复合技能（`skills_composite.py`）

确定性优先：能走 CLI（`open`/`mdfind`）就不点像素，能走 AX 设值就不模拟键入。

| id | 风险 | 实现 |
|---|---|---|
| `find_file` | L0 | `mdfind <query>`，返回前 10 个路径 |
| `web_search` | L1 | `open <引擎 URL>`（默认百度，`YIBAO_SEARCH_ENGINE` 可切 bing/google） |
| `open_path` | L1 | `open <path>`；`reveal=true` → `open -R`（访达定位） |
| `write_note` | L2 | launch TextEdit → AX 找 AXTextArea 设值，回退模拟键入（新建草稿，不落盘） |

经 `register_composite_skills(reg)` 在 `server.build_loop` 的 real_a11y 分支注册。测试：`tests/test_composite.py`（subprocess 走 monkeypatch，AX 走 FakeHost）。

## 2. server 协议扩展（`server.py`）

- 脑→壳新增：`hello`（启动握手，`version` + `permissions`）、`pong`、`permissions`。
- 壳→脑新增：`ping`（看门狗心跳，run 进行中也能即时应答）、`check_permissions`、`prompt_permission`（触发系统授权弹窗）。
- 权限检测复用 `permissions.py`（此前无调用方）；检测失败乐观返回 True，不出误报 banner。

## 3. 壳侧守护（`src-tauri/src/lib.rs`）

- `spawn_brain` 抽取为可复用函数；`BrainState` 持有 child + last_pong + 重启计数 + 退出标记。
- **崩溃重启**：stdout 桥遇 `Terminated`/stdout 关闭 → `on_brain_down` 清槽 → 退避重启（1s→2s→5s→10s 封顶，稳定 60s 清零），永不放弃；每代新进程起新桥任务。
- **看门狗**：每 5s 发 `ping`，>15s 无 `pong` 视为僵死 → kill，走同一重启路径。
- **状态透传**：`hello` → `brain-status:up` + `brain-permissions`；掉线/重启中发 `brain-status:down/restarting`；`write_to_brain` 在掉线窗口返回错误（前端气泡提示）。
- 退出（托盘 quit）先标记 `shutting_down` 并杀 sidecar，避免退出途中被守护拉起。

## 4. 前端（`App.vue` / `PermissionsBanner.vue` / `brain.ts`）

- `brain-status`：down/restarting → 复位状态机 + 气泡「大脑掉线，正在自动重启…」；up → 「大脑已恢复」。
- `brain-permissions` 有缺项 → 自动展开窗 + `PermissionsBanner`：逐项列出辅助功能/屏幕录制，「去授权」（系统弹窗 + 打开对应设置面板 URL scheme）、「重新检测」；全绿后气泡「权限就绪」。

## 验证

- `uv run pytest -q`：86 绿（含 test_composite / test_server 新增）。
- `cargo build` / `npm run build` 通过。
- 手测：`pkill -f yibao_brain.server` → 气泡提示 + 自动恢复；权限 banner 授权/重检流程；三个复合技能各跑一次。

## 遗留（后续 Plan）

- 生产打包：PyInstaller 单可执行 + `bundle.externalBin` + 签名/公证。
- 任务队列持久化：目前重启后只恢复待命，不恢复中断的 run。
- Windows 平台执行基座；审计日志回看 UI。
