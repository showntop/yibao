# 运行中会话 steer + 督导补遗 spec（P2 收尾）

日期：2026-08-20 | 前篇：specs/2026-08-17-p2-supervisor-wall.md（差距分析：8 条落地 7 条）| 依据：roadmap `docs/plan/2026-08-20-roadmap-r5.md` B1

## 背景

P2 督导基本落地（审批统一、终态汇报、任务卡路由、background 参数、live 字段、rail 接替墙）。剩余实质缺口一个：**大脑/用户对 running 会话无注入通道**——`coding.send` 对 running 会话硬拒（`coding.py:532-535`「会话正在运行中，请先中断或等待完成」）。B2 并发对话落地后，多会话并行 + 运行中 steer 是督导模式的最后一块。

另有两件小补遗：running_tasks 不含 waiting 语义；底座重启后陈旧 running 会话无对账（`coding.py:86-88` 留档）。

## 关键设计决策

### A. steer 队列（核心）

**语义：对 running 会话的 send 不再拒绝，转为排队；当前轮次收尾后自动接续，同会话同引擎 resume。**

- `SendSkill.run`（`coding.py:532-535`）：会话 running 时不拒绝，把 prompt 追加到该会话的 steer 队列（内存，挂 `_SESSIONS[sid]` entry，仿 `mode_pending` 模式 :953-957），返回 `{queued: true, position: N}`（L0 语义不变——send 本身本来就异步）。
- `_stream`（或 runner 收尾处）：一轮 done 后、流终结前查 steer 队列——非空则取出**全部排队消息合并**为一条新 prompt（多条按序拼接，标「【督导补充】」前缀），以 `resume_session_id=cc_sid` 原地续跑；期间新到的 steer 继续入队，逐轮 drain 直到队列空才真正落终态（`_report_final` 只在队列空时触发）。
- **打断语义**：`coding.stop` 连 steer 队列一起清（停止=全停，不留尾巴）；`interrupted` 事件照旧。
- **事件可见性**：steer 入队/消费各发一条 marker 事件（仿 resume fallback 的 `_persist("marker",…)` 先例），面板流可见「已排队，本轮结束后发送」/「督导补充已接续」。面板 Composer 对 running 会话可同样放开（输入即排队），UI 态由 marker 驱动，不新增复杂状态机。
- 并发安全：队列操作与 `_SESSIONS` 同锁域（现状 entry 级操作模式照抄）。

### B. running_tasks 补 waiting（server.py:903-928）

`_running_tasks()` 读 coding sessions 时，对 `_PERM` 有挂起项的 sid 在条目加 `waiting: true`（只读合并 `_coding_perm_registry()` 先例 :285-310），壳端「正在跑」计数/展示可区分「等审批」。壳侧若现状不消费 waiting 则零改动（additive 字段）。

### C. 陈旧 running 对账（coding.py:86-88 留档）

插件加载（`make_tools`）时一次性对账：sessions 表 `status="running"` 但内存无活体 → 改 `status="interrupted"` + messages 落一条 marker「底座重启，会话中断，可 send 续跑」。仿 agents 插件 pid 对账先例（`agents.py` docstring 所述），但 coding 是 in-process，判据就是「内存无 entry」而非 pid。

## 非目标

- 轮次中途硬注入（打断当前 turn 立即插入）——steer 是轮间接续，不打断正在跑的 turn
- 面板 Composer 排队态的精细 UI（v1 靠 marker 可见即可）
- 跨会话批量 steer（广播指令）

## 测试

- sidecar pytest：running send → queued 返回；done 后自动接续（断言二次 run 的 prompt 含【督导补充】+ resume_session_id 正确）；多条 steer 合并；stop 清队列不续跑；`_running_tasks` 带 waiting；陈旧 running 对账（造 running 行无 entry → interrupted + marker）。
- panel vitest：Composer 对 running 会话可输入（如改动）；marker 渲染沿用既有路径。

## 验收标准

A. coding 会话 running 中，大脑调 coding.send（或面板输入）→ 返回 queued，不当轮打断；当前轮 done 后自动以原文接续（同会话 resume），面板流见排队与接续 marker
B. stop 后 steer 队列清空，不发生「停了又自己跑起来」
C. 底座重启后旧 running 会话显示 interrupted，可直接 send 续跑
D. `/v1/state` running_tasks 里等审批的会话带 waiting 标记
E. sidecar pytest 全绿；panel vitest/build/typecheck 全绿
