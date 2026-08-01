# 点击 grounding 分层两阶段设计（双轨标记 + 区域放大）

日期：2026-08-01  关联：`docs/reports/2026-08-01-v1.1-slice1-baseline.md`（§7 复测对照）、`2026-07-30-click-grounding-som-design.md`、`2026-08-01-v1.1-consolidation-design.md`

## 1. 背景与根因链

Slice 1 基线点击全红（SoM 2/12）；Slice 2A 修复（AXRow 白名单/网格盲区兜底/低温/渲染）后复测**两轮 0/12**——覆盖空洞已修（正确标记 12/12 存在），但暴露两个更深问题：

1. **salience 竞争**：24 个编号网格格与 a11y 小框同轨并列，模型系统性偏爱大格（calc 从 2/3 退步到 0/3）。网格兜底已 revert。
2. **构造性矛盾**：spec 盲桶要求中位距 ≤20px，但 240×233 的格子即使选对，格心离小目标也远超 20px——单发网格 SoM 数学上不可能达标。

结论（用户已裁定）：改**分层两阶段**——粗决策（选区域）与精定位（小图 grounding）分离，两头难度都降到小模型能力圈内。

## 2. 协议 v2：双轨标记（Stage 1）

`build_marks` 重构为双轨，**网格格（_grid_cells/GRID_TRIGGER）整体废除**：

- **数字轨（元素）**：a11y 交互元素（含 AXRow），红实线框 + 框角数字标签（沿用 Slice 2A 渲染）。编号 1..N。
- **字母轨（区域）**：屏幕逻辑区域 3 列 × 2 行 = 6 区（A–F，行优先），**灰色细虚线框** + 框角大字母——视觉显著性明确低于红框。
- 返回签名变更为 `(b64, marks, zones)`：marks 为数字轨（原结构），zones 为 `[{"letter","rect","center"}]`（逻辑坐标）。

`choose_action` v2：

- Prompt 双轨语义：目标在红框元素上→输出数字编号；目标是网页/画布等红框外内容→输出所在字母区域。
- 解析 v2：JSON `{"action":"click","mark":N}` / `{"action":"zoom","zone":"X"}`；裸文本单字母（A–F 范围内）→ zoom；数字 → click。范围外一律 None（防失控，同现状）。
- temperature=0.1 沿用。

## 3. Stage 2：区域放大（zoom）

`zoom_ground(client, shot_path, zone_rect, scale, target) -> (x, y) | None`（grounding.py，client 注入无循环依赖）：

1. 按 zone_rect × scale 裁切物理截图（clamp 到图界），PNG b64。
2. crop 直接走 `client.next_action`（原生 bbox 路径，**不再标记**）——基线证据：原生 grounding 全屏近失 ~26px，小图精度足以支撑 ≤20px 中位距；4.1v 归一化转换在 client 内自理。
3. box 中心 → ÷scale → +zone_rect 原点 → 屏幕逻辑坐标。无 box/异常 → None。

生产 `computer_use` 链路（skills_real.py）同步：build_marks 3-tuple 适配；loop 内 zoom 动作 → zoom_ground → 命中点走 `SoMGrounding.resolve_point(cx, cy, host)`（从 resolve 抽出：element_at AX-press 优先、失败坐标点击），复用既有交互租约检查（_apply_marked 前的 lease 已覆盖）。

## 4. 范围与边界

- **做**：grounding.py（双轨 + zoom + resolve_point）、llm.py（choose_action v2）、eval_click.py（run_som v2）、skills_real.py（3-tuple + zoom 处理）、测试全量适配。
- **不做**：spec §3 达标线不动；模型默认值不动（维持 glm-4.1v-thinking-flashx，4.6v 复测不更优）；`prefers_raw_bbox` 生产路径不动（4.1v 配置下它走窗口裁剪原生 bbox，与本设计哲学同构）。
- **验证**：eval_click 两轮（4.1v / 4.6v 对照）对照 spec §3：可及桶 7/7；盲桶 ≥4/5 且中位距 ≤20px。Safari 分桶口径沿用 §7 注明（网页内容走字母轨，属预期架构）。
- **回退线**：两轮仍双红 → 不再迭代第三个回合，数据上报用户讨论（架构级再议）。

## 5. 风险与缓解

- **字母轨被误选**（a11y 富场景模型仍选字母）：灰虚线低显著 + 低温 + prompt 语义三重抑制；eval 观察 calc 是否回到 ≥2/3。
- **zoom 二次调用失败**：返回 None，eval 记 miss、生产记本步无动作（不失控），与既有非法输出处理一致。
- **行级歧义残留**（sysset 错一行）：本设计不专治，靠 AXRow + 框角标签改善；复测量化观察。
