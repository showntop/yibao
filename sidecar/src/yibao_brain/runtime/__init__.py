"""serve_async 闭包域宿主包（R-13 第二步）：工具域 helpers / mobile 域 / voice 域 / runs 调度域。

纪律（同 bridge.py/transport.py 先例）：各域函数从 server.serve_async 原样搬运，不改逻辑；
改行为请另开 commit。共享状态不设模块级全局——RuntimeCtx 实例由 serve_async 构造并注入，
域函数以 ctx.<attr> 访问。例外：_run_ctx（contextvars.ContextVar）保持 serve_async 局部、
传原对象（任务级隔离语义，收进 ctx 实例属性会丢并发隔离）。
"""


class RuntimeCtx:
    """serve_async 共享状态容器：serve_async 构造、按装配顺序逐段填充。

    简单可变类（非 dataclass）：字段随域拆分逐步增长；各域函数只访问自己依赖的字段。
    """

    def __init__(self) -> None:
        self.agent = None  # AgentLoop（loop.py）
        self.feed = None  # FeedStore（feed.py）
        self.settings = None  # 用户设置共享 dict（config.load_settings 的同一可变字典）
        self.write_msg = None  # 分接头（EventTap）：serve_async 内 write_msg 已重绑为 tap
        self.run_slots = None  # per-会话槽位表 dict[str, dict]（同槽抢占/跨槽并行）
        self.pending_confirms = None  # 确认多槽：cid -> asyncio.Future
        self.early_answers = None  # 早到确认缓存：cid -> (approved, remember)
        self.confirm_meta = None  # 确认元数据：cid -> {tool_id, summary, ...}（手机 /v1/state）
        self.confirm_done = None  # 已处理确认 deque（跨端防重）
        self.drive_run = None  # runs 调度域：run 驱动（serve_async 闭包，第四步迁 runtime/runs.py）
        self.schedule_run = None  # runs 调度域：受理尾巴（同槽抢占/跨槽并行）
        self.preempt_current = None  # runs 调度域：槽内抢占（preempt_gen+1 + cancel）
        self.ai_loop = None  # asyncio 事件循环（run_in_executor/ensure_future 用）
        self.voice = None  # 语音栈实例（voice.py build_voice；None = 语音不可用）
        self.stream_agent = None  # run 流：_stream_agent（暂留 serve_async 的闭包）
