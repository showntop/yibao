# C 反应式宠物最小版 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 团子对三个确定性信号做「生命反应」：任务完成欢呼/失败叹气、深夜劝睡打哈欠、久坐做伸展操（新 one-off 动画）。

**Architecture:** sidecar 只加一个 watch 行为（LateNightNudge，注册进 build_behaviors）；三个反应的「表现层」全在壳——`flashValence` 泛化为 `flashState(state, ms)`，reminder case 按 type/task.status 插一次性闪现；唯一新美术是 Avatar.vue 的 stretch 态（弯眼笑+双臂上举+squash-stretch keyframes）。

**Tech Stack:** Python（watch.py/pytest）、Vue3/TS（App.vue/Avatar.vue/tokens.css）。

**关联 spec：** `docs/superpowers/specs/2026-08-08-reactive-pet-design.md`

## Global Constraints

- **全确定性信号**：不引入视觉模型/新传感器；深夜判定复用 `in_quiet_hours`（watch.py:45-59）。
- **提醒纪律不动**：dispatcher 三档闸门/👎降频/Feed 落账/大小窗互斥一字不改；late_night 带 `type` 自动入降频闸。
- **reduced-motion 底线**：新动画必须进 Avatar.vue 的 prefers-reduced-motion 停用列表。
- **sidecar pytest 全绿（基线 858）；vue-tsc/vite build exit 0；不改 Rust**。
- **commit**：每任务一 commit，中文 scope（`feat(reactive): …`），仅 stage 本任务文件，不动 `.gitignore`，提交在 `feat/reactive-pet` 分支。

## File Structure

**改：**
- `sidecar/src/yibao_brain/watch.py`（LateNightNudge + build_behaviors 注册）
- `sidecar/tests/test_watch.py`（LateNightNudge 用例）
- `app/src/components/Avatar.vue`（stretch 态：union/label/脸/双臂/keyframes/reduced-motion）
- `app/src/assets/tokens.css`（--yb-state-stretch 语义灯色一行）
- `app/src/App.vue`（AvatarState 加 stretch；flashState 泛化；reminder 反应分支）
- `app/src/lib/brain.ts`（BrainEvent 补 task?/status?/exit_code?）

---

### Task 1: sidecar LateNightNudge 行为

**Files:**
- Modify: `sidecar/src/yibao_brain/watch.py`（HealthNudge :84-105 之后加类；build_behaviors :194-212 注册）
- Test: `sidecar/tests/test_watch.py`（追加，复用 `_snap`/`_lt`/`WatchCtx` helper，:55-97 先例）

**Interfaces:**
- Consumes: `in_quiet_hours(now, spec)`（watch.py:45）、`WatchSnapshot`（activity={state,seconds,segment_id}, now）、`WatchCtx`、`build_behaviors(settings, ...)`
- Produces: `LateNightNudge`（name="late_night"，`tick(snapshot, ctx) -> dict | None`）；事件 `{"kind":"reminder","type":"late_night","text":...}`

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_watch.py` 追加（放在 build_behaviors 测试组附近）：

```python
# ---------- LateNightNudge（深夜劝睡）----------
def test_late_night_fires_in_quiet_hours_when_active_long():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    snap = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 46 * 60, "segment_id": 1})
    ev = n.tick(snap, WatchCtx())
    assert ev and ev["kind"] == "reminder" and ev["type"] == "late_night"
    assert "早点休息" in ev["text"]


def test_late_night_silent_outside_quiet_hours():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    snap = WatchSnapshot(now=_lt(15, 0), activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(snap, WatchCtx()) is None


def test_late_night_below_active_threshold_and_idle():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00")
    short = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 20 * 60, "segment_id": 1})
    assert n.tick(short, WatchCtx()) is None
    idle = WatchSnapshot(now=_lt(23, 30), activity={"state": "idle", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(idle, WatchCtx()) is None


def test_late_night_cooldown_and_nightly_cap():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00", max_per_night=2, cooldown_s=3600)
    s = lambda mi: WatchSnapshot(now=_lt(23, 30) + mi * 60, activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(s(0), WatchCtx()) is not None       # 第 1 次
    assert n.tick(s(30), WatchCtx()) is None          # 30 分钟后再触发：冷却内
    ev = n.tick(s(70), WatchCtx())                    # 70 分钟后：第 2 次
    assert ev and "收尾就睡" in ev["text"]
    assert n.tick(s(200), WatchCtx()) is None         # 第 3 次：每晚上限 2
    assert n.tick(s(260), WatchCtx()) is None


def test_late_night_resets_after_quiet_hours():
    n = LateNightNudge(active_minutes=45, quiet_hours="23:00-07:00", max_per_night=2)
    late = WatchSnapshot(now=_lt(23, 30), activity={"state": "active", "seconds": 99 * 60, "segment_id": 1})
    assert n.tick(late, WatchCtx()) is not None
    n.tick(late, WatchCtx())  # 可能被冷却拦，无所谓
    day = WatchSnapshot(now=_lt(12, 0), activity={"state": "active", "seconds": 99 * 60, "segment_id": 2})
    assert n.tick(day, WatchCtx()) is None            # 白天不触发，并清零当晚计数
    late2 = WatchSnapshot(now=_lt(12, 0) + 12 * 3600, activity={"state": "active", "seconds": 99 * 60, "segment_id": 3})
    assert n.tick(late2, WatchCtx()) is not None      # 第二天深夜重新可触发


def test_build_behaviors_includes_late_night():
    bs = build_behaviors({"watch.idle_warn_minutes": 30, "watch.quiet_hours": "00:00-06:00"})
    assert [b.name for b in bs] == ["health_nudge", "late_night", "ambient"]
```

（`_snap`/`_lt`/`WatchCtx`/`WatchSnapshot` 沿用文件顶部既有 import 与 helper；注意既有 `test_build_behaviors_slice1`（:95-97）断言 `["health_nudge", "ambient"]`——注册后它会挂，**把它的期望改为 `["health_nudge", "late_night", "ambient"]`**，这是计划内的断言更新。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_watch.py -k late_night -x -q`
Expected: FAIL（`LateNightNudge` 未定义）；`test_build_behaviors_slice1` 此时仍绿（实现后才需改）

- [ ] **Step 3: 实现**

`sidecar/src/yibao_brain/watch.py`：在 `HealthNudge` 类后（:106）加：

```python
class LateNightNudge:
    """深夜劝睡（反应式宠物 C）：静默时段内仍连续活跃 ≥ 阈值 → 打哈欠劝睡。
    每晚最多 max_per_night 次、间隔 ≥ cooldown_s；出静默时段清零重计。
    与 HealthNudge 互斥天然成立：久坐提醒在静默时段被抑制，本行为只在静默时段触发。"""
    name = "late_night"

    def __init__(self, active_minutes: int = 45, quiet_hours: str = "23:00-07:00",
                 max_per_night: int = 2, cooldown_s: float = 3600.0):
        self._active_s = max(1, int(active_minutes)) * 60
        self._quiet_hours = quiet_hours
        self._max_per_night = max(0, int(max_per_night))
        self._cooldown = float(cooldown_s)
        self._fired: list[float] = []  # 当晚已触发时刻（snapshot.now 系）

    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None:
        if not in_quiet_hours(snapshot.now, self._quiet_hours):
            if self._fired:
                self._fired = []  # 天亮出静默时段：清零，明晚重新计
            return None
        act = snapshot.activity
        if not act or act.get("state") != "active":
            return None
        if float(act.get("seconds", 0)) < self._active_s:
            return None
        if len(self._fired) >= self._max_per_night:
            return None
        if self._fired and snapshot.now - self._fired[-1] < self._cooldown:
            return None
        self._fired.append(snapshot.now)
        text = ("很晚了，还在忙吗？早点休息 🌙" if len(self._fired) == 1
                else "深夜了还在忙，收尾就睡吧 😴")
        return {"kind": "reminder", "type": "late_night", "text": text}
```

`build_behaviors` 的 behaviors 列表改为：

```python
    behaviors = [
        HealthNudge(
            idle_warn_minutes=int(settings.get("watch.idle_warn_minutes", 45)),
            quiet_hours=str(settings.get("watch.quiet_hours", "23:00-07:00")),
        ),
        LateNightNudge(
            quiet_hours=str(settings.get("watch.quiet_hours", "23:00-07:00")),
        ),
        Ambient(),
    ]
```

- [ ] **Step 4: 跑测试确认通过 + 改旧断言 + 全量回归**

先按 Step 1 注释把 `test_build_behaviors_slice1` 期望改为 `["health_nudge", "late_night", "ambient"]`，再：

Run: `cd sidecar && uv run pytest tests/test_watch.py -q && uv run pytest -q`
Expected: test_watch 全绿；全量 858+新增 全绿

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/watch.py sidecar/tests/test_watch.py
git commit -m "feat(reactive): LateNightNudge 深夜劝睡行为（静默时段内活跃 ≥45min，每晚 2 次/间隔 1h）"
```

---

### Task 2: Avatar stretch 态（唯一新美术）

**Files:**
- Modify: `app/src/components/Avatar.vue`（:31 union、:57-67 label、:314-343 脸组、:442-450 灯色、:487-519 动画区、:582-586 reduced-motion）
- Modify: `app/src/assets/tokens.css`（--yb-state-* 语义层加一行）

**Interfaces:**
- Consumes: 既有 face 组坐标（success :315-319）、`INK`/`BLUSH` 常量、`.body-grp`（transform-box: fill-box; origin 50% 92%）、`--yb-ease-spring`
- Produces: Avatar state `"stretch"`（Task 3 的 flashState("stretch", 1500) 消费）

- [ ] **Step 1: tokens.css 灯色**

`app/src/assets/tokens.css` 的 `--yb-state-*` 语义组（--yb-state-drowsy 附近）加：

```css
  --yb-state-stretch: var(--yb-c-sky-500); /* 久坐做操：活力青 */
```

- [ ] **Step 2: Avatar.vue 六处**

① props.state union（:31）加 `"stretch"`；② `STATE_LABEL`（:57-67）加 `stretch: "伸展中"`；③ 脸组：在 success 组（:315-319）后加：

```html
        <!-- stretch（做操：弯眼笑 + 张嘴 + 双臂上举） -->
        <g v-else-if="state === 'stretch'">
          <path d="M47 60.5 Q51 57.5 55 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 60.5 Q69 57.5 73 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <ellipse cx="60" cy="70.5" rx="3.4" ry="4" :fill="INK" />
          <path class="arm-l" d="M40 54 Q33 44 30 35" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path class="arm-r" d="M80 54 Q87 44 90 35" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
```

④ 灯色（:450 后）：`.av.stretch { --dot: var(--yb-state-stretch); }`

⑤ 动画区（:497 error 后）加一次性 squash-stretch（覆盖 breathe——`.av.stretch .body-grp` 优先级更高，闪现结束回 idle 后 breathe 恢复）：

```css
/* stretch：一次性做操（下蹲蓄力 → 向上伸展 → 回弹），由 flashState 触发，不循环 */
.av.stretch .body-grp { animation: yb-stretch 1.15s var(--yb-ease-spring) 1; }
.av.stretch .dot-grp { animation: pulse 0.9s ease-in-out 1; }
@keyframes yb-stretch {
  0%   { transform: scale(1, 1); }
  30%  { transform: scale(1.06, 0.86); }
  62%  { transform: scale(0.94, 1.12); }
  82%  { transform: scale(1.02, 0.97); }
  100% { transform: scale(1, 1); }
}
```

⑥ reduced-motion 停用列表（:582-586 的 `@media (prefers-reduced-motion: reduce)` 块内）加：

```css
  .av.stretch .body-grp { animation: none; }  /* 一次性动画同样让位：只留静态脸 */
```

- [ ] **Step 3: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: 全 exit 0

- [ ] **Step 4: 视觉自验 + Commit**

`design.html` 走查页（DesignPreview.vue）若有九态矩阵区，把 stretch 加进去同款一格（读 DesignPreview.vue 的状态列表数组，追加 `"stretch"`）；`npx vite build` 复绿。然后：

```bash
git add app/src/components/Avatar.vue app/src/assets/tokens.css app/src/DesignPreview.vue
git commit -m "feat(reactive): Avatar stretch 态——弯眼笑 + 双臂上举 + squash-stretch 一次性动画（含 reduced-motion 停用）"
```

（若 DesignPreview 无状态矩阵/不适合加，从 add 去掉并在报告说明。）

---

### Task 3: 壳侧反应渲染（flashState 泛化 + reminder 分支 + BrainEvent 字段）

**Files:**
- Modify: `app/src/lib/brain.ts`（BrainEvent :51-63 区域补字段）
- Modify: `app/src/App.vue`（AvatarState :50；flashValence :764-773 泛化；reminder case :587-615 插反应分支）

**Interfaces:**
- Consumes: Task 2 的 stretch 态；既有 reminder 事件字段（`e.type`/`e.level`/`e.day`）；agents 事件 `task.status`（done/failed/stopped）；watch_command 事件 `status`（completed/failed/timed_out/cancelled）+ `exit_code`
- Produces: `flashState(v: AvatarState, ms?: number)`；reminder 三分支无新接口

- [ ] **Step 1: brain.ts BrainEvent 补字段**

`BrainEvent` 接口（`level?` 字段附近）加：

```typescript
  /** 反应式渲染原料：agents 任务完成事件携带 */
  task?: { id?: string; status?: string; label?: string; prompt?: string };
  /** watch_command 完成事件携带（completed/failed/timed_out/cancelled） */
  status?: string;
  exit_code?: number;
```

- [ ] **Step 2: App.vue AvatarState + flashState 泛化**

:50 改为：

```typescript
type AvatarState = "idle" | "listen" | "think" | "work" | "say" | "success" | "error" | "notify" | "drowsy" | "stretch";
```

:764-773 整段替换为：

```typescript
// ---- 短暂闪现（success/error/stretch/drowsy…）：ms 后回 idle，期间不可打断（busy 是 allowlist，闪现态天然不在内）----
let flashTimer: ReturnType<typeof setTimeout> | null = null;
function flashState(v: AvatarState, ms = 400) {
  if (flashTimer) clearTimeout(flashTimer);
  state.value = v;
  flashTimer = setTimeout(() => {
    if (state.value === v) state.value = "idle";
    flashTimer = null;
  }, ms);
}
function flashValence(v: "success" | "error") {
  flashState(v, 400);
}
```

（确认 `busy` computed 确为 allowlist——think/listen/work/say——则无需改；读 App.vue:157-161 确认，若把 success/error 特判列出则同样无需改：闪现态不在 busy 语义内。）

- [ ] **Step 3: reminder case 反应分支**

App.vue `case "reminder"`（:587-615）：在 `bubbles.value.push(...)` 之后、async IIFE 之前插入：

```typescript
      // —— 反应式渲染（C 最小版）：确定性信号 → 一次性闪现；提醒纪律（档位/互斥/TTS）不变 ——
      if (e.type === "health_nudge") {
        flashState("stretch", 1500); // 久坐 → 一套伸展操
      } else if (e.type === "late_night") {
        flashState("drowsy", 3000); // 深夜 → 打哈欠（Zz）
      } else if (e.task?.status === "done" || (e.type === "watch_command" && e.status === "completed")) {
        flashState("success", 1200); // 任务完成 → 星芒欢呼
      } else if (e.task?.status === "failed" || (e.type === "watch_command" && e.status === "failed")) {
        flashState("error", 900); // 任务失败 → 叹气
      }
```

- [ ] **Step 4: 验证**

Run: `cd app && npx vue-tsc --noEmit && npx vite build`
Expected: 全 exit 0

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/brain.ts app/src/App.vue
git commit -m "feat(reactive): reminder 反应渲染——flashState 泛化 + 久坐做操/深夜哈欠/任务欢呼叹气闪现"
```

---

### Task 4: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q          # 858 + 新增 ≈ 864
cd ../app && npx vue-tsc --noEmit && npx vite build && cargo check --manifest-path src-tauri/Cargo.toml
```

- [ ] **Step 2: 真机（人工）验收清单**

1. **任务欢呼**：对译宝说「派个后台任务：30 秒后完成」或直接 `agents` 派个 echo 小任务 → 完成播报时团子星芒闪现 1.2s + 气泡；派一个必失败的（exit 1）→ error 抖动 + 气泡。
2. **深夜劝睡（模拟）**：设置页把静默时段改成覆盖当前时间（如 "00:00-23:59"）→ 连续活跃 45 分钟（或临时把 LateNightNudge 的 active_minutes 调小验证）→ 团子打哈欠 3s + 气泡「很晚了…」。验完改回 "23:00-07:00"。
3. **久坐做操（模拟）**：设置页把久坐阈值调到 1 分钟 → 连续敲键盘 1 分钟 → 团子做伸展操 1.5s + 气泡。验完改回 45。
4. **reduced-motion**：macOS 辅助功能 → 显示 → 减弱动态效果 打开 → 久坐触发时无 squash 动画、只有静态脸。
5. 三档旋钮：proactive.level=quiet 时三个反应都只落 Feed 不闪现（quiet 吞 reminder）——确认安静档安静。

（2/3 的阈值模拟走设置页改值；嫌慢可直接改 sidecar 源码阈值验证后改回，任选。）

- [ ] **Step 3: 收尾 commit（如有验收修小补）**

```bash
git add -p
git commit -m "fix(reactive): 真机验收小修"
```

---

## 自审

- spec 覆盖：LateNightNudge（Task 1）✅、stretch 美术（Task 2）✅、三反应渲染（Task 3）✅、克制纪律验证（Task 4 Step 2.5）✅
- 类型一致：`LateNightNudge(active_minutes, quiet_hours, max_per_night, cooldown_s)`（Task 1 定义=测试使用）✅；`flashState(v, ms)`（Task 3 定义=reminder 分支调用）✅；`"stretch"` 在 Task 2 Avatar union + Task 3 App.vue AvatarState 同步加 ✅；事件字段 `task.status`/`status`/`exit_code`（Task 3 brain.ts 补 = reminder 分支读）✅
- 无占位：每步含完整代码/命令/预期 ✅
