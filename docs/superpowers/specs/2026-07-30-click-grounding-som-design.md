# 设计文档：点击精度——Set-of-Marks 视觉 Grounding

- **状态**：已定稿，待写实现计划
- **日期**：2026-07-30
- **关联**：`docs/superpowers/specs/2026-07-16-desktop-agent-design.md` §5（执行层）、§12 v2 路线图（本地 grounding 可选）
- **代码现状**：`sidecar/src/yibao_brain/skills_real.py`（`ClickControlSkill` / `ComputerUseSkill`）、`sidecar/src/yibao_brain/llm.py:230`（`ComputerUseClient`）

---

## 1. 背景：当前点击为何不准

产品核心承诺是「操作我的软件」，点击精度是当前已知短板。现有两条点击路径各有硬伤：

1. **`ClickControlSkill`（a11y 确定性优先）**：`find(role/title)` → `AX-press`，命中时精确确定。但 a11y 找不到时会**回退坐标 `(x,y)` 点击**——而这个技能**不看屏幕**，坐标是 LLM 凭空猜测的（「盲坐标回退」），几乎必然点偏。
2. **`ComputerUseSkill`（GLM-4.6V 视觉兜底）**：让通用 VLM 直接吐「目标元素的绝对像素 bbox」→ 点框中心。通用 VLM 的**像素级定位天然不准**（常偏几十~几百 px），无校验、无标记；且为多步循环，慢。

两条路径的共同根因：**当 a11y 无法识别目标时，定位退化为「无视觉依据的坐标」**。

## 2. 目标与非目标

**目标**
- 消除「盲坐标」与「裸 VLM 像素 bbox」两条不准路径，统一为有视觉依据的定位。
- a11y 可见区做到**零坐标误差**（确定性 AX-press）；a11y 盲区做到**格子级**精度（网格 SoM）。
- 先量化现状（团队尚未系统测过点击精度），再以数据验证改进。

**非目标（本轮不做）**
- 本地 grounding 重模型（OS-Atlas/ShowUI）——作为评测未达标时的备案（见 §9）。
- TTS 升级（CosyVoice2）——独立子项目，另行 spec。
- Live2D 形象、watch mode 等其它 v2 项。

## 3. 方案选型：混合 Set-of-Marks

核心机制 **Set-of-Marks (SoM)**：截图上为每个候选目标叠编号标记 → VLM 只需「选第几号」→ 反查得到确定坐标。把 VLM 从「精确算坐标」降级为「选哪个」，精度跃升，GLM-4.6V 现成可用，无需带新模型。

标记来源三选一：

| 方案 | 标记来源 | 精度 | 覆盖 | 复杂度 |
|---|---|---|---|---|
| **A. 混合（a11y 帧 + 网格补齐）** ✅采纳 | a11y 交互元素 frame 优先；稀疏区叠网格 | 高（a11y 区零误差） | 全（自绘 UI 有网格兜底） | 中 |
| B. 纯网格 | 全屏网格 | 中（仅格子中心） | 全 | 低 |
| C. 纯 a11y 帧 | 仅 a11y 元素 | 最高 | 差（自绘/Canvas 全盲） | 低 |

**选 A**：团队「没系统测过」失效场景，混合两边都覆盖不押注；隐藏增益是选中 a11y 标记时可**直接 AX-press**（零坐标误差），仅网格格回退坐标。Phase 0 评测（§6）将给出 a11y 区 vs 自绘区的实际占比，后续可据此调网格密度。

## 4. 架构与集成

### 4.1 新模块 `grounding.py`——`SoMGrounding`（纯编排，host 注入，可单测）

三方法：

- `build_marks(screenshot_path, a11y_tree) -> (marked_image_b64, marks)`
- `ask(client, marked_image_b64, target) -> mark_id | None`
- `resolve(mark_id, host) -> ActionResult`

详见 §5。

### 4.2 现有技能改造（最小 churn，保留快路径）

| 技能 | 改造 |
|---|---|
| `ClickControlSkill` | `find(role/title)` → `AX-press` **不变**；**移除盲坐标 `(x,y)` 回退**；a11y 找不到时回提示「该控件无法定位，建议用 computer_use 视觉定位」，把视觉需求导向 computer_use |
| `ComputerUseSkill` | 多步循环保留；每步的「找目标」从「吐绝对像素 bbox」**改为 SoM**（`build_marks → ask → resolve`）。`max_steps=1` 即单发 grounded click |

### 4.3 数据流（兑底式：a11y 优先，SoM 兜底）

```
LLM 要点目标 T
├─ T 有 role/title → click_control → a11y find → AX-press ✅瞬时、确定性
└─ a11y 识别不了 → computer_use(grounded)，每步：
     截图 + a11y 树 → build_marks(叠编号) → GLM-4.6V 选编号 → resolve
       ├─ 标记是 a11y 元素 → AX-press ✅零误差
       └─ 标记是网格格 → 坐标点击格子中心
     多步：重截图 → 下一步，直到 finish / 连续两帧无变化
```

**核心增益**：选中 a11y 标记 → 直接 AX-press 而非坐标点击。a11y 可见区从「VLM 像素 bbox」升级到「零误差确定性」。

## 5. SoM 实现细节

### 5.1 `build_marks`
1. **选 a11y 交互元素**：按 role 白名单取（AXButton / AXLink / AXTextField / AXCheckBox / AXPopUpButton / AXMenuItem / AXTab / AXSlider …），带各自 `AXFrame`；IoU>0.8 的重叠元素合并；编号 `1..n`。
2. **网格补齐**：若 a11y 交互元素 `< 8` 个（疑似 a11y 看不清 / 自绘 UI），全屏叠自适应网格（基础约 6×4，按屏幕比例），继续编号 `n+1..m`。
3. **封顶 ≤ 40**：超额时优先保 a11y 元素，网格稀疏采样降配额——保证 VLM 可读。
4. **渲染**：在截图副本上每个标记画半透明框 + 编号（PIL `ImageDraw`，编号字号足够大），返回叠加图 + `marks = [{id, source:"a11y"|"grid", handle?, center:(x,y), rect}]`。

**⚠️ 坐标系（关键，单测重点）**：截图为物理像素，a11y / pyautogui 为逻辑像素（Retina≈2）。
- `marks` 中 `center` / `rect` **统一存逻辑坐标**（`resolve` 时直接用）。
- 渲染叠加框用**物理像素**（叠在原图上）。
- 复用现有 `ComputerUseSkill._scale`（物理宽 / 逻辑宽）。所有 a11y frame 需确认其原生坐标系并归一化到逻辑坐标。

### 5.2 `ask`
Prompt（`ComputerUseClient` 沿用，仅替换 SYSTEM_PROMPT 的动作段）：
> 屏幕上每个可交互元素 / 区域都标了编号 (1..N)。要点「{target}」，选最合适的编号，**只输出一个数字**。

解析：取响应中首个整数，范围校验 `1 ≤ id ≤ len(marks)`，越界 / 非法 → 返回 `None`。

### 5.3 `resolve`
查 `marks[mark_id]`：
- `source == "a11y"` 且 `handle` 有效 → `host.a11y.press(handle)`（确定性）；
- 否则（网格格，或 a11y handle 在多步循环中失效）→ `host.input.click(center)`。
- `AX-press` 抛错 / 返回失败 → 回退 `input.click(center)`，结果记 `method` 字段留痕。

## 6. Phase 0：点击精度评测脚手架

目的：量化「没系统测过」的现状，给 SoM 一个可对比的基线。

- **位置**：`scripts/eval_click.py`（手动评测，**非 CI 必跑**）。
- **场景格式**：`{name, screenshot_path, target_text, gt}`，`gt` 为「点 `(x,y)` / 区域 `rect` / a11y `role+title`」之一。
- **对比**：baseline = 现有 raw-bbox `computer_use`；new = SoM。同 `(截图, target)` → 预测点 → 算指标。
- **指标**：`hit-rate`（预测点落 gt 区域内 / AX-press 命中 gt 元素）+ `center-distance`（到 gt 中心的逻辑 px）。
- **场景**：约 12 个真实截图，覆盖——计算器 / 系统设置（a11y 易）、Safari 网页链接（a11y 中）、Canvas / 自绘 UI + Electron 应用（a11y 盲）。配套采集小工具：复用 `screenshot` 技能 + 手工标 gt。
- **产物**：baseline vs SoM 对照表 → 决定 SoM 是否达标、是否触发 §9 本地 grounding 备案。

## 7. 错误处理

| 情况 | 处理 |
|---|---|
| GLM 返回越界 / 非法 mark_id | 当作本步未找到 → 继续下一步或 `finish`（**改现有「非法即 break」**） |
| a11y 树取不到 | 纯网格标记 |
| 标记数 < 3 | 网格补齐 |
| 渲染 / 编码失败 | **回退旧 raw-bbox prompt**（兜底，保证不劣于现状） |
| `AX-press` handle 失效 | 回退点 `center` |
| 连续两帧无变化 | 停（现有逻辑保留） |

## 8. 测试与成功标准

**单测（CI 必跑）**
- `build_marks`：fake a11y 树 + 假图 → 断言标记 id 连续、交互元素入选、网格补齐触发、封顶生效、**坐标 scale 正确**（物理↔逻辑）。
- `ask`：`"7"` → 7、`"第3号"` → 3、垃圾 → `None`、越界 → `None`。
- `resolve`：a11y 标记 → 调 `press`；网格标记 → 调 `click`；handle 失效 → 回退 `click`。
- 回归：`ComputerUseSkill` 多步循环接 SoM 跑通（fake host）；`ClickControlSkill` 移除盲坐标回退后旧用例更新。

**评测成功标准（Phase 0）**
- SoM 的 `hit-rate` **比 baseline 高 ≥ 20 个百分点（绝对）**，**且标准控件区 ≥ 85%**。
- 未达标 → §9 本地 grounding 模型进入评估。

## 9. 未来选项（本轮不做，备查）

- 本地 grounding 重模型（OS-Atlas / ShowUI / OmniParser）：Phase 0 评测若 SoM 不达标则启动评估。精度更高，代价是带几百 MB~GB 模型 + 打包变重。
- `ClickControlSkill` 保留 `x/y` + SoM 在该坐标附近复核（更稳但更复杂，替代直接移除盲坐标回退）。
- 单发 `grounded_click` 独立技能（脱离 computer_use 多步循环）。

---

## 开放问题

- 暂无。实现期若 Phase 0 数据显示 a11y 区 / 自绘区占比极端（如自绘区占绝大多数），回来调整网格密度策略与成功标准权重。

---

## 验收记录（2026-08-01，v1.1 收口迭代）

- **基线**：SoM 2/12（场景集 `sidecar/scripts/eval_scenarios` 12 例），miss 偏移 100-870px。
- **根因**：a11y 覆盖空洞（AXRow 不入白名单 + a11y≥8 不叠网格）+ flash 模型选号抖动。
- **迭代**：AXRow 白名单 ✅（保留）；网格盲区兜底（实证 salience 竞争，已 revert）；分层双轨（字母轨被模型证伪 0/24，代码保留基础设施）；**win 裁窗路径成立**（4/12 全路径最佳，calc 3/3 满分，平均 22.8px）——与生产 `prefers_raw_bbox` 窗口裁剪路径互证。
- **验收口径（修订）**：AX 直达场景 100% 硬线；win 裁窗防回归线 ≥4/12 且 calc 3/3（2026-08-01 复测通过）；盲桶硬线废除转 backlog。详见 `docs/reports/2026-08-01-v1.1-slice1-baseline.md` §5-§9 与 `2026-08-01-click-grounding-hierarchical-design.md`。
