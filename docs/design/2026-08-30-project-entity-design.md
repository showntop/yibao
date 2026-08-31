# 项目实体：从插件围墙到工作语境（设计稿 v1）

> 状态：V1a 已实施（2026-08-31，分期与口径校准见 §8）；P2 起待定稿
> 日期：2026-08-30
> 缘起：地平线只有 `ctx: home · idle` 这样的状态读数，没有「我在做哪个项目」。
> 用户问「项目/空间/目录的选择器及展示区呢」——需要一个真·项目实体。

## 1. 定义：项目是视图，不是容器

**项目 = 一组引用的聚合 + 一个名字。** 它不搬走任何东西：

```
Project {
  id: "pj_xxx"
  name: "上周周刊"                 // 唯一必填
  created_at, touched_at
  objects: [                       // 引用式挂载（type + ref 指向各域已有实体）
    { type: "zimeiti.topic",  ref: "42" }
    { type: "coding.cwd",     ref: "/Users/x/work/weekly" }
    { type: "notes.dir",      ref: "weekly" }
  ]
  conversation_ids: ["cv_…"]       // 阶段 2：会话归属
  material_ids: ["mt_…"]           // 阶段 2：素材归属
}
```

引用式（不是容器）的原因：与工作台哲学同构——surface 是器物的装配视图，
项目是工作对象的装配视图。zimeiti 的选题还在 zimeiti，coding 的目录还在
coding；项目只是把它们**指**在一起。

## 2. 存储：core 级 JSON（先轻后重）

`config.data_dir/projects.json`，原子写（reminders.json 先例）+ ProjectStore
（FeedStore 先例的轻量版）。数据量级：个位数到几十个项目、每项目个位数对象，
JSON 足够；将来跨设备同步/多人再迁 SQLite。

`current_project_id` 进 settings.json（全局单例，重启保持）。

## 3. 语义：切换项目改变什么（阶段 1 = 最小诚实）

- **地平线 ctx 读数**：`ctx: 上周周刊 · idle`（无项目时 `ctx: home · idle`）
- **语境 peek**：点击 ctx → 列项目（当前高亮）+ 新建 + 切换
- **项目卡展示区**：抽屉形态？独立？——阶段 1 先做 ctx peek 内的行内卡片
  （名字 + 对象列表 + 最近活跃），不新增常驻区
- **不硬挂的**：今日安排（reminders 无项目归属，不造关联）；会话/素材过滤
  （阶段 2）

## 4. 接口

- IPC（壳→脑）：`project_list` / `project_create` / `project_switch` /
  `project_add_object` / `project_remove_object`；变更广播 `projects` 回执
  （settings 同款模式）
- Rust：get_projects / create_project / switch_project / project_add_object
  五条命令（commands.rs 同款模板）
- 前端：brainClient 六个薄封装；`useProject` composable（current + list）
- agent tool（阶段 3）：`project.open` / `project.current` / `project.attach`
  ——agent 与人同权操作项目（design §2 检验清单第 4 条）

## 5. UI

- **选择器**：地平线右端 ctx 位升级为语境按钮（mono 小字 `上周周刊 · idle`，
  无项目时 `home · idle`），点击 peek 出项目列（左对齐还是右对齐？——
  跟入口同侧：右缘 peek，向左展开）
- **展示区**：peek 内行内项目卡（名字 + 对象引用 chips + touched_at）；
  顶栏语境徽标继续承担"当前工位"展示，与项目解耦（工位 ≠ 项目：
  工位是现在打开的面板，项目是跨面板的工作语境）

## 6. 阶段切分

- **P1**（本设计稿范围）：ProjectStore + 五条 IPC + Rust + ctx 语境 peek
  （列/建/切）+ ctx 读数接项目名。~1 天
- **P2**：会话与素材挂项目、列表按项目过滤、项目卡独立展示区
- **P3**：agent tool、守夜人夜间按项目汇总

## 7. 开放问题

1. 项目要不要图标/颜色？（原型没有，先不做）
2. 删除项目 = 删聚合（引用的对象原样保留）——需要二次确认 UI
3. 对象引用的展示文案：各域自己出 face（zimeiti.topic → 选题标题；
   coding.cwd → 目录名）——引用式的好处，域内自己会说话

## 8. 实施校准（V1a 落地，2026-08-31）

与上文设计稿的偏差，以代码为准：

- **分期提前**：agent tool（原 P3）提前进 V1a——`project.create` /
  `project.open` / `project.current` / `project.attach` 已注册
  （`sidecar/src/yibao_brain/project_tools.py`），人与 agent 同权操作项目
  （检验清单第 4 条提前兑现）。
- **Project 增加 `dir` 字段**：立项即落目录骨架
  `data_dir()/projects/<slug>/{01_素材,02_工程,03_导出,04_文档}`
  （视频文档 §8.1）。目录是项目在文件系统的锚点，视图本质不变。
- **L2/L3 分级映射**（重要）：设计稿里写的"L2 按印"，代码里必须落
  `RiskLevel.L3_HIGH`——GatePolicy 对 ≤L2 是自动执行，只有 L3 弹人工
  确认卡。`project.create` 已按 L3_HIGH 注册。今后设计文档凡写
  "L2/L3 按印"处，实现一律 L3_HIGH。
- **IPC 实装名**：查询是 `projects`（不是 `project_list`）；变更广播
  `{"type":"projects", current, projects}`；id 格式 `proj_xxx`。
- **UI 校准**：项目卡不止 §5 的 ctx peek——家态装配零件 `HomeProject`
  （项目名 + S0–S8 九段进度轨 + 下一步 + 待确认数）已注册进 parts.ts，
  暂只入 field 预设 shelf。进度轨推导规则（objects 引用类型 → 阶段）
  与待确认数（恒 0）是诚实占位，**V1b 接阶段模型与确认队列后替换**。
  地平线 ctx 已读项目名（`项目名 · state`；无项目回落 `home · state`）。
- **立项相变实装**：zimeiti 选题详情卡「立项」→ schema 卡 action →
  `panel_action` IPC → api.toml intent `zimeiti.promote` → agent 流程 →
  `project.create`（L3 闸门卡按印）→ agent 回写 `topics.project_id`
  并把选题挂进项目 objects（`{type:"zimeiti.topic", ref:选题id}`）。
  选 intent 通道的原因：它能过闸门且能带数组型 objects 参数；
  裸 IPC `project_create` 无闸门，只留给前端人工操作。
- **仍未做**（开放）：项目选择器/新建 UI（specimen 头部的"新建 +"）、
  会话与素材归属过滤（P2）、守夜人按项目汇总（P3 残余）。
