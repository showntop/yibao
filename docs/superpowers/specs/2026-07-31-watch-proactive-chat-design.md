# 场景化主动搭话（watch slice 3，截图视觉）设计

日期：2026-07-31  关联：[[v1-status]]（watch mode slice 3）。用户选「直接上截图视觉」。

## 目标 / 非目标
**目标**：watch 循环里新增 `ProactiveChat` 行为——在**活动段切换 + 节流 + 应用白名单 + 预算闸**都通过时，截一张图交**视觉 LLM**（现有 GLM-4.1v）判断「是否有值得主动帮忙的点」；有则经 `proactive.level` 出口发一句短建议。
**非目标**：不替用户自动操作（只建议，不动手）；不做多轮主动对话（一次一建议）；文本（app/title）版不做（用户选了视觉版）。

## 架构

### 复用 + 新增
- 复用：watch 循环（slice 1）、`WatchBehavior` 协议、`ctx.emit_event` 主动通道（slice 4 接的那条，gated）、`ComputerUseClient`（视觉模型，新增 `observe` 方法）。
- 新增：`Budget`（预算闸）、`ProactiveChat` 行为、`WatchCtx` 扩字段、`watch.observe_apps` 等设置。

### Budget（watch.py）
```python
class Budget:
    """调用预算闸：滑动窗口限每小时/每日次数。allow() 命中即计入；超限返 False。"""
    def __init__(self, max_per_hour: int, max_per_day: int, *, clock=time.time): ...
    def allow(self) -> bool: ...   # 清过期戳 + 判当前窗口未超 → 记一条戳、返 True；否则 False
```
纯逻辑、可单测（注入 clock）。

### ComputerUseClient.observe（llm.py）
```python
def observe(self, screenshot_b64: str, app: str) -> dict | None:
    """视觉模型看一眼：是否有值得主动搭话的点。返 {"speak":bool,"text":str} 或 None。"""
```
系统提示：看截图，仅在有明显可帮的点（报错/编译失败/卡住的对话框/明显困惑）时 speak=true 并给一句≤20字中文建议；否则 speak=false。best-effort（GLM-4.1v extra_body thinking），真机验收；测试用 client_factory 注入 fake。

### ProactiveChat（watch.py，WatchBehavior）
`tick(snapshot, ctx)` 只做**廉价门控**，通过则**起后台线程**做贵的视觉调用（不阻塞循环）：
- 门控（全过才看）：① 活动段切换（`segment_id` ≠ 上次所见）；② 节流（距上次看 ≥ `look_min_gap`，默认 300s）；③ 前台 app ∈ `observe_apps` 白名单（**默认空 → 永不看，opt-in**）；④ `budget.allow()`。
- 后台线程：`ctx.host.screenshotter.capture()` → b64 → `ctx.vision.observe(b64, app)` → 若 `speak` → `ctx.emit({"kind":"reminder","text":text})`。任何异常只记 stderr（不杀）。
- 跨 tick 状态（last_segment / last_look_ts）放行为实例自身。

### WatchCtx 扩字段
```python
@dataclass
class WatchCtx:
    settings: dict = ...
    host: Any = None          # 截图（ProactiveChat 用）
    vision: Any = None        # ComputerUseClient.observe（None=无视觉，行为静默）
    budget: Any = None        # Budget
    emit: Any = None          # gated 主动事件通道（后台线程直发）
```

### build_behaviors + 接线
- `build_behaviors(settings, *, host=None, vision=None, budget=None, emit=None)`：在 HealthNudge/Ambient 之外，**仅当 `vision` 可用 且 `observe_apps` 非空** 才注册 ProactiveChat（否则不建，省资源）。
- watch 循环把 host/vision/budget/emit 塞进 WatchCtx。`serve_async`：建 `ComputerUseClient`（vision_model 可用才建）+ `Budget(max_per_hour, max_per_day)`，传给 `_watch_loop`。

## 配置 / 隐私
- 新增 settings：`watch.observe_apps`（list，默认 `[]`）、`watch.look_min_gap`（默认 300）、`watch.look_max_per_hour`（默认 6）、`watch.look_max_per_day`（默认 50）。
- **隐私**：白名单默认空 = 不截任何图、不上云；用户显式加 app 才对那些 app 截图上云。截图只发云端视觉模型（GLM），不落库、不回传第三方。

## 测试（fake 注入，不触真截图/视觉）
- `Budget`：每小时/每日上限、窗口滑动过期、clock 注入。
- `ProactiveChat`：① 门控全过 → 起一次看、budget 扣 1、vision 返 speak=true → emit 收到；② speak=false → 不 emit；③ 白名单空/段未变/节流内/预算尽 → 不看（budget 不扣）；④ vision/host 缺 → 静默；⑤ 后台异常不杀。
- `observe`：client_factory 注入 fake（不触网）。
- 集成：`build_behaviors` 在 vision 缺/白名单空时不建 ProactiveChat。

## 风险 / 验收
| 风险 | 处置 |
|---|---|
| 视觉调用慢阻塞循环 | 后台线程做，tick 只廉价门控 |
| 费用失控 | Budget 每小时/每日双闸 + 节流 + 白名单默认空 |
| 隐私（截图上云） | 白名单 opt-in；默认空永不上云 |
| 误打扰 | speak 由视觉模型判；仍受 proactive.level 管（quiet 不响） |

**验收（用户）**：① `watch.observe_apps` 加入当前 app（如「代码编辑器/终端」）→ 切到该 app 触发一次看 → 屏幕有报错时收到一句建议；② 无值得搭话时不出声；③ 预算/节流到顶不再看；④ quiet 档不响、full 档气泡+语音。
