# C 反应式宠物最小版设计（2026-08-08）

> 方向来源：高频场景探索——C（环境反应宠物，「它注意到你」比「它陪你玩」更戳人）。
> 第一版全用**确定性信号**（活动状态/时间/任务退出码），不依赖视觉模型。

## 1. 目标与范围

**目标**：团子对三个真实信号做出「生命反应」，从工具变成在场：

| 反应 | 信号（现有） | 表现 |
|---|---|---|
| 任务欢呼/叹气 | agents `task.status` / watch_command `status`+`exit_code` | success 星芒 1.2s / error 抖动 0.9s + 既有提醒气泡 |
| 深夜劝睡 | `in_quiet_hours`（23:00-07:00）内连续活跃 ≥45min | 新 LateNightNudge 行为 → drowsy 哈欠 3s + 气泡 |
| 久坐做操 | health_nudge（连续活跃 ≥45min，同段一次） | 新 stretch 一次性动画 1.5s + 既有提醒气泡 |

**核心洞察**：三条信号与渲染管线（reminder → dispatcher → 三档闸门/👎降频/Feed）全部现成——C 是把「信号」升级成「反应」，不是建信号。

**不做**：视觉模型反应、键盘强度（不装 event tap）、反应注册表抽象（三个硬编码，YAGNI）、新 TTS 通道。

## 2. 架构

### 2.1 sidecar：LateNightNudge（唯一新信号）

`watch.py` 新行为，与 HealthNudge 并列注册进 `build_behaviors`：
- 条件：`in_quiet_hours(now)` 内 且 快照 activity.state=="active" 且连续活跃秒数 ≥ 45×60
- 冷却：每晚最多 2 次、间隔 ≥60 分钟；出静默时段清零重计（`clock` 注入便于测试）
- 产出 `{"kind":"reminder","type":"late_night","text":...}`（首次「很晚了，还在忙吗？早点休息 🌙」/再次「深夜了还在忙，收尾就睡吧 😴」）
- 与 HealthNudge 无互斥问题：HealthNudge 在静默时段被抑制（in_quiet_hours → None），不会双打
- 带 `type` → dispatcher 的 👎降频 + proactive.level 三档自动生效；Feed 落账照旧

### 2.2 壳侧：flashState 泛化 + reminder 反应分支

- `flashValence(v)`（400ms 闪现）泛化为 `flashState(v: AvatarState, ms)`，旧函数保留为 wrapper；`busy` 计算不受影响（它是 allowlist：think/listen/work/say，闪现态天然不在内）
- `AvatarState`（App.vue:50）加 `"stretch"`
- reminder case（App.vue:587-615）在既有渲染前插入反应分支：
  - `e.type === "health_nudge"` → `flashState("stretch", 1500)`
  - `e.type === "late_night"` → `flashState("drowsy", 3000)`（state 非 idle 即直接呈现，petState 短路逻辑 :67-68 天然支持）
  - `e.task?.status === "done"` 或 `watch_command` 且 `e.status === "completed"` → `flashState("success", 1200)`
  - `e.task?.status === "failed"` 或 `watch_command` 且 `e.status === "failed"` → `flashState("error", 900)`
- 其余提醒纪律（大小窗互斥、level 档、sticky 气泡、TTS）一字不动
- `BrainEvent`（brain.ts）补 `task?`/`status?`/`exit_code?` 字段

### 2.3 Avatar：stretch 新态（唯一新美术）

Avatar.vue 六步（既定新态流程）：
1. props.state 联合类型 + App.vue AvatarState 加 `"stretch"`
2. `STATE_LABEL`：stretch = 「伸展中」
3. 脸组（v-else-if）：弯眼笑（复用 success 眼坐标）+ 张嘴
4. 双臂上举（简单 path，身体两侧 30° 上举）
5. `--dot` 灯色（sky 系）+ 一次性 keyframes：`yb-stretch` 1.15s spring 一次（下蹲蓄力 scale(1.06,0.86) → 上伸 scale(0.94,1.12) → 回弹），`transform-origin` 底部中心，挂在 `.av.stretch .body-grp`（覆盖 breathe）
6. `prefers-reduced-motion` 停用列表加 stretch 动画（:582-586，常驻软件底线）

### 2.4 错误处理与克制

- LateNightNudge 所有字段缺失/类型异常 → tick 返 None（行为循环不炸）
- 反应闪现与用户交互冲突：flashState 期间用户点团子展开 → expand 正常进行（state 会被后续事件覆盖，可接受）
- Feed：三类都低频，不豁免；👎 降频闸对 health_nudge/late_night 生效（type 在）

## 3. 测试策略

- **sidecar（pytest，test_watch.py 追加）**：LateNightNudge——静默外 None 且清零、静默内活跃不足 None、达阈值触发 type/文案、间隔冷却、每晚上限 2 次、跨午夜。仿 HealthNudge 既有用例（构造 WatchSnapshot + clock 注入）。
- **Rust/前端**：vue-tsc + vite build（无前端单测框架）；cargo 不动。
- **真机验收**：任务欢呼（派个 agents 小任务）、深夜劝睡（临时把 quiet_hours 改成当前时段模拟）、久坐做操（idle_warn_minutes 调小模拟）、reduced-motion 下动画停用。

## 4. 文案

- 深夜首次：「很晚了，还在忙吗？早点休息 🌙」；再次：「深夜了还在忙，收尾就睡吧 😴」
- 久坐/任务文案沿用既有（不改）

## 5. 风险

- **drowsy 复用冲突**：发呆计时（5 分钟纯待命，仅收起态）与深夜哈欠同写一个 state——petState 的 `state.value !== "idle"` 短路使两者不打架（闪现态优先呈现）；flashState 结束回 idle 后发呆逻辑照旧。
- **深夜亮窗克制**：late_night 不跳体现状纪律（level=bubble 档不亮窗）。宠物窗被用户隐藏时不补 show——等 E 后续真机观察再定。
