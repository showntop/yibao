# 守夜人（夜间内容流水线）调研：质量保障与开源生态

日期：2026-08-25 · 背景：睡前布置 → 夜里自动跑「抓热点 → 选题 → 起稿」→ 早上晨报验收（个人 IP / 中文自媒体方向）

## 一、最值得看的开源项目

- **AIWriteX**（github.com/iniwap/AIWriteX）——中文圈最贴近本场景：CrewAI 多智能体全流程（热点雷达→生成→排版→发布）。可借鉴：专家赛道配置注入全链路、成稿自动体检回环（钩子/节奏/AI 味打分不达标自动修）、「五个旋钮」临时调参。
- **Stanford STORM / Co-STORM**（github.com/stanford-oval/storm）——两阶段分离（研究+大纲 / 逐节写作）、多视角专家互访深挖选题、分段可换模型（研究用便宜模型、成稿用强模型）。
- **GPT Researcher**（github.com/assafelovic/gpt-researcher）——多源并行抓取 + 来源加权 + 每个论点挂 citation，选题「为什么选它」可审计。
- **DailyHotApi**（github.com/imsyy/DailyHotApi）——50+ 源热榜聚合 API，JSON/RSS 双模，可做热点数据源层；RSSHub 备选，MediaCrawler 兜底对标账号采集。
- **ai_trending / AI-Daily / HotPush**——晨报/日报生成类 agent：LLM 观点 agent（不只罗列）、RSS 为主爬虫兜底的降级、规则过滤前置去噪。
- **Wechatsync**——浏览器扩展做发布通道绕开无官方 API 的平台（二期数据回流可借鉴）。
- **xhs-writer-skill**——把平台形态（竖版卡片/话题标签）做成 skill 资产的参考。

## 二、商业产品的质量控制设计

- **Jasper**：Brand Voice（上传既有内容训练品牌语气）——选题/写作都应带账号画像上下文。
- **5118**：「反向选题」——从已验证爆款反推需求，比从热榜正推命中率高。
- **易撰/句无忧**：成稿三检（原创度+违禁词+权重预估），违禁词要分平台分行业词库。
- **Hypefury**：发布后数据决定 evergreen 重发队列——「发布→数据→反哺选题」飞轮。
- **Typefully**：编辑器即验收界面。

## 三、质量保障模式（经实践验证的）

### 分层防线（确定性优先）
能写代码的绝不用 LLM：字数/平台规格/敏感违禁词/必含来源链接/SimHash 查重，全部硬闸门，不过直接打回重写。

### 选题环节
便宜模型初筛 + 结构化 JSON 输出（选题/理由/来源链接/预估热度），schema 校验当质量门；不过门升级强模型并记 `gate_reason`。

### 起稿环节
Self-Refine 循环（生成→按 checklist 逐条自评→改写，2 轮封顶）；checklist 用 Constitutional AI 式 [{原则, 描述}] 配置化；模型当批评家比当作者强。

### 终稿
强模型 + 引用锚定：关键事实（数字/日期/人名）必须挂来源链接，无法锚定的句子在晨报标「⚠️ 未核实」。

### LLM-as-judge 要点（Monte Carlo 工程总结）
一个 judge 只评一个维度；1–5 整数分档且每档写定义；必须输出理由；看滑动平均趋势不看单点；eval 成本目标 ≤ 业务负载 1:1。

### 回归测试
promptfoo + 中文 rubric（参考 AlignBench 多维单点打分）；积累 30–50 个 golden case（历史好稿/烂稿各半），prompt/模型变更时 CI 跑 `llm-rubric`；主观质量必须用 llm-rubric 不能只靠确定性断言。

### HITL 检查点（4.2M agent 任务实战）
- 三个要避开的失败模式：全量人工审批（99.7% 橡皮图章）、单一置信度阈值（42% 错误发生在置信度>0.9 的输出上）、随机抽样（只抓 11% 错误）。
- 我们的映射：夜间全程 Audit Trail（输入/输出/模型/理由/hash）；早上用户是 Collaborative Drafting（编辑而非批准，质量高 42%）；发布是不可逆动作，Approval Gate fail-closed（无人验收默认不发）。

### 分层模型
cascade 路由：便宜模型先跑，质量门不过才升级；tiering 和 fallback 分开；每次升级记 gate_reason；每个 tier 单独建 golden set。业界可降本 40–85% 无明显质量损失。

## 四、落地清单（按实施顺序）

1. 夜间 pipeline 三步全部留审计日志（输入/输出/模型/理由）。
2. 确定性硬闸门先行：字数、违禁词（分平台词库）、来源链接存在性、SimHash 查重（对热点去重 + 对历史稿防撞车）。
3. 选题用便宜模型出 JSON + schema 门；起稿 Self-Refine 2 轮；checklist 配置化。
4. 晨报每篇附：judge 各维度分数+理由、⚠️未核实标注、来源链接——用户 5 分钟做的是编辑不是从零审。
5. 发布闸门 fail-closed。
6. golden set 从已有 post_stats 的历史稿起步，逐步攒到 30–50 条。
7. 前两周是 rubric/阈值校准期，别第一天就信分数。

## 五、直接可用资产清单（2026-08-25 二次调研，license 已核）

### ⭐ 最大发现：wewrite（github.com/imraywang/wewrite，MIT，极度活跃）
与译宝插件体系几乎同构的现成实现：10 个自包含 SKILL.md（prompt 判断层）+ `wewrite` Python CLI（确定性操作层）+ 状态目录。覆盖：热点抓取（微博/头条/百度）、选题评分+历史去重、文章任务书、主张-证据分离、7 套写作人格、编辑五维审稿（准确/观点/有用/合声/好读，不过就改稿复审）、`wewrite score` 11 项机械检测、18 主题微信排版、公众号草稿箱推送、一稿多发小红书/抖音、阅读数据回填反哺选题、编辑飞轮（学习用户修改）。每篇任务留 brief.yaml/claims.yaml/draft.md/review-report.json 全程可溯源。「prompt 负责判断，Python 负责确定性」与我们架构原则一致。已 FetchURL 亲验 README。

### 按流水线环节
- 抓热点：DailyHotApi（MIT，docker 一行起）／wewrite 自带 hotspots。**直接可用**。TrendRadar（GPL-3.0）可部署不可 fork。
- 选题/起稿/审稿：wewrite skills vendor；baoyu-skills（MIT，小红书卡片/信息图等子 skill 按需挑）。**直接可用**。
- 质量校验：CCCpan/chinese-sensitive-words-mcp（MIT，分平台违禁词词库可抽出）+ pyahocorasick；datasketch（MIT，MinHash 去重）。**直接可用/包装后可用**。
- 发布：公众号走 wewrite 的微信官方草稿 API（MIT 现成）；小红书走 xpzouying/xiaohongshu-mcp（Apache-2.0，Go 单二进制，活跃；注意平台未授权自动化的封号风险，低频+人工确认）；多平台分发建议用户自装 Wechatsync（GPL-3.0，不 fork 只编排）。
- 数据回流：wewrite stats（微信官方数据分析 API）；xiaohongshu-mcp 轮询自有账号数据。

### License 红线
- MediaCrawler：自定义 NON-COMMERCIAL LICENSE，**禁商用**，只能参考。
- TrendRadar、Wechatsync：GPL-3.0，只部署/进程间调用，不 fork 不分发改码。
- 其余均 MIT/Apache-2.0，可自由集成。
