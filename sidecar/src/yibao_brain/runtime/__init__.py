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
