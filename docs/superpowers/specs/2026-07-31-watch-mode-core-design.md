# watch mode 核心设计（slice 1：循环 + 健康节律 + 在场陪伴）

日期：2026-07-31  关联：[[v1-status]]（v2 watch mode 第一刀）；spec 仅覆盖 slice 1，后续 slice 各自再出 spec。

## 1. 目标 / 非目标（slice 1）

**目标**
- 把现有「被动感知记录」升级为「主动观察 + 可出手」的 watch 回路，并把**行为做成可插拔策略**，为后续 slice 打地基。
- 落地两个最简、零 LLM、零隐私风险的行为：
  - **健康节律**：久坐提醒（纯定时，读 perception 的 active/idle 时长）。
  - **在场陪伴**：循环在跑、上下文保持热；本期不主动出声。
- 复用现有出口：行为产出的事件经 `_gate_proactive_event`（`proactive.level`）→ 气泡/Feed/语音。

**非目标（推迟到后续 slice）**
- 预算闸（`budget.allow()`）——没有 LLM 行为就不花预算，与「场景化搭话」一起做。
- 场景化主动搭话（屏幕→云端 LLM 理解+建议；花预算 + 隐私）。
- 后台盯任务（任务注册 + 完成检测）。
- 屏幕内容上云（本期不碰，隐私零增量）。

## 2. 架构

### 2.1 watch 循环（server.py 新增 async 长任务）
- 由总开关 `watch.enabled`（settings，**默认关**）启停。`serve_async` 启动时若开则 `asyncio.create_task`；关则不启。
- 每 `watch.cadence`（默认 60s）tick 一次：
  1. 构造 **快照** `WatchSnapshot`：当前前台 app、当前活动段（active/idle 及已持续时长），从 perception 取（受现有 `perception.app`/`perception.activity` 开关管；关则对应字段为空）。
  2. 依次调用注册的 `WatchBehavior.tick(snapshot, ctx)`；每个返回一个**主动事件**或 None。
  3. 收到的事件走现有 `_gate_proactive_event(ev, settings)`（`proactive.level`：quiet 吞 reminder / 其余标注 level）→ 经现有 emit → 气泡/Feed/语音。
- 取消：共享 cancel 语义（serve 退出 / 总开关关 → 取消 watch task）。

### 2.2 行为接口（新）
```python
class WatchBehavior(Protocol):
    name: str
    def tick(self, snapshot: WatchSnapshot, ctx: WatchCtx) -> dict | None: ...
```
- `WatchSnapshot`：`now: float`、`app: str | None`、`activity: {"state": "active"|"idle", "seconds": float} | None`。
- `WatchCtx`：行为要用的依赖（当前 settings、perception store 的查询句柄、各行为自己的跨 tick 记忆——如「本活动段已提醒过」）。**记忆放在行为实例自己身上**（有状态对象），不放全局。
- 返回的事件 dict 形如 `{"kind": "reminder", "text": "...", "level": ...}`，复用现有 reminder 事件形态，可直接喂 `_gate_proactive_event` / `_dispatch_reminder` 风格出口。

### 2.3 健康节律行为
- 字段：`idle_warn_minutes`（默认 45）、`quiet_hours`（默认 "23:00-07:00"，可配/可关）。
- tick 逻辑：
  - `activity.state == "active"` 且 `activity.seconds >= idle_warn_minutes*60`
  - 且 `now` 不落在 quiet_hours
  - 且 `self._last_warn_segment` 不是当前活动段（同段只提醒一次——perception 活动段切换时刷新段 id）
  - → 返回 reminder 事件「坐久了，起来活动一下吧 🧘」；记下当前段 id。
- idle 状态不提醒；quiet_hours 内不提醒；无 activity 数据不提醒。

### 2.4 在场陪伴行为（slice 1）
- `tick` 恒返回 None（本期不出声）。
- 存在意义：占位 + 让循环稳定跑、上下文保持热（perception 持续记录即「在场」）；为 slice 3 的主动搭话留插点。
- 行为对象预留 `snapshot_history`（近期快照环）字段，slice 3 用，本期不填。

## 3. 配置 / 设置 UI
config.py（settings 双轨）：
- `watch.enabled`（默认 False）、`watch.cadence`（默认 60）、`watch.idle_warn_minutes`（默认 45）、`watch.quiet_hours`（默认 "23:00-07:00"，空串=关）。
进 `_SETTINGS_DEFAULTS` / `_SETTINGS_ENUMS`（无枚举；都是标量）。
SettingsView「自主权」区加：watch 开关 + 久坐时长 + 静默时段（cadence 高级项走 settings.json 不上 UI 或折叠）。

## 4. 隐私
slice 1 只读 app/activity（现有 perception 已加密落库、且受 `perception.app`/`perception.activity` 开关管），**不上云、不读屏幕**。`watch.enabled` 默认关。隐私零增量相对现有 perception。

## 5. 测试（不触真定时/真 perception）
- `WatchSnapshot` 构造（fake perception 返回 canned app/activity）。
- **健康节律**单测：① active≥阈值且非静默且未提醒 → 出 reminder；② 同段二次 tick → None（不重复）；③ 阈值内 → None；④ idle → None；⑤ 落在 quiet_hours → None；⑥ 段切换后重新允许提醒。
- quiet_hours 解析（跨午夜 "23:00-07:00"）单测。
- **循环分发**：fake behavior 返回事件 → 经 `_gate_proactive_event` 在 quiet 档被吞、full 档放行（用现有 gating 逻辑）。
- 行为对象跨 tick 记忆隔离（两个实例不串）。

## 6. 风险 / 验收
| 风险 | 处置 |
|---|---|
| 总开关忘开 → 用户以为没反应 | 默认关但 UI 明显；开启引导 |
| quiet_hours 跨午夜解析错 | 单测覆盖 "23:00-07:00" |
| 循环异常杀掉整轮 watch | tick 外层 try/except，单行为报错只记 stderr 不死循环 |
| 活动段 id 来源 | 用 perception 的 activity 段（active/idle 切换为段边界）；无段时用「首次满足阈值的时间窗」兜底 |

**验收（用户）**：① 开 watch + 连续用机 >45min（非静默时段）→ 收到久坐提醒，且同段不再重复；② 23:00–07:00 内不提醒；③ quiet 档提醒被吞、full 档出气泡(+语音)；④ 关 watch → 不再有任何主动行为。

## 7. 实施顺序（writing-plans 细化）
1. WatchSnapshot/WatchCtx + 健康节律 behavior（纯逻辑，单测全）。
2. quiet_hours 解析 + 单测。
3. watch 循环（serve_async 启停 + tick 分发 + gating）+ 总开关/cadence config。
4. settings（idle_warn/quiet_hours）+ 设置 UI 开关。
5. 在场陪伴占位行为 + 循环注册两个行为。
