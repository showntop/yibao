# 译宝 设置补两件：记忆管理增强 + 自主权旋钮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 补齐 OS 感套餐第 ③ 项——记忆管理「可见、可改、可删」三件套中的「可改」，加命名空间筛选与全文展开；新增「自主权旋钮」三档（安静/气泡/完整）控制主动触达强度。

**Architecture:** 记忆编辑沿既有四层链路（`Memory` 抽象 → `mem_edit` IPC → Rust command → `brain.ts` → SettingsView 行内编辑），mem0 原生 `update()` 提供能力。自主权旋钮 = 新 settings 键 `proactive.level`（`quiet`/`bubble`/`full`，默认 `full` 保持现状），sidecar 在两个主动推送点（`_reminder_loop`、`_on_plugin_event`）按档位过滤广播并给事件标注 level，前端按 level 决定是否亮窗；落历史/Feed 不受档位影响，`error`/`confirmation_needed` 事件不受旋钮管辖。

**Tech Stack:** Python 3.12、mem0、pytest、Rust/Tauri v2、Vue 3/TypeScript。

**Spec:** `docs/research/2026-07-27-os-feel-design.md` §4.4（设置 = 系统设置：记忆管理 + 自主权旋钮）、§4.5（通知纪律）。

**摸底结论（2026-07-28）：**

- 记忆管理已有：按命名空间列出（`ns`/`label` 已随 `mem_list` 返回）、单条删除、整体清空、后端状态提示。缺：编辑、命名空间筛选、全文展开。
- `Memory` 抽象（`sidecar/src/yibao_brain/memory.py:21-34`）只有 `add/recall/list_all/delete_by_id`；mem0 的 `delete`/`update` 均按 `memory_id` 操作、无需 user_id，故编辑/删除无需 ns 路由。
- 主动行为仅两类：提醒触发（`server.py:567-603`，10s 轮询）与 agents 任务完成播报（`plugins/agents/skills/_common.py:91-99` 经 `server.py:425-437` `_on_plugin_event` 广播）。现有 `proactive_voice` 只控 TTS 出声，不控触达。
- settings 机制：`config.py:174-180` `_SETTINGS_DEFAULTS`，`settings_set` 即时生效免重启。

---

## File Structure

- Modify `sidecar/src/yibao_brain/memory.py`: `Memory.update` 抽象 + `FakeMemory`/`Mem0Memory` 实现。
- Modify `sidecar/src/yibao_brain/server.py`: `mem_edit` IPC；`_reminder_loop`/`_on_plugin_event` 按 `proactive.level` 过滤与标注。
- Modify `sidecar/src/yibao_brain/config.py`: `proactive.level` 默认值。
- Modify `sidecar/tests/test_memory.py`、`test_server.py`、`test_mem_settings.py`、`test_reminders.py`: 对应覆盖。
- Modify `desktop/src-tauri/src/lib.rs`: `mem_edit` command 与 `brain-mem-edited` 转发。
- Modify `desktop/src/lib/brain.ts`: `editMem()` 一次性接口；`SettingsValues` 加键；reminder 事件 DTO 加 `level`。
- Modify `desktop/src/components/SettingsView.vue`: 记忆行内编辑 + 命名空间筛选 chips + 全文展开；「主动行为」组三档旋钮。
- Modify `desktop/src/App.vue`: reminder case 按 `level` 决定亮窗。
- Modify `docs/research/2026-07-27-os-feel-design.md`: §4.4 实装记录。

## Task 1: sidecar 记忆编辑能力

**Files:**
- Modify: `sidecar/src/yibao_brain/memory.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Test: `sidecar/tests/test_memory.py`
- Test: `sidecar/tests/test_server.py`

- [x] **Step 1: 写 update 的失败测试**

`test_memory.py` 中对 `FakeMemory`：add 一条后 `update(mem_id, "改后")`，断言 `list_all` 中文本已替换、id 不变、不存在旧文本；更新不存在的 id 返回 False。对 `Mem0Memory`：用 monkeypatch 的假 mem0 client 断言调用了底层 update 且签名按 `memory_id` + 新文本（实装时先核对已装 mem0 版本的 `update` 精确签名，以其为准）。

- [x] **Step 2: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_memory.py -q`
Expected: FAIL with `AttributeError: update`。

- [x] **Step 3: 实现 update**

`Memory` 抽象加 `update(self, memory_id: str, text: str) -> bool`；`FakeMemory` 原地替换；`Mem0Memory` 委托 mem0 client（异常回 False 不抛）；`LazyMem0Memory` 就绪前返回 False 或委托转发（与 `delete_by_id` 同款策略）。

- [x] **Step 4: 写 mem_edit IPC 的失败测试**

经 fake `serve` harness 发送 `{"type": "mem_edit", "mem_id": 7, "text": "改后"}`，断言回包 `mem_edited {id, ok}`；空文本/缺 id 回 `ok=False` 与明确 error，异常不炸主循环。

- [x] **Step 5: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_server.py -k mem_edit -q`

- [x] **Step 6: 实现 mem_edit 并全绿**

`server.py` 在 `mem_delete` 旁加 `mem_edit` 分支（复用底座 `memory` 实例，与删除同路径）。

Run: `cd sidecar && uv run --extra dev pytest tests/test_memory.py tests/test_server.py -q`
Expected: all tests pass。

Commit:

```bash
git add sidecar/src/yibao_brain/memory.py sidecar/src/yibao_brain/server.py sidecar/tests/test_memory.py sidecar/tests/test_server.py
git commit -m "feat(memory): 支持编辑单条长期记忆"
```

## Task 2: Rust 与 TypeScript 编辑链路

**Files:**
- Modify: `desktop/src-tauri/src/lib.rs`
- Modify: `desktop/src/lib/brain.ts`

- [x] **Step 1: Rust command 与转发**

sidecar reader 将 `mem_edited` emit 为 `brain-mem-edited`；新增 `mem_edit(id, text)` command 注册进 `invoke_handler`（与 `mem_delete` 同模式，`lib.rs:731-741` 参照）。

- [x] **Step 2: TypeScript 一次性接口**

`editMem(id, text)`：`invoke("mem_edit")` + 监听匹配 id 的 `brain-mem-edited` 回包，5s 超时，完成或超时释放 listener（与 `deleteMem` 同款）。

- [x] **Step 3: 类型与编译验证**

Run: `cd desktop && npx vue-tsc --noEmit && cargo check --manifest-path src-tauri/Cargo.toml`
Expected: exit 0。

Commit:

```bash
git add desktop/src-tauri/src/lib.rs desktop/src/lib/brain.ts
git commit -m "feat(shell): 记忆编辑 IPC 桥"
```

## Task 3: 前端记忆管理增强

**Files:**
- Modify: `desktop/src/components/SettingsView.vue`

- [x] **Step 1: 行内编辑**

记忆行尾加「编辑」mini 按钮 → 文本切换为 textarea（带原始值）+ 保存/取消；保存中禁用，成功原地更新该行，失败提示并还原。复用现有 `confirming`/行内按钮样式（`SettingsView.vue:503-510` 参照）。

- [x] **Step 2: 命名空间筛选 chips**

列表上方 chips 行：「全部 · N」+ 各命名空间「label · n」（按 `memItems` 的 `ns` 分组计数，纯前端 computed）；选中态过滤列表。只有「译宝」一个命名空间时不显示 chips 行。

- [x] **Step 3: 全文展开**

点击记忆文本在两行截断与全文间切换（CSS class 切换 `line-clamp`），长文本行尾给「展开/收起」提示。

- [x] **Step 4: 前端验证**

Run: `cd desktop && npx vue-tsc --noEmit && npm run build`
Expected: exit 0。

Commit:

```bash
git add desktop/src/components/SettingsView.vue
git commit -m "feat(settings): 记忆可编辑、按命名空间筛选、展开全文"
```

## Task 4: sidecar 自主权旋钮

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Test: `sidecar/tests/test_mem_settings.py`
- Test: `sidecar/tests/test_reminders.py`
- Test: `sidecar/tests/test_server.py`

- [x] **Step 1: 写 settings 失败测试**

默认值断言加 `"proactive.level": "full"`；`settings_set` 设为 `quiet` 后 get 生效；非法值（如 `"loud"`）被拒绝或回退默认，未知键仍忽略。

- [x] **Step 2: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_mem_settings.py -q`

- [x] **Step 3: 加默认键与校验**

`_SETTINGS_DEFAULTS` 加 `"proactive.level": "full"`；`settings_set` 对该键只接受 `{"quiet", "bubble", "full"}`。

- [x] **Step 4: 写旋钮行为失败测试**

提醒循环：level=quiet 时到期提醒**不广播** reminder 事件、不 TTS，但仍落历史与 Feed；level=bubble 时广播事件带 `level="bubble"` 且不 TTS；level=full 时带 `level="full"` 且 TTS 仍受 `proactive_voice` 控（现状）。插件事件：`_on_plugin_event` 对播报类事件做同款过滤/标注，Feed 照落。

- [x] **Step 5: 确认 RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_reminders.py tests/test_server.py -q`

- [x] **Step 6: 挂接两个推送点并全绿**

`_reminder_loop`（`server.py:567-603`）与 `_on_plugin_event`（`server.py:425-437`）每轮实时读 `settings.get("proactive.level", "full")`：`quiet` 跳过广播与 TTS；其余档位在广播 payload 加 `level` 字段；TTS 条件收紧为 `level == "full" and proactive_voice`。`error`/`confirmation_needed` 事件路径不动。

Run: `cd sidecar && uv run --extra dev pytest tests/test_reminders.py tests/test_server.py tests/test_mem_settings.py -q`
Expected: all tests pass。

Commit:

```bash
git add sidecar/src/yibao_brain/config.py sidecar/src/yibao_brain/server.py sidecar/tests/test_mem_settings.py sidecar/tests/test_reminders.py sidecar/tests/test_server.py
git commit -m "feat(settings): 自主权三档旋钮挂接主动推送"
```

## Task 5: 前端旋钮 UI 与呈现分级

**Files:**
- Modify: `desktop/src/lib/brain.ts`
- Modify: `desktop/src/components/SettingsView.vue`
- Modify: `desktop/src/App.vue`

- [x] **Step 1: 类型与设置读写**

`SettingsValues` 加 `"proactive.level": "quiet" | "bubble" | "full"`；reminder 事件 DTO 加可选 `level`。

- [x] **Step 2: 设置页「主动行为」组**

三档 segmented：安静（提醒与播报只记入动态，不打扰）/ 气泡（桌宠冒泡轻提示，不亮窗不出声）/ 完整（亮窗 + 气泡，语音播报由下方开关控制）。乐观更新 + 失败回滚（与感知开关同款）。`proactive_voice` 开关保留，非 `full` 档置灰并注明「仅完整档生效」。

- [x] **Step 3: App.vue 按 level 呈现**

reminder case（`App.vue:374-392`）：`e.level === "bubble"` 时只 push 气泡 + `attentionNeeded`，跳过亮窗分支；无 `level` 字段按 `full` 处理（兼容旧 sidecar）。`notice`/`error`/确认闸门不动。

- [x] **Step 4: 前端验证**

Run: `cd desktop && npx vue-tsc --noEmit && npm run build`
Expected: exit 0。

Commit:

```bash
git add desktop/src/lib/brain.ts desktop/src/components/SettingsView.vue desktop/src/App.vue
git commit -m "feat(settings): 主动行为三档旋钮与呈现分级"
```

## Task 6: 全量验证与实装记录

**Files:**
- Modify: `docs/research/2026-07-27-os-feel-design.md`

- [x] **Step 1: sidecar 全量**

Run: `cd sidecar && uv run --extra dev pytest -q`
Expected: all tests pass，记录数量。

- [x] **Step 2: 前端与 Rust 全量**

Run: `cd desktop && npx vue-tsc --noEmit && npm run build && cargo check --manifest-path src-tauri/Cargo.toml && cargo test --manifest-path src-tauri/Cargo.toml`
Expected: exit 0。

- [x] **Step 3: 更新 os-feel-design §4.4 实装记录**

追加日期行：记忆管理三件套齐（可见/可改/可删 + 命名空间筛选 + 展开）；自主权旋钮三档语义与管辖范围（只管触达强度，历史/Feed 照落，error/确认闸门不受管辖）；遗留真机验收项。

- [x] **Step 4: 文档提交**

```bash
git add docs/research/2026-07-27-os-feel-design.md
git commit -m "docs(os-feel): 记录设置两件实装"
```

## 真机验收清单

1. 让译宝记住一个偏好（如「我喜欢美式咖啡」），设置 → 记忆管理里编辑为「拿铁」，再对话确认召回的是改后文本。
2. 命名空间筛选：安装过插件记忆后按 chips 过滤。
3. 设一个 1 分钟后的提醒：full 档亮窗 + 气泡 + 出声；切 bubble 档再设一个：窗不弹、团子标「有事找你」、不出声，点开见气泡；切 quiet 档再设一个：无气泡无亮窗，主屏动态里有记录。
4. agents 委派任务完成播报在三档下的表现同上。
5. quiet 档下触发一个 error（如断网发消息），错误气泡仍正常出现（不受旋钮管辖）。
