# 第二步 + P2 + 会话墙 实施 plan（对应 specs/2026-08-17-p2-supervisor-wall.md）

worktree：.worktrees/feat-p1-takeover（分支 feat/p1-takeover，续接 P1 的 8 commit）
执行：波次内可并行（文件不重叠），波次间串行；chat.html 任一时刻只有一个 implementer。

## 关键事实（侦察结论）

- coding runner：进程内 ClaudeSDKClient + 每会话 daemon 线程（plugins/coding/skills/coding.py:100-135）；_SESSIONS dict coding.py:69；面板关闭 runner 照跑
- 审批私有链：SDK can_use_tool → permission_request panel_data + `_PERM[rid].event.wait(60s)` 超时 deny（_runner.py:30-61）；coding.decide 兑现（coding.py:717-725）；stop 时 release_pending_permissions 全 deny 放行
- L2 确认体系：PendingConfirm{id,skill,label,desc,params?,risk?,surface?,tier?}（brain.ts:597-609）；confirmation_needed 入队（brain.ts:656-676）；action_result/error 按 id 出队（:677-686）；PanelApp 只消费 surface.startsWith("panel")（PanelApp.vue:399-406），HomeFeed 收件箱收全部（HomeFeed.vue:458）；sendConfirmBatch → server.py:1454-1473 兑现 future/early_answers；remember → invoker.apply_verdict 写 session_allowed（对 coding 无效→前端特判隐藏）
- 主动推送：ProactiveDispatcher.emit——panel_data 直送 shell；其它 kind 先 feed.add（有 task meta 则 kind="task"）再 brain-event 广播；kind=="reminder" 走宠物气泡+TTS（proactive.py:62-85，level 门控）；agents 完成播报先例 plugins/agents/skills/_common.py:95-99
- running_tasks：server.py:774-824 只读 agents tasks 表 + background_jobs；HomeFeed 点击固定 openInChat（HomeFeed.vue:364-385）
- 面板体系：多 [[panel]] 先例（zimeiti 5 个）；panel ref=pid:name；api.toml panel 字段校验须指向已声明面板（plugins.py:501-507）；schema list 组件=卡片+title/subtitle+行 actions（SchemaPanel.vue:38-122）；行 action 触发 panelAction，params 支持 $data/item 绑定
- 大脑工具：coding.start 已是 L1 Skill（coding.py:770-774）Gate AUTO；StartSkill 返回 {session_id, panel:"coding:chat"}，result.panel 非空 → loop.py:335-337 发 panel 事件开面板
- chat.html resumeSession(sid) 已存在（P1）；handleInitData(data, msg) 已二参化

## 波次

### B1（sidecar：审批统一 + 状态汇报；文件 coding.py / _runner.py / server.py / loop.py / 测试）
1. _runner.py can_use_tool 回调改造：构建 confirmation action `{id:rid, skill:"coding", label:tool, desc:摘要(command/path 截 80 字), surface:"panel:coding", risk:"L1"}`，经 ctx.emit_event 发 `{"kind":"confirmation_needed","actions":[action]}`（对齐 loop.py:309-313 攒批格式）；等待改为 future/event 双模式——仍等 `_PERM[rid].event`，但 confirm_batched 通道也要能 set 它：server.py:1454-1473 兑现 future 时除原逻辑外，若 cid 以 "perm_" 开头则路由 `_PERM[cid]["allow"]=approved; event.set()`
2. 裁决后 coding 侧补出队事件：_runner 拿到结局（allow/deny/超时）发 permission_done（已有）+ emit `{"kind":"action_result","id":rid,"status":"ok"/"error"}` 让确认条/收件箱出队
3. stop 时 release_pending_permissions 除 event.set 外同样补 action_result 出队
4. 会话终态汇报：done/error/stopped 时 emit `{"kind":"reminder" if done/error else "event", text, "task":{"id":sid,"status":"完成"/"失败"/"已停止","label":prompt 前 30 字,"prompt":prompt}, "plugin":"coding"}`；done 的 text 带成本（usage 在 done 事件里，_runner.py:119-130）
5. server.py `_running_tasks()` 追加 coding running 会话（_SESSIONS 键 + sessions 表 running 对账）
6. coding.list 每行加 `live`：_PERM 有该 sid 挂起→"waiting"，_SESSIONS 有 entry→"running"，else "idle"
7. coding.start 加 `background?: bool`：true 时返回 {session_id, panel: None}；loop SYSTEM_PROMPT 补「后台/并行编码任务 coding.start background=true」一句
8. 测试：审批 confirmation 通道（发事件/future 兑现/超时 deny/出队事件）、live 字段、background 参数、终态汇报事件形态。`uv run --extra dev pytest -q` 全绿

### B2（app：审批只读化 + 逃生口 + 打磨；文件 chat.html / PanelApp.vue / brain.ts，与 B1 并行）
1. chat.html 审批卡只读化：appendPermission 不再渲染可点按钮，卡面文案「⏳ 等待审批…在顶部确认条或主屏收件箱处理」；decidePerm/按钮监听删除或惰性化（保留 DOM 结构供 permission_done 收敛）；非 takeover 同样只读（裁决统一走 confirmation）
2. brain.ts canRememberSkill：skill==="coding" → false（remember 复选框对 coding 审批不显示）
3. PanelApp 逃生口：takeover 时 onPetTap → 打开对话浮层 + 浮层底部 mini 输入行（单行 input + 发送钮，ref askText）；mini submit → `submitBrain(text)`：pushMsg("user") + runInput（复用原大脑路径，带 focus）；非 takeover onPetTap 原行为；浮层关闭时 mini 输入行一并收起
4. PanelApp chip 打磨：isCoding 且 chipText 时直接用 focus.item.title（不加「在看：」前缀）
5. 验证：vue-tsc / vitest / chat.html node --check

### B3（attach 接管链路；依赖 B1 的 task meta；文件 coding.py / server.py(如需) / chat.html / HomeFeed.vue / brain.ts）
1. coding.py 加 `attach`（L0）：{session_id} → 校验存在 → 经 panel payload 通道发 panel 事件打开 coding:chat（plugins.py:361-375 组装先例），data={session_id}；api.toml 注册（panel="coding:chat"）
2. chat.html handleInitData：data.session_id 且 != currentSession → 自动 resumeSession(data.session_id)（防重入：resumeSession 进行中标志）
3. HomeFeed 点击路由：item.meta.plugin==="coding" → openCodingSession(meta)（调 panelAction/invoke coding.attach）替代 openInChat；其它 meta 原行为
4. 验证 + chat.html node --check

### B4（会话墙；依赖 B3 的 attach；文件 manifest.toml / api.toml / coding.py / 插件页入口）
1. manifest.toml 加 `[[panel]] name="wall" type="schema" label="会话墙"`；api.toml 加 `coding.wall_data`（L0，panel="coding:wall"，refresh 声明参照现有先例）+ `coding.attach` 已注（B3）
2. coding.py `wall_data`：返回 list schema——每会话卡片 title=`{cwd basename} · {prompt 前 20 字}`，subtitle=`{live 状态文案} · {相对时间}`，行 actions=[{label:"接管",method:"coding.attach",params:{session_id}},{label:"停止",method:"coding.stop",params:{id}}（仅 live=="running"/"waiting" 显示）]；schema 结构对齐 SchemaPanel list 组件字段（先看 zimeiti/forge 的 list 先例）
3. 插件页入口：参照「面板级入口：插件页子入口直调此 api 方法打开」先例（zimeiti.mat_list）加 coding.wall 入口（读现有入口声明机制，可能在 manifest 或插件页 UI 配置）
4. 测试 wall_data 形态 + 验证

### B5 终审 + 修复波
- 全量静态审（spec J-O 逐条）+ 全部自动化 + diff 卫生 + 修复波

## 风险备案

- confirmation_needed 走 ProactiveDispatcher.emit 广播时 kind 字段原样透传，前端 brain.ts:656 按 kind=="confirmation_needed" 入队——B1 需核实 emit 的事件名映射（proactive.py 非 panel_data 分支）
- 大脑发起 coding.start 时 Gate AUTO 免确认已是现状；background=true 只是不开面板，安全语义不变
- approval 双通道幂等：confirm_batched 与 coding.decide 都能 set _PERM[rid]，先到我胜，event.set 幂等
- iframe 审批只读后，permission_done 收敛路径（卡片状态翻转）必须保留——只去按钮，不去卡片
- sessions 表陈旧 running（重启后 cancel 丢失）在 live 字段下显示 running 但无实际线程——沿用 StopSkill 既有兜底思路，wall「停止」可对陈旧会话补发 stopped
