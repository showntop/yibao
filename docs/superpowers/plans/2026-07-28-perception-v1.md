# 感知 v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 交付默认关闭、内容加密、即时可控且完全可审计的应用窗口与活跃/空闲感知 v1。

**Architecture:** Python sidecar 新增 `PerceptionStore` 和两个低成本轮询 sensor；sensor 只在设置闸门开启且状态变化时写入。观察内容以 Fernet 字段加密写 SQLite，密钥来自 macOS Keychain；sidecar 通过既有 JSONL IPC 暴露查询/删除/清空，Tauri 只做透明转发，Vue 设置页负责授权、控制和日志展示。

**Tech Stack:** Python 3.12、sqlite3、cryptography/Fernet、PyObjC Cocoa/Quartz/ApplicationServices、pytest、Rust/Tauri v2、Vue 3/TypeScript。

---

## File Structure

- Create `sidecar/src/yibao_brain/perception.py`: 加密 key provider、SQLite store、A/C sensor 采样和后台协调器。
- Create `sidecar/tests/test_perception.py`: store、加密、留存、sensor 去重与设置闸门测试。
- Modify `sidecar/pyproject.toml`: 显式声明 `cryptography`。
- Modify `sidecar/src/yibao_brain/config.py`: 感知 settings 默认值与数据库路径。
- Modify `sidecar/src/yibao_brain/server.py`: 生命周期挂载和三类 IPC 请求。
- Modify `sidecar/tests/test_server.py`: 感知 IPC 往返测试。
- Modify `desktop/src-tauri/src/lib.rs`: 感知消息转发及 Tauri commands。
- Modify `desktop/src/lib/brain.ts`: 感知 DTO 和一次性查询/删除/清空接口。
- Modify `desktop/src/components/SettingsView.vue`: 感知开关、状态、日志、删除与清空 UI。
- Modify `docs/research/2026-07-27-perception-design.md`: 记录最终加密决策和实装状态。

### Task 1: 加密 Observation Store

**Files:**
- Create: `sidecar/tests/test_perception.py`
- Create: `sidecar/src/yibao_brain/perception.py`
- Modify: `sidecar/pyproject.toml`
- Modify: `sidecar/src/yibao_brain/config.py`

- [x] **Step 1: 写 store 的失败测试**

测试构造固定 Fernet key，依次验证 `append()`、倒序 `list()`、`before_id` 分页、坏密文容忍、`delete()`、`clear()`、按来源留存清理以及数据库中不存在应用名/窗口标题明文。测试 API 固定为：

```python
store = PerceptionStore(str(tmp_path / "observations.db"), key=Fernet.generate_key())
oid = store.append("app", "frontmost", {"app": "Xcode", "title": "Secret Project"}, "S1", ts=100.0)
assert store.list(limit=10)[0]["payload"]["title"] == "Secret Project"
assert b"Secret Project" not in (tmp_path / "observations.db").read_bytes()
```

- [x] **Step 2: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q`
Expected: FAIL with `ModuleNotFoundError: yibao_brain.perception`。

- [x] **Step 3: 实现最小 store 和 key provider**

实现 `PerceptionStore(db_path, key=None, key_provider=None)`：`key` 供测试注入；默认 provider 在 macOS 调用 `/usr/bin/security find-generic-password`，不存在时生成 Fernet key 并用 `add-generic-password -U` 写入 service `com.yibao.perception`。非 macOS 且未注入 key 时抛 `PerceptionKeyUnavailable`，不得退化明文。schema 为设计稿 §11.2；`payload` 列保存 ASCII Fernet token。创建目录后 chmod 0700，建库后 chmod 0600。

- [x] **Step 4: 加入明确依赖和配置路径**

在 `sidecar/pyproject.toml` dependencies 加 `cryptography>=43.0`；`config.py` 增加：

```python
def perception_db_path() -> str:
    return os.environ.get("YIBAO_PERCEPTION_DB", os.path.join(data_dir(), "observations.db"))
```

- [x] **Step 5: 确认 GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q`
Expected: all tests pass。

### Task 2: 默认关闭且即时生效的 A/C Sensors

**Files:**
- Modify: `sidecar/tests/test_perception.py`
- Modify: `sidecar/src/yibao_brain/perception.py`
- Modify: `sidecar/src/yibao_brain/config.py`

- [x] **Step 1: 写 sensor 的失败测试**

用可变 settings dict 和 fake sampler 验证：总开关关闭不采；子开关关闭不采；同一 `(app,title)` 不重复；状态变化写一条；idle 以 60 秒为阈值且只写 active/idle 切换；运行中拨开/拨停下一轮立即生效。

- [x] **Step 2: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q`
Expected: FAIL because `PerceptionSensors` is missing。

- [x] **Step 3: 实现采样器和协调器**

`sample_frontmost()` 在 macOS 使用 `NSWorkspace.sharedWorkspace().frontmostApplication()`，标题优先 AX、失败退化 `CGWindowListCopyWindowInfo`；`sample_idle_seconds()` 使用 `CGEventSourceSecondsSinceLastEventType`。`PerceptionSensors.tick()` 每轮读取共享 settings dict，变化才调用 store；`run(stop_event, interval=5)` 负责循环，单次采样异常只记录 stderr。

- [x] **Step 4: 加 settings 默认值**

```python
"perception.master": False,
"perception.app": False,
"perception.activity": False,
```

并保持 `load_settings`/`save_settings` 只接受这些已知键。

- [x] **Step 5: 确认 GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py tests/test_mem_settings.py -q`
Expected: all tests pass。

### Task 3: Sidecar 生命周期与 IPC

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py`
- Modify: `sidecar/tests/test_server.py`

- [x] **Step 1: 写 IPC 失败测试**

通过现有 fake `serve` harness 发送：

```python
{"type": "perception_list", "limit": 20}
{"type": "perception_delete", "per_id": 7}
{"type": "perception_clear"}
```

断言回包分别为 `perception`、`perception_deleted`、`perception_cleared`，且删除请求使用 `per_id` 而非信封 `id`。

- [x] **Step 2: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_server.py -q`
Expected: FAIL because no perception responses are emitted。

- [x] **Step 3: 挂载 store/sensors/cleanup**

`serve_async` 构造 store；密钥不可用时只输出错误并保持 `perception.master=false`。启动 sensor daemon thread 与每小时 purge coroutine；退出时设置 stop event、取消清理任务、关闭 store。测试可通过可选 `perception_store`/`perception_sensors` 参数注入 fake，避免触碰 Keychain。

- [x] **Step 4: 实现三协议方法**

`perception_list` 限制 `limit` 到 1..200 并支持 `before_id`；响应带 `items` 和去重 `sources`。delete/clear 返回真实计数或明确 error，绝不让异常炸掉主循环。

- [x] **Step 5: 确认 GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_server.py tests/test_perception.py -q`
Expected: all tests pass。

### Task 4: Rust 与 TypeScript IPC 桥

**Files:**
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src/lib/brain.ts`

- [x] **Step 1: 增加 Rust 转发与 commands**

sidecar reader 将 `perception`/`perception_deleted`/`perception_cleared` 分别 emit 为 `brain-perception`/`brain-perception-deleted`/`brain-perception-cleared`。新增 `get_perception(limit,before_id)`、`perception_delete(id)`、`perception_clear()` commands 并注册到 `invoke_handler`。

- [x] **Step 2: 增加 TypeScript DTO 与一次性接口**

定义 `PerceptionItem`、`PerceptionResponse`；`getPerceptionOnce()` 采用 `once + 3s timeout`；delete 监听匹配 id 的回包并在 5s 超时；clear 等待 cleared 回包。所有 listener 在完成或超时时释放。

- [x] **Step 3: 类型与 Rust 编译验证**

Run: `cd desktop && npx vue-tsc --noEmit && cargo check --manifest-path src-tauri/Cargo.toml`
Expected: exit 0。

### Task 5: 设置页感知控制与日志

**Files:**
- Modify: `desktop/src/components/SettingsView.vue`

- [x] **Step 1: 接入状态和动作**

onMounted 并行读取 settings 和首屏 50 条观察；总开关关闭时子开关置灰。任何 toggle 先乐观更新、`setSettings` 失败则回滚。列表展示来源、内容、相对时间；单条删除和清空均二次确认。

- [x] **Step 2: 添加两组 UI**

「感知」组固定显示“全部默认关闭、内容加密存本机”；三开关为总开关/应用与窗口/活动与空闲。「感知日志」组显示数量、运行/暂停状态、刷新、分页、逐条删除与清空；空态区分未开启和暂无观察。

- [x] **Step 3: 前端验证**

Run: `cd desktop && npx vue-tsc --noEmit && npm run build`
Expected: exit 0。

### Task 6: 全量验证与实装记录

**Files:**
- Modify: `docs/research/2026-07-27-perception-design.md`

- [x] **Step 1: Python 全量测试**

Run: `cd sidecar && uv run --extra dev pytest -q`
Expected: all tests pass, 0 failures。

- [x] **Step 2: 前端与 Rust 验证**

Run: `cd desktop && npx vue-tsc --noEmit && npm run build && cargo test --manifest-path src-tauri/Cargo.toml`
Expected: all commands exit 0。

- [x] **Step 3: 安全回归检查**

Run: `rg -n 'Secret Project|Xcode' <temporary-test-observations.db>` during the dedicated test, plus inspect file modes in test assertions。
Expected: payload plaintext absent; database mode 0600 and parent mode 0700。

- [x] **Step 4: 更新设计稿实装记录**

在 §11.7 后追加日期、实现文件、测试数量、已验证命令；只记录实际完成项，未做真机 UI/Keychain 验收必须明确列为待验收。

