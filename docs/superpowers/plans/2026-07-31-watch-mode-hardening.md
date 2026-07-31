# Watch Mode Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make watch mode privacy-safe, immediately controllable, consistently dispatched, cancellable for background jobs, and understandable in Settings.

**Architecture:** Keep pure watch decisions in `watch.py`, move runtime orchestration to `watch_service.py`, proactive delivery to `proactive.py`, and subprocess lifecycle to `background_jobs.py`. `server.py` becomes composition and IPC glue; Vue exposes capability-specific controls rather than a single ambiguous watch switch.

**Tech Stack:** Python 3.12, asyncio/threading/subprocess, pytest, Vue 3 + TypeScript, Tauri 2, Node source-contract tests.

---

### Task 1: Privacy-safe snapshot and visual observation

**Files:**
- Modify: `sidecar/src/yibao_brain/perception.py`
- Modify: `sidecar/src/yibao_brain/watch.py`
- Modify: `sidecar/src/yibao_brain/llm.py`
- Test: `sidecar/tests/test_watch.py`
- Test: `sidecar/tests/test_llm.py`

- [x] **Step 1: Write failing tests**

Add tests that require settings-aware/fresh snapshots, strict observe JSON, app-change triggers, and before/after bundle-id checks:

```python
def test_snapshot_ignores_disabled_and_stale_sources():
    snap = snapshot_from_perception(store, 1000, settings={"perception.master": False})
    assert snap.app is None and snap.activity is None

def test_parse_observe_requires_boolean_and_clamps_text():
    assert parse_observe('{"speak":"false","text":"x"}') is None
    assert len(parse_observe('{"speak":true,"text":"这是一个很长很长很长很长的建议"}')["text"]) <= 20

def test_proactive_capture_discards_when_frontmost_changes(tmp_path):
    frontmost = iter(["com.microsoft.VSCode", "com.apple.mail"]).__next__
    run_proactive_look(host, vision, "com.microsoft.VSCode", emit, frontmost)
    assert vision.calls == []
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_watch.py tests/test_llm.py`

Expected: failures for unsupported settings/freshness arguments, loose parser, old trigger behavior, and missing frontmost recheck.

- [x] **Step 3: Implement minimal safe behavior**

Implement:

```python
def sample_frontmost_bundle_id() -> str | None: ...

def snapshot_from_perception(store, now, *, settings=None, max_age=15.0): ...

def parse_observe(content):
    obj = json.loads(extracted)
    if type(obj.get("speak")) is not bool:
        return None
    text = " ".join(str(obj.get("text", "")).split())[:20]
    return {"speak": obj["speak"] and bool(text), "text": text}
```

Make `ProactiveChat` trigger on `(bundle_id, active_segment)`, and make its worker validate the real frontmost bundle id before and after capture before calling vision.

- [x] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning tests/test_watch.py tests/test_llm.py tests/test_perception.py`

Expected: all pass with no warnings.

### Task 2: Per-action confirmation policy and background jobs

**Files:**
- Create: `sidecar/src/yibao_brain/background_jobs.py`
- Modify: `sidecar/src/yibao_brain/skills.py`
- Modify: `sidecar/src/yibao_brain/invoker.py`
- Modify: `sidecar/src/yibao_brain/skills_real.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Test: `sidecar/tests/test_background_jobs.py`
- Test: `sidecar/tests/test_invoker.py`
- Test: `sidecar/tests/test_real_skills.py`

- [x] **Step 1: Write failing tests**

```python
def test_watch_command_cannot_be_remembered(invoker):
    action = invoker.propose(ToolCall(id="x", skill_id="watch_command", params={"command":"echo x","cwd":"/tmp"}))
    invoker.apply_verdict(action, True, True)
    assert "watch_command" not in invoker.gate.session_allowed

def test_background_job_requires_cwd_and_can_cancel(tmp_path):
    mgr = BackgroundJobManager(emit=events.append)
    job = mgr.start("sleep 30", cwd=str(tmp_path), timeout_sec=60)
    assert mgr.cancel(job["task_id"])
    assert mgr.status(job["task_id"])["status"] == "cancelled"
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_background_jobs.py tests/test_invoker.py tests/test_real_skills.py`

Expected: missing manager/status/cancel skills and remember policy failure.

- [x] **Step 3: Implement manager and thin skills**

Add `Skill.allow_session_remember = True`, set it false on `WatchCommandSkill`, and gate `apply_verdict` through the declaration. Implement `BackgroundJobManager` using `Popen(start_new_session=True)`, a bounded log file/tail, task IDs, status/cancel, TERM→KILL process-group cleanup, and `shutdown()`.

Register:

```python
WatchCommandSkill(manager)
WatchCommandStatusSkill(manager)
WatchCommandCancelSkill(manager)
```

Require `cwd`; bound `timeout_sec` to 1..3600; inject the manager during server composition and call `shutdown()` during cleanup.

- [x] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_background_jobs.py tests/test_invoker.py tests/test_real_skills.py tests/test_server.py`

Expected: all pass.

### Task 3: Unified proactive dispatcher and live WatchService

**Files:**
- Create: `sidecar/src/yibao_brain/proactive.py`
- Create: `sidecar/src/yibao_brain/watch_service.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Test: `sidecar/tests/test_proactive.py`
- Test: `sidecar/tests/test_watch_service.py`
- Test: `sidecar/tests/test_watch.py`

- [x] **Step 1: Write failing tests**

```python
async def test_quiet_dispatch_records_feed_without_shell_event(): ...
async def test_full_dispatch_respects_voice_toggle_and_busy_state(): ...
async def test_watch_service_apply_settings_starts_and_stops_immediately(): ...
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_proactive.py tests/test_watch_service.py tests/test_watch.py`

Expected: missing modules/classes.

- [x] **Step 3: Implement dispatcher and service**

`ProactiveDispatcher.emit(ev)` schedules an async delivery that always writes Feed, gates shell delivery, and speaks only for full + `proactive_voice` + idle run state. `WatchService.apply_settings()` starts/stops/rebuilds a single task immediately; each tick takes a settings-aware snapshot, runs behaviors, and sends every event through the dispatcher.

- [x] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_proactive.py tests/test_watch_service.py tests/test_watch.py tests/test_reminders.py tests/test_server.py`

Expected: all pass.

### Task 4: Settings validation and IPC status

**Files:**
- Modify: `sidecar/src/yibao_brain/config.py`
- Modify: `sidecar/src/yibao_brain/server.py`
- Modify: `sidecar/tests/test_mem_settings.py`
- Modify: `sidecar/tests/test_server.py`

- [x] **Step 1: Write failing tests**

```python
def test_save_settings_rejects_bad_quiet_hours(tmp_path): ...
def test_enabling_health_watch_enables_required_perception(fake_server): ...
def test_settings_response_includes_watch_status(fake_server): ...
```

- [x] **Step 2: Run tests and verify RED**

Run: `.venv/bin/pytest -q tests/test_mem_settings.py tests/test_server.py`

Expected: invalid value accepted, dependencies not linked, status absent.

- [x] **Step 3: Implement validation and live application**

Add `watch.screen_enabled`; validate all watch scalar/list types and quiet hours. On `settings_set`, atomically expand health/screen dependency patches, persist accepted values, call `watch_service.apply_settings(settings)`, and include a `watch.status` object in settings replies.

- [x] **Step 4: Run tests and verify GREEN**

Run: `.venv/bin/pytest -q tests/test_mem_settings.py tests/test_server.py tests/test_watch_service.py`

Expected: all pass.

### Task 5: Product settings and accessibility

**Files:**
- Modify: `app/src/components/SettingsView.vue`
- Modify: `app/src/lib/brain.ts`
- Create: `app/tests/watch-settings-ui.test.mjs`
- Modify: `app/package.json`

- [x] **Step 1: Write failing source-contract test**

Assert the Settings source contains the approved capability labels, disclosure, status, bundle allowlist, validation copy, `role="switch"`, `:aria-checked`, and `aria-pressed`, and no longer contains `观察 / watch` or restart-required watch copy.

- [x] **Step 2: Run test and verify RED**

Run: `node --test tests/watch-settings-ui.test.mjs`

Expected: assertion failure for missing new UI contract.

- [x] **Step 3: Implement the Settings UI**

Replace the old card with “主动协助”: health toggle/settings, screen-advice toggle/disclosure, comma/newline bundle-id allowlist, advanced gap/hour/day inputs, inline status and quiet-hours validation. Co-locate delivery mode controls and add switch/pressed ARIA state. Update `SettingsValues` with typed watch fields/status.

- [x] **Step 4: Run tests and build**

Run: `node --test tests/*.test.mjs && npm run build`

Expected: all Node tests and Vue/TypeScript build pass.

### Task 6: Integration verification

**Files:**
- Modify tests only if a genuine integration gap is discovered.

- [x] **Step 1: Run Python full suite with thread warnings as errors**

Run: `.venv/bin/pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning`

Expected: all tests pass; sandbox-exec tests may require the approved unsandboxed route.

- [x] **Step 2: Run frontend and Rust checks**

Run: `node --test tests/*.test.mjs && npm run build && cargo test --manifest-path src-tauri/Cargo.toml`

Expected: all pass.

- [x] **Step 3: Build current macOS bundle**

Run: `npm run tauri -- build --debug`

Expected: bundle succeeds at `app/src-tauri/target/debug/bundle/macos/译宝.app`.

- [x] **Step 4: Execute the four-step manual acceptance from the design**

Record any OS permission or duplicate-bundle blocker explicitly; do not claim unobserved UI/runtime behavior.
