# 感知 v2（B 源屏幕内容 + 首个消费闭环）设计

日期：2026-08-02  关联：`docs/research/2026-07-27-perception-design.md`（§2 B 源/§4 出站点/§5 生命周期/§6 信任架构）、`2026-07-28-activity-recall-tool-design.md`（load_user_activity 模式）

## 1. 目标与范围

按感知 roadmap 落地 v2 眼睛：事件驱动采集屏幕内容（a11y 树文本优先、截图视觉概括兜底），三层过滤与独立出站闸门齐备，透明（日志页 + 状态灯）到位，并以 `load_screen_content` 工具完成首个消费闭环（「那个报错是什么」「我刚才看的页面讲了什么」）。

**消费方绑定**（设计纪律：每个源必须挂消费方）：B 源 → `load_screen_content`（LLM 按需加载屏幕内容上下文）。

**不在本轮**：晨间反刍、主屏时间线、Distiller 模式提炼进 mem0、记忆巩固 job（数据量未达门槛）、watch 主动建议与 B 源合流（watch 截屏建议仍是旁路）。

## 2. 采集（B 源 sensor）

- **触发**：复用 A 源 5s 轮询循环——(app, title) 变化即产生事件；同一画面超过 5 分钟产生一次心跳事件（防「一直看同一页什么都不记」）。不新建事件基础设施。
- **每次事件的内容决策**：
  1. 抓前台 a11y 树 → 序列化为紧凑文本（截断 4KB）→ 非空则存 `kind='tree'`（免费、可检索、**永不出站**）；
  2. 树为空/不可用（自绘 UI）→ 截图 → GLM 视觉概括（≤80 字，概括 prompt 聚焦「前台应用在显示什么内容」）→ 存 `kind='vision'`（概括文本），原图按生命周期 24h 删除。
- **预算闸**：screen 事件 ≤120/天；其中 vision 概括 ≤30/天。超限丢弃当次事件（感知是增强面，不许失控）。
- **存储**：复用 observations.db（`source='screen'`，`kind='tree'|'vision'`，`sensitivity='S3'`），payload 走现有 Fernet 字段加密；tree/概括文本留存 7 天，截图原图 24h（存文件路径于 payload，清理 daemon 一致性检查）。

## 3. 三层过滤（§6.3 照单）

1. **应用黑名单**：内置 `com.1password.1password`、钥匙串访问、常见银行/支付类（settings 键 `perception.blacklist` 可增删 bundle id）；命中即整段不采（树与截图都不采）。
2. **隐私窗启发式**：窗口标题/AX 属性命中主流浏览器（Chrome/Safari/Edge/Arc）的无痕/隐私浏览标记 → 不采。
3. **secure input 与敏感内容**：secure input 激活时当帧弃；vision 概括文本落库前过敏感正则（密码/卡号/证件号模式）命中即删不存。

## 4. 出站与授权

- **写路径闸门**：`perception.screen` 独立开关（默认 false）。开启时前端弹一次明示对话：「屏幕内容将被持续观察；a11y 树文本只存本机，无法读取界面结构时的截图会发送给智谱 GLM 做概括」。确认后才落 `perception.screen=true`。
- **出站点**：唯一的出站是兜底截图 → GLM 概括。a11y 树文本不出站。
- **读路径**：`load_screen_content` 返回内容进主 LLM 上下文，沿用 `perception.model_access` 独立授权（A/C 同款，未开启时拦截并引导去设置），单窗上限 24h/20 条。

## 5. 透明

- **感知日志页**：新增「屏幕」来源条目（S3 徽章）：tree 条目显示树文本首行摘要、vision 条目显示概括文本；单条删除/按 app 删除/清空沿用现有机制。
- **团子「观察中」微态**：任一感知采集开关激活时天线灯为常亮青白小点（不动画不抢眼），全部暂停即灭。新增 state 不进九态主循环，只做叠加指示。

## 6. 消费工具（首个闭环）

`load_screen_content`（L0 底座工具，load_user_activity 同构）：

- 参数：`minutes`（默认 30，上限 1440）/ `limit`（默认 10，上限 20）。
- 行为：model_access 未开 → 返回引导错误（同 load_user_activity 拦截模式）；返回最近窗口内的 screen 观察（ts/kind/text/app），按时间倒序。
- 敏感边界：完整内容只进当前模型轮次；audit/history/事件只存安全摘要（复用 load_user_activity 的摘要钩子与 notice「已参考最近活动」）。

## 7. 工程与验证

- sensors 挂进现有 perception 线程同一循环；settings 即时生效（`_settings_signature` 模式）；清理 daemon 支持 screen 留存与原图一致性检查。
- 测试：pytest——三层过滤各层、预算闸、tree/vision 分流、加密落库、消费工具闸门与窗口语义、状态灯设置逻辑；前端 vue-tsc/build；cargo check。
- 真机验收：开 `perception.screen`（弹明示）→ 切 3-4 个 app（含一个自绘 UI）→ 日志页出现 tree/vision 条目 → 问「我刚才看的页面讲了什么」→ 答案引用概括内容 → 黑名单 app 无条目。

## 8. 非目标

晨间反刍/时间线/Distiller/巩固 job（感知 roadmap v3+）；B 源与 watch 主动建议合流；SQLCipher/Touch ID（v2 加密增强）；E/D 源。

---

## 实装记录（2026-08-02）

- **已实现**：B 源采集（变化/300s 心跳触发，a11y 树文本优先、截图 GLM 概括兜底）；三层过滤（内置+可配黑名单、浏览器隐私窗启发式、secure input 弃帧 + 概括敏感正则）；日预算（120 事件/30 概括）；observations.db 加密落库（S3，7 天留存）；截图帧即清（过滤/概括后删原图堵明文残留）；敏感概括丢弃也去抖。
- **已实现**：`perception.screen` 独立开关（默认关，行内两段确认明示 GLM 概括出站）；`perception.blacklist` 可配；`load_screen_content` L0 消费工具（minutes/limit 收敛、model_access 拦截、safe_result 摘要、notice「已参考屏幕内容」）；感知日志「屏幕」徽章（vision 前缀「概括 ·」）；团子观察中青白叠加点（不占 state 通道）。
- **真机验收（2026-08-02 通过）**：开关明示/状态灯亮灭、Safari/系统设置/VS Code tree 条目与 Excalidraw vision 概括、1Password 黑名单无条目、「我刚才看的页面讲了什么」引用屏幕内容并带 notice、model_access 关闭拦截引导、关闭后无新条目——7 步全过。
- **自动验证**：sidecar 733 passed；vue-tsc + vite build、cargo check 全绿。
- **backlog**：sampler 先判去抖再采样（IO 优化）；状态灯小尺寸辨识度微调；日志「概括 ·」前缀观感。
