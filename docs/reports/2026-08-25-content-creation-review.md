# 内容创作（zimeiti）插件深审报告

> 日期：2026-08-25 ｜ 范围：plugins/zimeiti 全量（数据模型/交互/信息结构/体验/创作质量）
> 方法：静态审查（manifest/api/tools/panel/SKILL/测试）+ 实机验证
> 结果：18 项问题，v1 已修 9 项（见提交 `feat(zimeiti)` 内容创作 v1），余项为 v2 backlog

## v1 已修（2026-08-25 落地）

| 审查项 | 修法 |
|---|---|
| #2 move/set_status 双标（幽灵状态/不留痕） | 声明式 move 退役，MoveTool 代码承接（枚举校验 + published_at + published_version） |
| #4 选题不可编辑 | 新增声明式 `update` 工具（title/angle/platform） |
| #1 返回丢稿 + AI 替换不可撤销 | 编辑器 back dirty 拦截确认；AI 应用前快照 + 状态栏「点此处撤销」 |
| #7 看板→编辑器两跳 | 看板卡片加「写稿」action 直调 open_editor |
| #3 编辑器 AI 与 write SKILL 两套文风 | ai_edit 全模式注入语气规则 _TONE + 选题简报 brief（onInit 带 angle/platform）；标题模式贴合目标平台 |
| #8 draft intent 丢上下文 / 发布不记版本 | intent 加「先 get 拿 angle/platform」；topics 加 published_version（move/set_status/publish 三路径都记） |
| #6 发布链路输出不一致 | publish.py 剪贴板改纯文本（_strip_md，与编辑器小红书路径同语义；公众号富文本留编辑器） |
| #10a content_path 绝对路径 | 改落相对路径，article_read/publish 兼容老库绝对路径 |
| #10b 版本膨胀 | 每选题保留最近 20 版，超出删行删文件 |
| 改名 | 自媒体 → 内容创作（展示层；id=zimeiti 不动，全链路引用） |
| E1 部分 | SKILL.md 平台适配与 _PLATFORMS 对齐（小红书字数/标题/标签规格、公众号结构、知乎）+ 发布前自检清单 5 条；删播客（工具链无此平台） |

## v2 backlog（按影响排序，未实施）

> 2026-08-25 续：#5 / #9 / #11 已落地——delete 级联代码工具（tools/delete_topic.py：稿件行+文件+目录、素材摘关联、发布数据）；
> 详情页「录数据」表单（record 面板 + api record/stat_add，SchemaPanel form 提交加 declared-fields 防串护栏）+ 素材「查看」正文面板（matdoc）；
> 热点并行抓取 + 10min 缓存（失败回退陈缓存）。
>
> 2026-08-25 第二批：#13 / #14 / #16 / #18 已落地——stat_add 代码承接（stat_add.py：同选题同平台同日去重，
> 没传的字段保留旧值；post_stats 加 favorites/shares 列）；get 代码承接（get_topic.py：按 id 查拼 draft/materials 聚合，
> 详情页新增「稿件/素材/链接」行）；topics 加 url 列（热点转选题带原文链接）；详情页冗余「已发布」按钮并入「复制成稿」
> （published_at=0 显 1970 一项查证为已修：schema `|date` 管道对 0/负值返回空串）。余项：#15 / #12 / #17 / A8。

| # | 问题 | 证据 | 建议修法 | 量级 |
|---|---|---|---|---|
| 5 | delete 无级联（稿件文件/素材关联/数据成孤儿） | manifest delete 声明式 | delete 改代码工具：删 articles 行+目录、清 materials.topic_id、删 post_stats | 中 |
| 9 | 录数据/素材关联无面板入口，复盘靠口述 | detail.schema.json 无 stat_add；materials 条目只有删除 | detail 加「录数据」form action（依赖宿主 form 能力确认）；materials 条目加查看/关联 | 中 |
| 11 | 热点串行抓取无缓存（最坏 30s+） | hot_topics.py:146-154 | 并行抓取 + 10min 缓存；转选题 refresh 用缓存 | 中 |
| 14 | 详情页无稿件状态/版本数/关联素材 | detail.schema.json | tool 侧拼装聚合字段（get 带 latest_version/note/字数） | 中 |
| 15 | 8000 字上限把长文踢出编辑器；全文 diff 不可读 | ai_edit.py:_MAX_FULL；editor.html diff | 分段润色队列；diff 折叠未变段落 | 中 |
| 12 剩余 | 平台格式化结构差异（小红书不要 H1 等） | editor.html toPlain/md-styled | 发布格式化按平台调结构 | 中 |
| 13 | post_stats 缺收藏/转发（小红书核心指标）；无去重 | manifest post_stats | 加 favorites/shares 列 + 同平台同日去重 | 小 |
| 16 | topics 无 url 字段，热点转选题丢链接 | manifest topics | 加 url 列，hot_add 带上 | 小 |
| 17 | mat_enrich/defer、invoke_* 零测试 | test_zimeiti.py 无覆盖 | 补测试 | 小 |
| 18 | published_at=0 渲染 1970；详情「已发布/复制成稿」冗余 | detail.schema.json | 空值显「—」；合并动作 | 小 |
| A8 剩余 | materials 无检索（tag/LIKE），content 截 8000 | mat_save.py | 加 mat_search | 小 |
| D8 | publish 仅 macOS（pbcopy 硬编码） | publish.py | 跨平台暂缓（产品暂只 macOS） | 挂起 |

## 审查维度速览

- **数据**：topics/articles/materials/post_stats 四表；v1 修 published_version、相对路径、版本治理
- **交互**：主流水线 录选题→看板→写稿→版本→发布→复盘；v1 修最高频断点（看板直达）与信任级问题（丢稿/撤销）
- **信息结构**：board=首页/editor=主工作面格局明确化；v2 处理 detail 信息薄与素材关联可见性
- **体验**：v1 修丢稿保护/AI 撤销/发布一致；v2 处理长文与 diff 可读性
- **创作质量**：v1 统一文风（_TONE 单源注入 ai_edit）+ 平台规格加深 + 自检清单；v2 处理长文分段润色与平台结构格式化
