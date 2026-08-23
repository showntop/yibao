"""run_metrics 链路：usage 抓取、定价计算、聚合进 final_reply payload。"""
import asyncio

from yibao_brain.audit import AuditLog
from yibao_brain.history import ConversationHistory
from yibao_brain.ipc import RiskLevel
from yibao_brain.llm import FakeProvider, Usage
from yibao_brain.loop import AgentLoop
from yibao_brain.memory import FakeMemory
from yibao_brain.pricing import compute_cost, price_for
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.tools import EchoTool, ToolRegistry


def build_loop(tmp_path, provider, history):
    reg = ToolRegistry()
    reg.register(EchoTool())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        history=history,
    )


def test_usage_aggregated_into_final_reply_payload(tmp_path):
    """FakeProvider 流式末尾 usage chunk → loop 聚合进 final_reply.payload.metrics。"""
    provider = FakeProvider(
        text="你好",
        usage={"prompt_tokens": 120, "completion_tokens": 30, "cached_tokens": 50, "total_tokens": 150},
    )
    loop = build_loop(tmp_path, provider, ConversationHistory(tmp_path / "h.json"))

    async def go():
        events = []
        async for e in loop.arun("你好"):
            events.append(e)
        return events

    events = asyncio.run(go())
    fr = next(e for e in events if e.kind == "final_reply")
    m = fr.payload["metrics"]
    assert m["prompt_tokens"] == 120
    assert m["completion_tokens"] == 30
    assert m["cached_tokens"] == 50
    assert m["total_tokens"] == 150
    assert m["elapsed_ms"] >= 0
    assert m["cost"] is None  # FakeProvider 无 model → 未知模型不显示费用
    # 不产生额外事件：最后事件仍是 final_reply
    assert events[-1].kind == "final_reply"


def test_metrics_multiple_tool_turns_accumulate(tmp_path):
    """工具轮多次 LLM 调用：usage 累加（2 次调用 60+60 prompt）。"""
    from yibao_brain.llm import ToolCall

    # 第一轮调 echo 工具（带 usage 50/10），第二轮纯文本回复（带 usage 70/20）
    class _Seq:
        def __init__(self):
            self._n = 0
            self._first = FakeProvider(
                tool_calls=[ToolCall(id="t1", tool_id="echo", params={"text": "hi"})],
                usage={"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            )
            self._second = FakeProvider(
                text="已回显",
                usage={"prompt_tokens": 70, "completion_tokens": 20, "total_tokens": 90},
            )

        def chat(self, messages, tools=None, timeout=None):
            self._n += 1
            src = self._first if self._n == 1 else self._second
            return src.chat(messages, tools, timeout)

        async def astream(self, messages, tools=None):
            self._n += 1
            src = self._first if self._n == 1 else self._second
            async for d in src.astream(messages, tools):
                yield d

    loop = build_loop(tmp_path, _Seq(), ConversationHistory(tmp_path / "h.json"))
    events = list(loop.run("转圈"))
    fr = next(e for e in events if e.kind == "final_reply")
    m = fr.payload["metrics"]
    assert m["prompt_tokens"] == 120  # 50 + 70
    assert m["completion_tokens"] == 30  # 10 + 20
    assert m["total_tokens"] == 150


def test_pricing_known_model():
    """已知模型按 (输入/输出/缓存命中) 每 1M 元计算。"""
    p = price_for("glm-4.6")
    assert p == (0.6, 2.0, 0.6)
    # 100K 未命中输入 + 50K 命中缓存 + 20K 输出
    cost = compute_cost(
        "glm-4.6",
        Usage(prompt_tokens=150_000, completion_tokens=20_000, cached_tokens=50_000, total_tokens=170_000),
    )
    assert cost == 0.13  # (100k*0.6 + 50k*0.6 + 20k*2.0)/1e6


def test_pricing_unknown_model_is_none():
    """未知模型 → None（前端不显示费用，避免误导）。"""
    assert compute_cost("glm-nonexistent", Usage(prompt_tokens=10, completion_tokens=10)) is None
    assert price_for("org/glm-4.6") == (0.6, 2.0, 0.6)  # 带 org 前缀也命中


def test_usage_from_openai_shape():
    """_usage_from_openai 兼容 OpenAI SDK 对象/dict/缺省。"""
    from yibao_brain.llm import _usage_from_openai

    u = _usage_from_openai({"prompt_tokens": 10, "completion_tokens": 5, "cached_tokens": 3})
    assert u.prompt_tokens == 10 and u.cached_tokens == 3 and u.total_tokens == 15
    assert _usage_from_openai(None).total_tokens == 0
    # prompt_tokens_details 里的 cached（OpenAI 新结构）
    u2 = _usage_from_openai({"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 7}})
    assert u2.cached_tokens == 7
