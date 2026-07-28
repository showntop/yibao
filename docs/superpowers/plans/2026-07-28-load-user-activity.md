# Load User Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `load_user_activity` L0 tool so the LLM can load an explicitly authorized, bounded timeline from encrypted A/C perception observations without leaking decrypted details into audit logs, shell events, conversation history, or long-term memory.

**Architecture:** Extend the shared `Skill` contract with an opt-in safe-result boundary, then keep two representations of sensitive tool output inside `AgentLoop`: full data for the current model round and a safe summary for audit, shell events, and persisted history. Implement the activity loader beside `PerceptionStore`, register it only when that store exists, and gate reads with the live `perception.model_access` setting. Reuse the existing `notice` event after the final reply for the transparency hint.

**Tech Stack:** Python 3.12, sqlite3, Fernet, pytest, OpenAI-compatible tool calling, Vue 3, TypeScript, Tauri v2.

**Design:** `docs/superpowers/specs/2026-07-28-activity-recall-tool-design.md`

---

## File map

- Modify `sidecar/src/yibao_brain/skills.py`: generic sensitive-output contract.
- Modify `sidecar/src/yibao_brain/invoker.py`: audit only a skill's safe result.
- Modify `sidecar/src/yibao_brain/loop.py`: full model result, safe shell/history result, post-reply notice.
- Modify `sidecar/src/yibao_brain/perception.py`: window queries, timeline builder, `LoadUserActivitySkill`.
- Modify `sidecar/src/yibao_brain/config.py`: default-off model access setting.
- Modify `sidecar/src/yibao_brain/server.py`: register the tool against the live perception store/settings.
- Modify `app/src/lib/brain.ts`: type the new setting.
- Modify `app/src/components/SettingsView.vue`: add the explicit outbound-data toggle and copy.
- Modify tests in `sidecar/tests/test_invoker.py`, `test_loop.py`, `test_history.py`, `test_perception.py`, `test_mem_settings.py`, and `test_server.py`.
- Modify `docs/research/2026-07-27-perception-design.md`: record the implemented consumption slice and validation evidence.

### Task 1: Add a generic safe-result boundary to the tool pipeline

**Files:**
- Modify: `sidecar/src/yibao_brain/skills.py`
- Modify: `sidecar/src/yibao_brain/invoker.py`
- Modify: `sidecar/src/yibao_brain/loop.py`
- Test: `sidecar/tests/test_invoker.py`
- Test: `sidecar/tests/test_loop.py`
- Test: `sidecar/tests/test_history.py`

- [ ] **Step 1: Write the failing invoker privacy test**

Add a test-only skill whose runtime result contains a sentinel but whose `safe_result` removes it:

```python
class _SensitiveSkill(Skill):
    id = "sensitive"
    description = "返回敏感数据"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True

    def run(self, params, ctx):
        return ActionResult(success=True, data={"secret": "Window Secret", "count": 1})

    def safe_result(self, result):
        return ActionResult(success=result.success, error=result.error, data={"count": 1})

    def post_reply_notice(self, result):
        return "已参考敏感上下文" if result.success else None
```

Then assert `inv.execute(...)` returns the full sentinel while `inv.log.recent()[0]["data"]` does not contain it.

- [ ] **Step 2: Run the invoker test and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_invoker.py::test_sensitive_skill_audits_only_safe_result -q`

Expected: FAIL because `Skill.safe_result` and `sensitive_output` do not exist and the audit row still stores `Window Secret`.

- [ ] **Step 3: Add the base safe-result API and use it for audit**

Add to `Skill`:

```python
sensitive_output: bool = False

def safe_result(self, result: ActionResult) -> ActionResult:
    return result

def post_reply_notice(self, result: ActionResult) -> str | None:
    return None
```

In `ToolInvoker.execute`, keep returning the full result but pass a safe copy to `_safe_record`:

```python
safe = skill.safe_result(result)
self._safe_record(action, safe)
return result
```

- [ ] **Step 4: Run the invoker test and verify GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_invoker.py -q`

Expected: all invoker tests pass; ordinary `EchoSkill` audit behavior remains unchanged.

- [ ] **Step 5: Write failing loop/history tests for split full and safe outputs**

Register `_SensitiveSkill` in a loop with a two-step recording provider. Assert all of the following in both `run` and `arun` paths:

```python
assert "Window Secret" in json.dumps(provider.second_messages, ensure_ascii=False)
event = next(e for e in events if e.kind == "action_result")
assert event.result.data == {"count": 1}
assert [(e.kind, e.text) for e in events[-2:]] == [
    ("final_reply", "你刚才在 Window Secret"),
    ("notice", "已参考敏感上下文"),
]
disk = (tmp_path / "h.json").read_text()
assert "Window Secret" not in disk
assert "本轮使用敏感工具回答，敏感内容未写入会话历史" in disk
```

Also retain the existing non-sensitive full-trace assertion for `EchoSkill`.

- [ ] **Step 6: Run the loop/history tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_loop.py tests/test_history.py -q`

Expected: the new assertions fail because events/history currently reuse the full result and no post-reply notice is emitted.

- [ ] **Step 7: Implement safe shell/history output in both loop paths**

At the start of each `run`/`arun`, maintain:

```python
safe_tool_content: dict[str, str] = {}
sensitive_turn = False
post_reply_notices: list[str] = []
```

After tool execution:

```python
skill = self.skills.get(action.skill_id)
safe = skill.safe_result(result)
yield Event(kind="action_result", action=action, result=safe)
messages.append({"role": "tool", "tool_call_id": tc.id, "content": _stringify_result(result)})
safe_tool_content[tc.id] = _stringify_result(safe)
if skill.sensitive_output and result.success:
    sensitive_turn = True
notice = skill.post_reply_notice(result)
if notice and notice not in post_reply_notices:
    post_reply_notices.append(notice)
```

Add a pure helper that clones the completed turn, replaces matching tool contents, and redacts the final assistant reply only for sensitive turns:

```python
def _history_safe_span(span: list[dict], safe_tool_content: dict[str, str], sensitive: bool) -> list[dict]:
    out: list[dict] = []
    for message in span:
        item = dict(message)
        if item.get("role") == "tool" and item.get("tool_call_id") in safe_tool_content:
            item["content"] = safe_tool_content[item["tool_call_id"]]
        out.append(item)
    if sensitive and out and out[-1].get("role") == "assistant":
        out[-1]["content"] = "【本轮使用敏感工具回答，敏感内容未写入会话历史】"
    return out
```

Use this helper immediately before `history.record_messages`. Yield accumulated notices immediately after `final_reply`. Keep full tool data in the in-memory `messages` sent to the next model step.

- [ ] **Step 8: Run focused tests and commit**

Run: `cd sidecar && uv run --extra dev pytest tests/test_invoker.py tests/test_loop.py tests/test_history.py -q`

Expected: all focused tests pass.

Commit:

```bash
git add sidecar/src/yibao_brain/skills.py sidecar/src/yibao_brain/invoker.py sidecar/src/yibao_brain/loop.py sidecar/tests/test_invoker.py sidecar/tests/test_loop.py sidecar/tests/test_history.py
git commit -m "feat(agent): 隔离敏感工具结果持久化"
```

### Task 2: Build the encrypted activity window and `load_user_activity` tool

**Files:**
- Modify: `sidecar/src/yibao_brain/perception.py`
- Test: `sidecar/tests/test_perception.py`

- [ ] **Step 1: Write failing store-window tests**

Append app/activity observations before, inside, and after a `[100, 200]` window. Assert:

```python
rows = store.query_window(100, 200)
assert [row["ts"] for row in rows] == [100.0, 150.0, 200.0]
assert store.latest_before("app", 100)["payload"]["app"] == "Seed App"
```

Insert corrupt ciphertext inside the window and assert it is returned with `payload == {}` so the tool can count and skip it without aborting the rest of the window.

- [ ] **Step 2: Run the store tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -k 'window or latest_before' -q`

Expected: FAIL because the two query methods do not exist.

- [ ] **Step 3: Implement bounded time queries**

Add a shared row decoder and these methods:

```python
def query_window(self, start_ts: float, end_ts: float, limit: int = 2000) -> list[dict]:
    sql = (
        "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
        "WHERE ts >= ? AND ts <= ? AND source IN ('app', 'activity') "
        "ORDER BY ts ASC, id ASC LIMIT ?"
    )
    with self._lock:
        rows = self._conn.execute(sql, (start_ts, end_ts, max(1, min(limit, 2001)))).fetchall()
    return [self._decode_row(row) for row in rows]

def latest_before(self, source: str, ts: float) -> dict | None:
    with self._lock:
        row = self._conn.execute(
            "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
            "WHERE source = ? AND ts < ? ORDER BY ts DESC, id DESC LIMIT 1",
            (source, ts),
        ).fetchone()
    if row is None:
        return None
    item = self._decode_row(row)
    return item if item["payload"] else None
```

Keep `list()` behavior compatible: corrupt rows still render with `{}` in the transparent log instead of disappearing.

- [ ] **Step 4: Run the store tests and verify GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -k 'store or window or latest_before' -q`

Expected: all selected tests pass.

- [ ] **Step 5: Write failing pure timeline-builder tests**

Cover seeded state, app switches, active/idle switches, duplicate-state merging, missing app state, and truncation:

```python
segments, truncated = build_activity_segments(
    rows=[
        {"ts": 120.0, "source": "app", "kind": "frontmost", "payload": {"app": "Terminal", "title": "yibao"}},
        {"ts": 150.0, "source": "activity", "kind": "idle", "payload": {"idle_seconds": 60}},
    ],
    seeds=[
        {"source": "app", "kind": "frontmost", "payload": {"app": "Chrome", "title": "Docs"}},
        {"source": "activity", "kind": "active", "payload": {"idle_seconds": 0}},
    ],
    start_ts=100.0,
    end_ts=200.0,
)
assert segments == [
    {"start_ts": 100.0, "end_ts": 120.0, "app": "Chrome", "title": "Docs", "activity": "active"},
    {"start_ts": 120.0, "end_ts": 150.0, "app": "Terminal", "title": "yibao", "activity": "active"},
    {"start_ts": 150.0, "end_ts": 200.0, "app": "Terminal", "title": "yibao", "activity": "idle"},
]
assert truncated is False
```

- [ ] **Step 6: Run builder tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -k activity_segments -q`

Expected: FAIL because `build_activity_segments` does not exist.

- [ ] **Step 7: Implement the pure timeline builder**

Walk seeds first to establish state at `start_ts`; then walk rows in timestamp order. Before applying a changed event, emit `[cursor, event.ts]` with the previous known state. Apply the change, move the cursor, and emit the final `[cursor, end_ts]`. Omit unknown keys, skip zero-length segments, merge adjacent identical states, and return only the newest 120 segments with `truncated=True` when necessary.

- [ ] **Step 8: Run builder tests and verify GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -k activity_segments -q`

Expected: all builder tests pass.

- [ ] **Step 9: Write failing tool contract, authorization, and validation tests**

Construct `LoadUserActivitySkill(store, settings, now_provider=...)` and assert:

```python
assert skill.id == "load_user_activity"
assert skill.default_risk == RiskLevel.L0_READONLY
assert skill.precheck(valid_params) == "模型读取感知记录未开启，请先在设置的感知区域开启"
settings["perception.model_access"] = True
result = skill.run(valid_params, SkillContext())
assert result.success is True
assert result.data["segments"][0]["app"] == "Chrome"
assert skill.safe_result(result).data == {
    "window": result.data["window"],
    "observation_count": result.data["observation_count"],
    "segment_count": len(result.data["segments"]),
    "truncated": result.data["truncated"],
}
assert skill.post_reply_notice(result) == "已参考最近活动"
```

Separately assert naive datetimes, reversed intervals, more than five future minutes, and windows over 24 hours return `success=False` with corrective errors. Empty segments must produce no notice.

- [ ] **Step 10: Run tool tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -k load_user_activity -q`

Expected: FAIL because `LoadUserActivitySkill` does not exist.

- [ ] **Step 11: Implement `LoadUserActivitySkill` minimally**

Use this public contract:

```python
class LoadUserActivitySkill(Skill):
    id = "load_user_activity"
    label = "加载活动记录"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True

    def __init__(self, store: PerceptionStore, settings: dict, now_provider=None):
        self.store = store
        self.settings = settings
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())

    def precheck(self, params: dict) -> str | None:
        if not self.settings.get("perception.model_access", False):
            return "模型读取感知记录未开启，请先在设置的感知区域开启"
        return None
```

Its schema requires timezone-aware `start_at`/`end_at` and includes the “刚才/最近/今天” routing guidance. `run` validates the interval, queries both seeds and window rows, calls `build_activity_segments`, formats timestamps in the requested local timezone, and returns `ActionResult`. `safe_result` includes only window/count/truncation metadata. `post_reply_notice` returns the hint only for a successful non-empty `segments` list.

- [ ] **Step 12: Run all perception tests and commit**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py -q`

Expected: all perception tests pass.

Commit:

```bash
git add sidecar/src/yibao_brain/perception.py sidecar/tests/test_perception.py
git commit -m "feat(perception): 增加用户活动加载工具"
```

### Task 3: Wire live settings and server registration

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Modify: `sidecar/tests/test_mem_settings.py`
- Modify: `sidecar/tests/test_server.py`

- [ ] **Step 1: Write failing settings tests**

Extend default and persistence assertions with:

```python
"perception.model_access": False,
```

Then set it to true through `settings_set` and assert the following `settings_get` returns true while an unknown key is still ignored.

- [ ] **Step 2: Run settings tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_mem_settings.py -q`

Expected: FAIL because the key is not in `_SETTINGS_DEFAULTS`.

- [ ] **Step 3: Add the default-off known setting**

Add to `_SETTINGS_DEFAULTS`:

```python
"perception.model_access": False,
```

- [ ] **Step 4: Run settings tests and verify GREEN**

Run: `cd sidecar && uv run --extra dev pytest tests/test_mem_settings.py -q`

Expected: all settings tests pass.

- [ ] **Step 5: Write failing server-registration tests**

Run `serve_async` with a fake perception store and a recording `FakeProvider`, send one `run`, and assert the advertised tools include `load_user_activity`. Run without a store and assert it is absent. Also assert toggling `perception.model_access` updates the same settings dict observed by the registered skill without restarting the server.

- [ ] **Step 6: Run server tests and verify RED**

Run: `cd sidecar && uv run --extra dev pytest tests/test_server.py -k 'activity_tool or model_access' -q`

Expected: FAIL because the tool is not registered.

- [ ] **Step 7: Register the tool after perception store initialization**

Immediately after `pstore` is resolved and before the sensor thread starts:

```python
if pstore is not None:
    from .perception import LoadUserActivitySkill

    agent.skills.register(LoadUserActivitySkill(pstore, settings))
```

Do not create a second store. Keep the shared mutable `settings` dictionary so `settings_set` is immediately visible to `precheck`.

- [ ] **Step 8: Run focused server/settings tests and commit**

Run: `cd sidecar && uv run --extra dev pytest tests/test_server.py tests/test_mem_settings.py -q`

Expected: all focused tests pass.

Commit:

```bash
git add sidecar/src/yibao_brain/config.py sidecar/src/yibao_brain/server.py sidecar/tests/test_mem_settings.py sidecar/tests/test_server.py
git commit -m "feat(perception): 接入模型活动访问授权"
```

### Task 4: Add the explicit model-access control to Settings

**Files:**
- Modify: `app/src/lib/brain.ts`
- Modify: `app/src/components/SettingsView.vue`

- [ ] **Step 1: Extend the TypeScript settings contract**

Add:

```typescript
"perception.model_access": boolean;
```

to `SettingsValues`.

- [ ] **Step 2: Add live component state and rollback behavior**

Create `perceptionModelAccess = ref(false)`, populate it in `syncPerceptionSettings`, include it in the old-value snapshot, and extend the accepted key union:

```typescript
key:
  | "perception.master"
  | "perception.app"
  | "perception.activity"
  | "perception.model_access",
```

Set and roll back `perceptionModelAccess` with the same optimistic-update behavior as the other perception switches.

- [ ] **Step 3: Add the settings row and disclosure copy**

Place this row after “活动与空闲”; do not disable it when capture is paused because retained history remains queryable:

```vue
<div class="s-row">
  <span class="s-row-label">
    允许模型读取感知记录
    <span class="s-row-why">询问最近活动时，将所选时间段的应用名、窗口标题和活动状态发送给当前模型；不发送截图或按键内容</span>
  </span>
  <button
    class="switch"
    :class="{ on: perceptionModelAccess }"
    title="允许模型读取感知记录"
    @click="setPerceptionSetting('perception.model_access', !perceptionModelAccess)"
  ><i /></button>
</div>
```

Change the opening note to distinguish encrypted local storage from this optional outbound path.

- [ ] **Step 4: Run frontend verification and commit**

Run: `cd app && npx vue-tsc --noEmit`

Run: `cd app && npm run build`

Expected: both commands exit 0.

Commit:

```bash
git add app/src/lib/brain.ts app/src/components/SettingsView.vue
git commit -m "feat(settings): 增加感知模型访问开关"
```

### Task 5: Full verification, privacy regression, and documentation

**Files:**
- Modify: `docs/research/2026-07-27-perception-design.md`

- [ ] **Step 1: Run the focused privacy and tool suite**

Run: `cd sidecar && uv run --extra dev pytest tests/test_perception.py tests/test_invoker.py tests/test_loop.py tests/test_history.py tests/test_mem_settings.py tests/test_server.py -q`

Expected: all selected tests pass with no warning or error output.

- [ ] **Step 2: Run the complete sidecar suite**

Run: `cd sidecar && uv run --extra dev pytest -q`

Expected: all tests pass; record the exact count in the research doc.

- [ ] **Step 3: Run all desktop static/build checks**

Run: `cd app && npx vue-tsc --noEmit`

Run: `cd app && npm run build`

Run: `cd app && cargo check --manifest-path src-tauri/Cargo.toml`

Run: `cd app && cargo test --manifest-path src-tauri/Cargo.toml`

Expected: every command exits 0.

- [ ] **Step 4: Scan persisted fixtures for the sentinel**

Run the privacy tests with sentinel `Window Secret`, then inspect their generated `audit.db` and `history.json` through the test assertions. The model recorder must contain the sentinel while both persisted stores and the public `action_result` must not.

- [ ] **Step 5: Update the perception design implementation record**

Record:

- `load_user_activity` is a default-visible L0 base tool with an explicit default-off `perception.model_access` outbound gate.
- Full decrypted segments exist only in the current model round; audit, shell events, and history receive safe summaries.
- Successful non-empty use emits “已参考最近活动”.
- Exact Python test count and successful TypeScript/Vite/Rust commands.
- Manual macOS acceptance remains required for actual GLM selection, settings copy, timeline correctness, and plaintext scans against the user's real data directory.

- [ ] **Step 6: Check documentation and commit**

Run: `rg -n "recall_activity|load_activity_context|load_user_activities" sidecar app docs/research`

Expected: no stale tool names.

Run: `git diff --check`

Expected: exit 0.

Commit:

```bash
git add docs/research/2026-07-27-perception-design.md
git commit -m "docs(perception): 记录活动上下文闭环"
```

## Manual acceptance after implementation

1. With `perception.model_access` off, ask “我刚才在干嘛”; the tool is blocked before decryption and the reply directs the user to Settings.
2. Enable the switch, change among three apps, wait for sampling, and ask again; the process line says “加载活动记录”.
3. Verify the response order and titles, followed by “已参考最近活动”.
4. Pause capture without deleting records and repeat; retained records remain queryable.
5. Search the real `audit.db` and `history.json` for a unique test window title and verify it is absent.
