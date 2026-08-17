# 第二步 + P2 督导 + R4 会话墙 spec（P1 接管续篇）

日期：2026-08-17 ｜ 前篇：specs/2026-08-16-p1-takeover.md ｜ 依据：docs/research/2026-08-16-yibao-collab-patterns.md（P2/P4 + 落地路径二三步）

## 范围

1. **L3 会诊上移**：coding 审批统一走 L2 confirmation 体系（PanelApp 确认条 / HomeFeed 收件箱），iframe 审批卡变只读镜像
2. **逃生口「问团子」**：takeover 中点团子 avatar → 对话浮层内 mini 输入行，直问译宝大脑（带面板 focus 上下文）
3. **接管视觉打磨**：chip 文案等小修
4. **P2 督导**：coding 会话状态汇报（Feed 任务卡 + 宠物气泡 + running_tasks）、后台任务（面板关闭照跑 + 完成汇报）、steer（任务卡点击打开面板接管该会话；大脑可调 coding.send 追加指令）
5. **R4 会话墙**：coding 第二面板 `coding:wall`（schema list）——多会话总览（状态/cwd/prompt/时间），行动作「接管」「停止」

## 关键设计决策（侦察依据）

- **后台存活零改造**：runner 是 sidecar 内线程（coding.py:100-135），面板关闭只是 win.hide（lib.rs:1388），runner 照跑；多会话并发无全局锁，_SESSIONS/_PERM 按 sid/rid 键控
- **审批统一走 confirmation_needed**：coding.py 的 SDK can_use_tool 回调（_runner.py:30-61）改造——发 confirmation_needed（action.id=rid，skill="coding"，label=工具名，desc=摘要，surface="panel:coding"）+ 注册 future 等 confirm_batched（server.py:1454-1473 通道复用）；裁决后 emit brain-event {kind:"action_result", id:rid} 让各 UI 出队；保留 60s 超时 deny 兜底与 coding.decide 备用直调（幂等，先到先得）；非 takeover surface（peek/大窗内嵌）裁决同样落入 HomeFeed 收件箱，**全模式单一裁决点零脑裂**
- **iframe 审批卡只读化**：全模式不再可点，文案「等待审批…在确认条或收件箱处理」；decidePerm 保留但不再被 UI 调用
- **remember 复选框对 coding 隐藏**：remember 机制依赖 invoker.apply_verdict，coding 审批不经 invoker，显示会误导；前端 canRememberSkill 特判 skill==="coding"→false
- **任务汇报照抄 agents 先例**：coding 会话终态（done/error/stopped）→ `ctx.emit_event({"kind":"reminder", text, "task":{id:session_id, status, label, prompt}, "plugin":"coding"})` → Feed kind=task 条目；完成/失败走宠物气泡（kind:"reminder" 通道），stopped 静默落 Feed（不发气泡，防打扰）
- **running_tasks 纳入 coding**：server.py `_running_tasks()`（774-824）扩读 coding sessions 表 status="running" + 内存 _SESSIONS 佐证
- **任务卡点击路由**：HomeFeed 点击行为目前固定 openInChat；meta 带 plugin:"coding" 时改路由——调 coding.attach{session_id}（新 L0 方法）→ sidecar 发 panel 事件打开 coding:chat 且 data.session_id=sid → chat.html init 检测 data.session_id 自动 resumeSession（复用 P1 已有 resumeSession + T4 takeover 自然生效）
- **大脑派生后台任务**：coding.start 加 `background?: bool` 参数——true 时返回结果 panel 字段为 None（loop.py:335-337 判空不开面板），任务卡照常汇报；SYSTEM_PROMPT 补一句「后台/并行编码任务用 background=true」
- **live status**：coding.list 每行加 `live` 字段：内存 _SESSIONS 有 entry→"running"，_PERM 有该 sid 挂起→"waiting"（优先级高于 running），否则 "idle"；成本不落库（留档，wall v1 不显示）
- **会话墙 = schema 面板**：manifest.toml 加 `[[panel]] name="wall" type="schema"`；数据方法 coding.wall_data（L0，panel="coding:wall"）返回 list schema：卡片 title=cwd basename+prompt 前 20 字，subtitle=live 状态+相对时间，行 actions=[接管 coding.attach, 停止 coding.stop]；刷新靠 api.toml refresh 声明（参照现有插件先例）+ 打开时重查；不做实时流式刷新（v1）
- **逃生口**：takeover 时 onPetTap → 打开对话浮层并显示 mini 输入行（单行 input + 发送钮，挂在 thread 层底部）；submit 走「强制大脑」路径（PanelApp.submit 加 forceBrain 分支：pushMsg user + runInput，绕过 isCoding 路由）；非 takeover 时 onPetTap 原行为不变
- **chip 打磨**：takeover 时 chipText 直接用 focus.item.title（「编码对话（运行中）」），不加「在看：」前缀

## 非目标

- Best-of-N tab、统一 review 流、会话成本持久化（留档 R4 后续）
- 会话墙实时流式刷新、拖拽排序
- 宠物气泡主动 steer 对话（气泡只汇报，介入一律打开面板或主框说）

## 验收标准（在 P1 的 A-I 之上追加）

J. takeover 中触发审批（如让 CC 跑需批准的命令）→ PanelApp 顶部确认条出现（无 remember 复选框），批准/拒绝后 CC 继续；iframe 卡片只读；裁决后确认条自动消失
K. 面板关闭期间发起的编码会话继续跑；完成后主屏 Feed 出现任务卡（状态徽章正确），完成/失败有宠物气泡，stopped 无气泡
K2. 点任务卡 → 打开 coding 面板并恢复该会话上下文，可直接接着聊（takeover 生效）
L. takeover 中点团子 → 浮层 mini 输入行，提问走译宝大脑（知道 coding 面板上下文），回复进浮层时间线；不打断 coding 会话
M. 会话墙：打开 coding:wall → 列出全部会话（运行中/等审批/空闲状态正确）；行内「接管」打开 chat 恢复会话；「停止」中断运行中会话
N. 大脑后台任务：对团子说「后台帮我改 X」→ 不开面板静默执行，完成有汇报（依赖模型路由，允许人工复核日志）
O. 全部既有验收（P1 A-I）不回归；sidecar pytest 全绿（含新增审批链/状态字段测试）
