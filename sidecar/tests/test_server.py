import asyncio
import json
import queue
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from yibao_brain.server import serve, serve_async, build_loop
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.ipc import RiskLevel


class _TwoStepProvider:
    def __init__(self, first, second):
        self._first, self._second = first, second
        self._n_chat, self._n_stream = 0, 0

    def chat(self, messages, tools=None):
        self._n_chat += 1
        return self._first.chat(messages, tools) if self._n_chat == 1 else self._second.chat(messages, tools)

    async def astream(self, messages, tools=None):
        self._n_stream += 1
        src = self._first if self._n_stream == 1 else self._second
        async for d in src.astream(messages, tools):
            yield d


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾返回 None 表示 stdin 结束
    return lambda: next(it)


def test_serve_streams_events_and_run_done(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(make_reader([{"id": 1, "type": "run", "text": "hi"}]),
                      use_real=False, db_path=str(tmp_path / "a.db"), provider=provider)
    out = []
    serve(loop, make_reader([{"id": 1, "type": "run", "text": "hi"}]), lambda m: out.append(m))
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "action_result" in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_round_trips_confirmation(tmp_path):
    from yibao_brain.tools import Tool, ToolRegistry
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger", params={})]),
        second=FakeProvider(text="done"),
    )
    inbox = [
        {"id": 1, "type": "run", "text": "做危险的事"},
        {"id": 2, "type": "confirm", "confirmation_id": "x", "approved": False},
    ]
    loop = build_loop(make_reader(inbox), use_real=False, db_path=str(tmp_path / "a.db"),
                      provider=provider, skills_factory=lambda: _registry_with(DangerTool()))
    out = []
    serve(loop, make_reader(inbox), lambda m: out.append(m))
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert "error" in kinds  # 用户拒绝后产出 error
    assert not any(m["type"] == "event" and m["event"].get("kind") == "action_result"
                   and m["event"]["result"]["data"].get("did") for m in out)


def _registry_with(*skills):
    from yibao_brain.tools import ToolRegistry
    reg = ToolRegistry()
    for s in skills:
        reg.register(s)
    return reg


# ---------- serve_async（Plan 4b：流式 + 打断）----------


def _run_async(coro):
    return asyncio.run(coro)


def test_serve_async_streams_events_and_run_done(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="echo", params={"text": "hi"})]),
        second=FakeProvider(chunks=["echoed:", " hi"]),
    )
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "hi"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "action_result" in kinds
    assert "final_reply_chunk" in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


def test_mobile_history_endpoint_payloads_from_real_bucket(tmp_path):
    """（mobile M1）/v1/conversations+/v1/history 的 serve_async 接线读 agent.history：
    build_loop 带 history_file、FakeProvider 真跑一轮 → 两个 payload 闭包产出端点形状。
    serve_async 测试态（use_real=False 默认无 history、HTTP 面关）走不通端到端，
    路由层由 test_http_api fake deps 覆盖——此处测「真 history 桶 → payload」这一段。"""
    from yibao_brain.server import _conversations_payload, _history_payload

    agent = build_loop(make_reader([]), use_real=False, db_path=str(tmp_path / "a.db"),
                       provider=FakeProvider(text="你好呀，我是译宝"),
                       history_file=str(tmp_path / "h.json"))

    async def one_turn():
        async for _ in agent.arun("你好", conversation_id="c1"):
            pass

    _run_async(one_turn())
    # 会话列表：跑过一轮的 c1 桶（user+assistant 共 2 条，preview=末条 assistant 文本）
    assert _conversations_payload(agent.history) == {
        "ok": True, "items": [{"id": "c1", "preview": "你好呀，我是译宝", "turns": 2}]}
    # 单会话回显：role/text 平铺
    assert _history_payload(agent.history, "c1")["items"] == [
        {"role": "user", "text": "你好"},
        {"role": "assistant", "text": "你好呀，我是译宝"}]
    # 无 conversation_id → default 桶（本例无记录 → 空）
    assert _history_payload(agent.history, "")["items"] == []
    # history 未启用（None）→ 空列表而非 503（端点已接线）
    assert _conversations_payload(None) == {"ok": True, "items": []}
    assert _history_payload(None, "c1") == {"ok": True, "items": []}


def test_serve_async_interrupt_stops_run(tmp_path):
    # 慢流式 provider：interrupt 在首 chunk 之前命中 cancel
    provider = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.02)
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "hi"}, {"type": "interrupt"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "interrupted" in kinds
    assert "final_reply" not in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_async_new_run_preempts_old(tmp_path):
    # 第一个 run 慢；第二个 run 到来应打断第一个并正常完成
    slow = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])
    state = {"n": 0}

    class _Switch:
        async def astream(self, messages, tools=None):
            state["n"] += 1
            src = slow if state["n"] == 1 else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"id": 1, "type": "run", "text": "slow"},
                    {"id": 2, "type": "run", "text": "fast"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Switch(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    # 第一个 run 被打断
    assert "interrupted" in kinds
    # 第二个 run 正常完成
    assert "final_reply" in kinds
    dones = [m for m in out if m["type"] == "run_done"]
    assert dones[-1] == {"type": "run_done", "id": 2}


def test_serve_async_cross_conversation_runs_parallel(tmp_path):
    """并发对话 spec §A/验收 A：不同 conversation_id 的两个 run（哪怕跨 surface）真并行——
    不排队、无 notice、互不抢占；快的不等慢的（run_done 顺序 = 完成顺序）。"""
    slow = FakeProvider(chunks=["A", "B"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])

    class _ByText:
        """按用户文本选快慢（并行下任务调度顺序不定，按调用次序选会偶发）。"""

        async def astream(self, messages, tools=None):
            src = slow if "slow" in str(messages[-1].get("content")) else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"id": 1, "type": "run", "text": "slow", "conversation_id": "c-home"},  # 默认 surface=pet
                    {"id": 2, "type": "run", "text": "fast", "surface": "panel:zimeiti",
                     "conversation_id": "c-panel"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_ByText(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    # 谁都不被打断：两个 run 都完整说完
    assert "interrupted" not in kinds
    assert kinds.count("final_reply") == 2
    # 跨会话不再排队：没有「另一个窗口还在说」notice
    assert not any(m["type"] == "event" and m["event"]["kind"] == "notice" for m in out)
    # 快的先收尾（不等慢的说完）——真并行，不是先 1 后 2 的串行
    dones = [m["id"] for m in out if m["type"] == "run_done"]
    assert dones == [2, 1]
    # run_done 带会话归属（spec §E）
    by_id = {m["id"]: m for m in out if m["type"] == "run_done"}
    assert by_id[1].get("conversation_id") == "c-home"
    assert by_id[2].get("conversation_id") == "c-panel"


def test_serve_async_default_slot_cross_surface_still_queues(tmp_path):
    """default 槽（无 conversation_id 的遗留调用）行为同现状：跨 surface 不抢占，
    链式排队 + notice，先来的先说完（并发对话 spec §A 兼容条款）。"""
    slow = FakeProvider(chunks=["A", "B"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])
    state = {"n": 0}

    class _Switch:
        async def astream(self, messages, tools=None):
            state["n"] += 1
            src = slow if state["n"] == 1 else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"id": 1, "type": "run", "text": "slow"},  # 默认 surface=pet
                    {"id": 2, "type": "run", "text": "fast", "surface": "panel:zimeiti"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Switch(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    # 谁都不被打断：两个 run 都完整说完
    assert "interrupted" not in kinds
    assert kinds.count("final_reply") == 2
    # 新 run 的 surface 收到排队提示
    assert any(
        m["type"] == "event" and m.get("surface") == "panel:zimeiti" and m["event"]["kind"] == "notice"
        for m in out
    )
    # 先来的先说完，排队的后说
    dones = [m["id"] for m in out if m["type"] == "run_done"]
    assert dones == [1, 2]
    # 无会话归属的 run_done 不带 conversation_id（兼容旧客户端/旧断言）
    assert all("conversation_id" not in m for m in out if m["type"] == "run_done")


def test_serve_async_same_conversation_cross_surface_preempts(tmp_path):
    """同会话跨 surface（同一 conversation_id 从 pet 和面板同时发话）视为同会话：
    新话顶旧话（spec §核心模型/验收 B）——interrupted 照旧，第二个 run 完整收尾。"""
    slow = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])

    class _ByText:
        async def astream(self, messages, tools=None):
            src = slow if "slow" in str(messages[-1].get("content")) else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"id": 1, "type": "run", "text": "slow", "conversation_id": "c1"},  # 默认 surface=pet
                    {"id": 2, "type": "run", "text": "fast", "surface": "panel:zimeiti",
                     "conversation_id": "c1"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_ByText(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "interrupted" in kinds  # 第一个 run 被同会话新话顶掉
    assert "final_reply" in kinds  # 第二个 run 正常完成
    dones = [m for m in out if m["type"] == "run_done"]
    assert dones[-1] == {"type": "run_done", "id": 2, "conversation_id": "c1"}


# ---------- 并发对话（spec 2026-08-20 §B/§D/§E）----------


def test_targeted_interrupt_keeps_other_conversation_confirm_alive(tmp_path):
    """spec §B/验收 C：A 会话停在确认等待时，B 会话的定向打断只收 B——A 的确认
    future 不被误取消（旧实现 confirmer 绑全局 cancel，打断 B 会连带拒掉 A 的确认）。
    批准后 A 照常执行、完整收尾；confirmation_needed 事件带会话归属。"""
    import queue as _queue

    from yibao_brain.tools import Tool
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    class _Mix:
        """A（文本含「危险」）首轮出 tool_call、确认后次轮出文本；B 慢速流式。"""

        def __init__(self):
            self._danger_prompted = False

        async def astream(self, messages, tools=None):
            joined = " ".join(str(m.get("content")) for m in messages)
            if "危险" in joined and not self._danger_prompted:
                self._danger_prompted = True
                src = FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger", params={})])
            elif "危险" in joined:
                src = FakeProvider(text="A 做完了")
            else:
                src = FakeProvider(chunks=["B1", "B2", "B3", "B4"], delay=0.05)
            async for d in src.astream(messages, tools):
                yield d

    out: list = []
    inbox_q: "_queue.Queue" = _queue.Queue()
    inbox_q.put({"id": 1, "type": "run", "text": "做危险的事", "conversation_id": "conv-a"})
    inbox_q.put({"id": 2, "type": "run", "text": "慢速回答", "conversation_id": "conv-b"})
    confirm_cid = [None]

    def reader():
        return inbox_q.get()

    def writer(m):
        out.append(m)
        if m.get("type") != "event":
            if m.get("type") == "run_done" and m.get("id") == 1:
                inbox_q.put(None)  # A 收尾后结束 stdin
            return
        ev = m["event"]
        if ev.get("kind") == "confirmation_needed" and m.get("conversation_id") == "conv-a":
            # A 在等确认：此刻定向打断 B——不该碰 A 的确认等待
            confirm_cid[0] = ev["confirmation_id"]
            inbox_q.put({"type": "interrupt", "conversation_id": "conv-b"})
        elif ev.get("kind") == "interrupted" and m.get("conversation_id") == "conv-b":
            # B 已被打断：批准 A 的确认 → A 应照常执行（未被 B 的打断误取消成拒绝）
            inbox_q.put({"type": "confirm_batch", "items": [
                {"id": confirm_cid[0], "approved": True, "remember": False}]})

    _run_async(
        serve_async(
            reader, writer,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Mix(),
            skills_factory=lambda: _registry_with(DangerTool()),
        )
    )
    # B 被打断，A 没有
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
               and m["event"]["kind"] == "interrupted" for m in out)
    assert not any(m["type"] == "event" and m.get("conversation_id") == "conv-a"
                   and m["event"]["kind"] == "interrupted" for m in out)
    # A 的确认被批准后真正执行了（确认等待没被 B 的打断取消成拒绝）
    assert any(m["type"] == "event" and m["event"].get("kind") == "action_result"
               and m["event"]["result"].get("success") for m in out)
    # A 完整收尾（run_done 带归属，spec §E）
    assert {"type": "run_done", "id": 1, "conversation_id": "conv-a"} in out
    assert any(m["type"] == "event" and m["event"].get("kind") == "final_reply"
               and m["event"].get("text") == "A 做完了" for m in out)


def test_tts_lock_second_conversation_silent_with_notice(tmp_path):
    """spec §D：物理声道只有一条——A 会话持锁播报时，B 会话的 run 静默不播
    （文字流式照出）+ notice「这段不念了」；不排队、互不掐断。"""
    from fakes import FakeVoice

    slow = FakeProvider(chunks=["甲一。", "甲二。"], delay=0.05)
    fast = FakeProvider(chunks=["乙一。"])

    class _ByText:
        async def astream(self, messages, tools=None):
            src = slow if "慢" in str(messages[-1].get("content")) else fast
            async for d in src.astream(messages, tools):
                yield d

    voice = FakeVoice("你好", stream_delay=0.05)  # 拉长 A 的播报窗口，B 必撞上锁
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "run", "text": "慢速播报", "conversation_id": "conv-a", "tts": True},
                {"id": 2, "type": "run", "text": "快速回答", "conversation_id": "conv-b", "tts": True},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_ByText(),
            voice=voice,
        )
    )
    # B 抢到不锁 → 静默 + notice（带 B 的会话归属）
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
               and m["event"]["kind"] == "notice"
               and m["event"]["text"] == "正在播报另一段对话，这段不念了" for m in out)
    # 只有 A 进了播报（speaking/speaking_done 都属 A），B 无 speaking
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-a"
               and m["event"]["kind"] == "speaking" for m in out)
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-a"
               and m["event"]["kind"] == "speaking_done" for m in out)
    assert not any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
                   and m["event"]["kind"] == "speaking" for m in out)
    # B 的文字流式照出，两个 run 都完整收尾
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
               and m["event"]["kind"] == "final_reply" for m in out)
    assert voice.stream_chunks == ["甲一。", "甲二。"]  # 只有 A 的文本进了播放器


def test_text_run_does_not_tts_by_default(tmp_path):
    """P1-06：文本 run 默认不 TTS——voice 栈可用也不发 speaking、不进播放器、不占锁；
    显式 tts=true 才播（将来的「朗读」入口）。"""
    from fakes import FakeVoice

    voice = FakeVoice("你好")
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "hi", "conversation_id": "c1"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(chunks=["纯文字回答"]),
            voice=voice,
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "final_reply" in kinds
    assert "speaking" not in kinds
    assert "speaking_done" not in kinds
    assert voice.stream_chunks == []  # 没碰播放器


def test_targeted_interrupt_does_not_cut_other_conversations_tts(tmp_path):
    """spec §D/验收 C：B 持锁播报中，A 的定向打断只停 A 自己的 LLM 流——
    B 的播报播完（speaking_done 照出、播放器未见 cancel），不被 A 的打断掐掉。"""
    from fakes import FakeVoice

    provider_a = FakeProvider(chunks=["甲1", "甲2", "甲3", "甲4", "甲5", "甲6"], delay=0.05)
    provider_b = FakeProvider(chunks=["乙1。", "乙2。"], delay=0.05)

    class _ByText:
        async def astream(self, messages, tools=None):
            src = provider_a if "甲" in str(messages[-1].get("content")) else provider_b
            async for d in src.astream(messages, tools):
                yield d

    def _delayed_reader(specs):
        it = iter(specs)

        def _r():
            try:
                msg, delay = next(it)
            except StopIteration:
                return None
            if delay:
                time.sleep(delay)
            return msg

        return _r

    voice = FakeVoice("你好", stream_delay=0.05)
    out = []
    _run_async(
        serve_async(
            _delayed_reader([
                ({"id": 1, "type": "run", "text": "乙会话长播报", "conversation_id": "conv-b", "tts": True}, 0.0),
                ({"id": 2, "type": "run", "text": "甲会话长回答", "conversation_id": "conv-a", "tts": True}, 0.02),
                ({"type": "interrupt", "conversation_id": "conv-a"}, 0.15),  # A 流式中途被打断
                (None, 1.0),  # 留足 B 收尾窗口再关 stdin
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_ByText(),
            voice=voice,
        )
    )
    # A 被打断；B 没有
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-a"
               and m["event"]["kind"] == "interrupted" for m in out)
    assert not any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
                   and m["event"]["kind"] == "interrupted" for m in out)
    # B 的播报完整播完：speaking_done 照出，播放器未见 cancel（A 的 cancel 没碰它）
    assert any(m["type"] == "event" and m.get("conversation_id") == "conv-b"
               and m["event"]["kind"] == "speaking_done" for m in out)
    assert voice.stream_interrupted is False
    assert voice.stream_chunks == ["乙1。", "乙2。"]
    # 两个 run 都收了尾
    assert any(m["type"] == "run_done" and m.get("id") == 1 for m in out)
    assert any(m["type"] == "run_done" and m.get("id") == 2 for m in out)


def test_serve_async_same_surface_run_still_preempts(tmp_path):
    # 同 surface（面板里连续发问）：维持抢占语义——新话顶掉旧话
    slow = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])
    state = {"n": 0}

    class _Switch:
        async def astream(self, messages, tools=None):
            state["n"] += 1
            src = slow if state["n"] == 1 else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"id": 1, "type": "run", "text": "slow", "surface": "panel:zimeiti"},
                    {"id": 2, "type": "run", "text": "fast", "surface": "panel:zimeiti"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Switch(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "interrupted" in kinds
    assert "final_reply" in kinds
    dones = [m for m in out if m["type"] == "run_done"]
    assert dones[-1] == {"type": "run_done", "id": 2}


def test_serve_async_provider_error_emits_error_and_run_done(tmp_path):
    # arun 抛异常（如 provider 400）→ 必须发 error + run_done，不能让前端卡死
    class _Boom:
        async def astream(self, messages, tools=None):
            raise RuntimeError("boom")
            yield  # 让它成为 async generator

    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "hi"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Boom(),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "error" in kinds
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_async_confirm_roundtrip(tmp_path):
    """异步路径 confirm_batch 往返：confirmation_needed 携带 id → confirm_batch 回批 → 拒绝 → error。

    loop 路径的 action.id 是随机生成的，客户端需从 confirmation_needed 事件取 id 再回批；
    这里用 queue + 反应式 writer 模拟（writer 看到事件即把 confirm_batch 推回 reader 队列）。
    """
    import queue as _queue

    from yibao_brain.tools import Tool
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger", params={})]),
        second=FakeProvider(text="done"),
    )
    out: list = []
    inbox_q: "_queue.Queue" = _queue.Queue()
    inbox_q.put({"id": 1, "type": "run", "text": "做危险的事"})
    reacted = [False]

    def reader():
        return inbox_q.get()  # 阻塞读；writer 会推 confirm_batch + None

    def writer(m):
        out.append(m)
        if (not reacted[0] and m.get("type") == "event"
                and m["event"].get("kind") == "confirmation_needed"):
            reacted[0] = True
            cid = m["event"]["confirmation_id"]
            inbox_q.put({"type": "confirm_batch", "items": [
                {"id": cid, "approved": False, "remember": False}
            ]})
            inbox_q.put(None)  # EOF

    _run_async(
        serve_async(
            reader, writer,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            skills_factory=lambda: _registry_with(DangerTool()),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert "error" in kinds
    # 裁决出炉即在流内广播 confirmation_resolved（主流事件，序确定性）：
    # 壳/旁路批准后待批卡即时出队，不等 action_result
    assert "confirmation_resolved" in kinds
    assert kinds.index("confirmation_needed") < kinds.index("confirmation_resolved") < kinds.index("error")
    resolved = next(m["event"] for m in out if m["type"] == "event"
                    and m["event"]["kind"] == "confirmation_resolved")
    assert resolved["payload"]["verdicts"] == {resolved["action_ids"][0]: False}
    assert not any(
        m["type"] == "event" and m["event"].get("kind") == "action_result"
        and m["event"]["result"]["data"].get("did") for m in out
    )


def test_serve_async_confirm_remember_skips_future_prompts(tmp_path):
    """勾选「本会话不再询问」并批准：同技能后续调用免确认直接执行（会话级，不落盘）。"""
    import queue as _queue
    import threading as _th
    import time as _time

    from yibao_brain.tools import Tool
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    class _SeqProvider:
        """按 astream 调用序弹出响应（两轮 run 各两步：tool_call → 收尾文本）。"""

        def __init__(self, responses):
            self._responses = list(responses)
            self._n = 0

        async def astream(self, messages, tools=None):
            i = min(self._n, len(self._responses) - 1)
            self._n += 1
            async for d in self._responses[i].astream(messages, tools):
                yield d

    provider = _SeqProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger", params={})]),
        FakeProvider(text="第一次完成"),
        FakeProvider(tool_calls=[ToolCall(id="t2", tool_id="danger", params={})]),
        FakeProvider(text="第二次完成"),
    ])
    out: list = []
    inbox_q: "_queue.Queue" = _queue.Queue()
    inbox_q.put({"id": 1, "type": "run", "text": "做危险的事"})
    first_confirmed = [False]

    def reader():
        return inbox_q.get()

    def writer(m):
        out.append(m)
        if (not first_confirmed[0] and m.get("type") == "event"
                and m["event"].get("kind") == "confirmation_needed"):
            first_confirmed[0] = True
            cid = m["event"]["confirmation_id"]
            inbox_q.put({"type": "confirm_batch", "items": [
                {"id": cid, "approved": True, "remember": True}
            ]})
            # run(3) 必须等 run(1) 执行完再投——瞬时连投会被同 surface 抢占（rid=1 直接 interrupted）
            def _push_second():
                _time.sleep(0.5)
                inbox_q.put({"id": 3, "type": "run", "text": "再做一次危险的事"})
                inbox_q.put(None)
            _th.Thread(target=_push_second, daemon=True).start()

    _run_async(
        serve_async(
            reader, writer,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            skills_factory=lambda: _registry_with(DangerTool()),
        )
    )
    confirms = [m for m in out
                if m["type"] == "event" and m["event"].get("kind") == "confirmation_needed"]
    assert len(confirms) == 1  # 只有第一次弹了确认
    oks = [m for m in out
           if m["type"] == "event" and m["event"].get("kind") == "action_result"
           and m["event"]["result"].get("success")]
    assert len(oks) == 2  # 两次都执行成功（第二次免确认）


# ---------- Task 3：多槽 pending_confirms + batch_confirmer + confirm_batch IPC ----------


def test_confirm_batch_resolves_multiple_futures(tmp_path):
    """多槽：两个 L3 tool 同轮 CONFIRM → 一次 confirmation_needed(actions=2) →
    confirm_batch 两个 id → batch_confirmer 都 resolve → 都执行。

    验证 spec §3.2：多槽 dict 互不阻挡；loop 攒批 + 一次推收件箱 + 批量批回的端到端链路。
    """
    import queue as _queue

    from yibao_brain.tools import Tool
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True, "n": params.get("n")})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="t1", tool_id="danger", params={"n": 1}),
            ToolCall(id="t2", tool_id="danger", params={"n": 2}),
        ]),
        second=FakeProvider(text="done"),
    )
    out: list = []
    inbox_q: "_queue.Queue" = _queue.Queue()
    inbox_q.put({"id": 1, "type": "run", "text": "做两件危险事"})
    reacted = [False]

    def reader():
        return inbox_q.get()

    def writer(m):
        out.append(m)
        if (not reacted[0] and m.get("type") == "event"
                and m["event"].get("kind") == "confirmation_needed"):
            reacted[0] = True
            ids = [a["id"] for a in m["event"]["actions"]]
            assert len(ids) == 2  # 一次推两个 action（攒批载荷）
            inbox_q.put({"type": "confirm_batch", "items": [
                {"id": i, "approved": True, "remember": False} for i in ids
            ]})
            inbox_q.put(None)

    _run_async(
        serve_async(
            reader, writer,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            skills_factory=lambda: _registry_with(DangerTool()),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    confirms = [e for e in evs if e.get("kind") == "confirmation_needed"]
    assert len(confirms) == 1                  # 只一次 confirmation_needed（攒批）
    assert len(confirms[0]["actions"]) == 2    # 多槽：两个 action 同事件
    oks = [e for e in evs if e.get("kind") == "action_result" and e["result"].get("success")]
    assert len(oks) == 2                        # 两个 id 都 resolve → 都执行


def test_confirm_batch_early_answer(tmp_path, monkeypatch):
    """早到答案：confirm_batch 在 batch_confirmer 注册 future 前到达 → 存 early_answers →
    loop 取时命中（不走 future await 路径）。

    用 panel_action 因其 action.id = pa_<rid> 可预知；把 confirm_batch 放在 panel_action
    之前投递，强制走 early_answers 分支（future 还没建）。
    """
    executed = []
    _patch_api(monkeypatch, risk="L2")  # 触发 CONFIRM
    out = []
    _run_async(
        serve_async(
            make_reader([
                # 先投 confirm_batch：pending_confirms 还没 pa_1 → 存 early_answers
                {"type": "confirm_batch", "items": [
                    {"id": "pa_1", "approved": True, "remember": False}
                ]},
                # 再投 panel_action：CONFIRM 时 batch_confirmer 从 early_answers 命中
                {"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]                      # 早到答案批准 → 执行了
    assert {"type": "confirm_batched", "ok": True} in out  # IPC 往返回执


def test_confirm_batch_ipc_roundtrip(tmp_path):
    """confirm_batch IPC 往返：发 items（无对应 future，存 early_answers）→ 回 confirm_batched ok。"""
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "confirm_batch", "items": [
                    {"id": "x1", "approved": True, "remember": False},
                    {"id": "x2", "approved": False, "remember": True},
                    {"id": "x3", "approved": True, "remember": True},
                ]},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert {"type": "confirm_batched", "ok": True} in out
    # 多条目都应被吃下（不抛、不漏回执）
    assert len([m for m in out if m["type"] == "confirm_batched"]) == 1


# ---------- 协议扩展：hello / ping / permissions ----------


def test_serve_async_stdin_close_cancels_pending_confirmation(tmp_path):
    """stdin 关闭时卡在确认等待的任务必须被取消、限时退出（防孤儿 brain 占 qdrant 锁）。"""
    import time as _time

    from yibao_brain.tools import Tool, ToolRegistry
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerTool(Tool):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger", params={})]),
        second=FakeProvider(text="done"),
    )
    inbox = iter([{"id": 1, "type": "run", "text": "做危险的事"}])

    def reader():
        try:
            return next(inbox)
        except StopIteration:
            _time.sleep(0.5)  # 让 run 任务先走到确认等待，再送 EOF（确认始终不答）
            return None

    out = []
    t0 = _time.monotonic()
    _run_async(
        serve_async(
            reader,
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            skills_factory=lambda: _registry_with(DangerTool()),
        )
    )
    elapsed = _time.monotonic() - t0
    assert elapsed < 8  # 取消 + 5s 限时内退出（旧行为：永久挂起 → 孤儿进程）
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert not any(
        m["type"] == "event" and m["event"].get("kind") == "action_result"
        and m["event"]["result"]["data"].get("did") for m in out
    )


def test_serve_async_emits_hello_on_start(tmp_path):
    out = []
    _run_async(
        serve_async(
            make_reader([]),  # 立即 EOF
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert out[0]["type"] == "hello"
    assert out[0]["version"] == 1
    assert set(out[0]["permissions"]) >= {"ax", "screen", "input"}


def test_serve_async_ping_pong(tmp_path):
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "ping"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    pongs = [m for m in out if m["type"] == "pong"]
    assert len(pongs) == 1


def test_serve_async_ping_answered_while_run_busy(tmp_path):
    """长任务占住主循环时，ping 由读线程即时应答（看门狗不误杀，2026-07-21 误杀根治）。"""
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "run", "id": "r1", "text": "hi"},
                {"type": "ping"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(text="ok", delay=0.5),
        )
    )
    types = [m["type"] for m in out]
    assert "pong" in types
    assert types.index("pong") < types.index("run_done")  # 不等长任务收尾就答了


def test_serve_async_ping_suppressed_when_loop_dead(tmp_path, monkeypatch):
    """主循环真卡死（tick 停滞）→ 扣住 pong，让看门狗杀掉重启。"""
    from yibao_brain import server as srv

    monkeypatch.setattr(srv, "_TICK_FRESH_S", -1.0)  # 任何 lag 都算「循环已死」
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "ping"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert not [m for m in out if m["type"] == "pong"]


def test_serve_async_check_permissions(tmp_path):
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "check_permissions"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    perms = [m for m in out if m["type"] == "permissions"]
    assert len(perms) == 1
    assert set(perms[0]["permissions"]) >= {"ax", "screen", "input"}


def test_serve_async_prompt_permission(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "yibao_brain.server.permissions.prompt_ax", lambda: calls.append("ax") or True
    )
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "prompt_permission", "which": "ax"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert calls == ["ax"]
    assert any(m["type"] == "permissions" for m in out)


def test_serve_async_prompt_input_permission(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "yibao_brain.server.permissions.prompt_input", lambda: calls.append("input") or True
    )
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "prompt_permission", "which": "input"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert calls == ["input"]
    assert any(m["type"] == "permissions" for m in out)


def test_serve_async_perception_list_delete_and_clear(tmp_path):
    class FakePerceptionStore:
        def __init__(self):
            self.deleted = []
            self.cleared = False

        def list(self, limit=60, before_id=None):
            assert limit == 20
            assert before_id == 9
            return [
                {
                    "id": 8,
                    "ts": 100.0,
                    "source": "app",
                    "kind": "frontmost",
                    "payload": {"app": "Xcode", "title": "yibao"},
                    "sensitivity": "S1",
                }
            ]

        def delete(self, oid):
            self.deleted.append(oid)
            return oid == 8

        def sources(self):
            return ["activity", "app"]

        def clear(self):
            self.cleared = True
            return 4

        def purge(self):
            return 0

        def close(self):
            pass

    store = FakePerceptionStore()
    out = []
    _run_async(
        serve_async(
            make_reader(
                [
                    {"type": "perception_list", "limit": 20, "before_id": 9},
                    {"type": "perception_delete", "id": 123, "per_id": 8},
                    {"type": "perception_clear"},
                ]
            ),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            perception_store=store,
        )
    )

    perception = [m for m in out if m["type"] == "perception"]
    assert perception == [
        {"type": "perception", "items": store.list(20, 9), "sources": ["activity", "app"], "available": True}
    ]
    assert {"type": "perception_deleted", "id": 8, "ok": True} in out
    assert {"type": "perception_cleared", "count": 4} in out
    assert store.deleted == [8]
    assert store.cleared is True


class _ActivityPerceptionStore:
    def __init__(self, row_ts=None):
        self.row_ts = row_ts
        self.queries = 0

    def purge(self):
        return 0

    def close(self):
        pass

    def query_window(self, start_ts, end_ts, limit=2000):
        self.queries += 1
        if self.row_ts is None:
            return []
        return [
            {
                "id": 1,
                "ts": self.row_ts,
                "source": "app",
                "kind": "frontmost",
                "payload": {"app": "Terminal", "title": "Window Secret"},
                "sensitivity": "S1",
            }
        ]

    def latest_before(self, source, ts):
        return None


def _tool_names(call):
    return {
        item.get("name") or item.get("function", {}).get("name")
        for item in call["tools"]
    }


def test_serve_async_registers_activity_tool_only_when_store_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    with_store = FakeProvider(chunks=["ok"])
    store = _ActivityPerceptionStore()
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "你好"}]),
            lambda _m: None,
            use_real=False,
            db_path=str(tmp_path / "with.db"),
            provider=with_store,
            perception_store=store,
        )
    )
    without_store = FakeProvider(chunks=["ok"])
    _run_async(
        serve_async(
            make_reader([{"id": 2, "type": "run", "text": "你好"}]),
            lambda _m: None,
            use_real=False,
            db_path=str(tmp_path / "without.db"),
            provider=without_store,
        )
    )

    assert "load_user_activity" in _tool_names(with_store.astream_calls[0])
    assert "load_user_activity" not in _tool_names(without_store.astream_calls[0])
    assert store.queries == 0  # 工具可见不等于自动读取；模型没调用时保持零访问


def test_serve_async_activity_tool_observes_live_model_access_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    end = datetime.now().astimezone()
    start = end - timedelta(minutes=10)
    store = _ActivityPerceptionStore(row_ts=(start + timedelta(minutes=1)).timestamp())
    provider = _TwoStepProvider(
        first=FakeProvider(
            tool_calls=[
                ToolCall(
                    id="t1",
                    tool_id="load_user_activity",
                    params={"start_at": start.isoformat(), "end_at": end.isoformat()},
                )
            ]
        ),
        second=FakeProvider(chunks=["你刚才在 Terminal"]),
    )
    out = []

    _run_async(
        serve_async(
            make_reader(
                [
                    {"type": "settings_set", "values": {"perception.model_access": True}},
                    {"id": 1, "type": "run", "text": "我刚才在干嘛"},
                ]
            ),
            out.append,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            perception_store=store,
        )
    )

    settings = [item for item in out if item["type"] == "settings"][-1]
    assert settings["values"]["perception.model_access"] is True
    assert store.queries == 1
    events = [item["event"] for item in out if item["type"] == "event"]
    result = next(item["result"] for item in events if item["kind"] == "action_result")
    assert result["data"]["segment_count"] == 1
    assert "Window Secret" not in json.dumps(result, ensure_ascii=False)
    assert [(item["kind"], item.get("text", "")) for item in events[-2:]] == [
        ("final_reply", "你刚才在 Terminal"),
        ("notice", "已参考最近活动"),
    ]


def test_load_plugins_safe_wires_registry(tmp_path, monkeypatch, capsys):
    """build_loop 的插件接线：YIBAO_PLUGINS_DIR 指向 tmp，加载结果进 registry 并打印 stderr。"""
    from yibao_brain.memory import FakeMemory
    from yibao_brain.server import _load_plugins_safe
    from yibao_brain.tools import ToolRegistry

    plugin = tmp_path / "notes"
    plugin.mkdir()
    (plugin / "manifest.toml").write_text(
        'id = "notes"\ncapabilities = ["db"]\n'
        '[[table]]\nname = "t"\ncolumns = [{name = "id", type = "text", pk = true}]\n'
        '[[tool]]\nid = "keep"\ntype = "db"\ndescription = "记"\n'
        "[tool.db]\nop = \"insert\"\ntable = \"t\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    reg = ToolRegistry()
    _load_plugins_safe(reg, FakeMemory(), FakeProvider(), None)
    assert reg.get("notes.keep").id == "notes.keep"
    assert "[yibao] 插件 notes: ok" in capsys.readouterr().err


def test_load_plugins_safe_never_raises(tmp_path, monkeypatch):
    """插件系统整体异常也不许拖垮底座启动（外层兜底 try）。"""
    from yibao_brain.memory import FakeMemory
    from yibao_brain.server import _load_plugins_safe
    from yibao_brain.tools import ToolRegistry

    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(tmp_path / "nonexistent"))
    _load_plugins_safe(ToolRegistry(), FakeMemory(), FakeProvider(), None)  # 不抛


def test_load_plugins_safe_passes_reminders_store(tmp_path, monkeypatch):
    """回归：_load_plugins_safe 必须把 reminders 透传给 load_plugins——
    漏传时 reminders 插件 ctx.reminders=None，面板直调全报「底座未提供提醒存储」，面板打不开。"""
    from yibao_brain.memory import FakeMemory
    from yibao_brain.reminders import ReminderStore
    from yibao_brain.server import _load_plugins_safe
    from yibao_brain.tools import ToolRegistry

    plugin = tmp_path / "rem"
    (plugin / "tools").mkdir(parents=True)
    (plugin / "manifest.toml").write_text(
        'id = "rem"\ncapabilities = ["reminders"]\n[code]\nentry = "tools"\n',
        encoding="utf-8",
    )
    (plugin / "tools" / "x.py").write_text(
        "from yibao_brain.ipc import ActionResult, RiskLevel\n"
        "from yibao_brain.tools import Tool\n"
        "class X(Tool):\n"
        '    id = "rem.x"\n'
        '    description = "x"\n'
        "    default_risk = RiskLevel.L0_READONLY\n"
        "    def openai_schema(self):\n"
        '        return {"type": "function", "function": {"name": self.id, "description": "x",'
        ' "parameters": {"type": "object", "properties": {}}}}\n'
        "    def run(self, params, ctx):\n"
        "        return ActionResult(success=True, data={})\n"
        "def make_tools(ctx):\n"
        "    return [X()]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    reg = ToolRegistry()
    store = ReminderStore(str(tmp_path / "data" / "reminders.json"))
    _load_plugins_safe(reg, FakeMemory(), FakeProvider(), None, reminders=store)
    sk = reg.get("rem.x")
    assert sk is not None and sk.plugin_ctx.reminders is store


# ---------- ⑦py：panel_action（面板直调方法，过白名单 + 闸门）----------


class _RecTool:
    """记录执行的删除 tool（plugin 命名空间注册）。"""

    @staticmethod
    def make(executed, ref=None, risk=RiskLevel.L1_LOW):
        from yibao_brain.ipc import ActionResult as AR
        from yibao_brain.tools import Tool as _S

        class Rec(_S):
            id = "tdel.delete"
            description = "删除一条"
            default_risk = risk

            def run(self, params, ctx):
                executed.append(dict(params))
                return AR(success=True, data={"deleted": params.get("id")}, panel=ref)

        return Rec()


def _pa_factory(executed, ref=None, risk=RiskLevel.L1_LOW):
    from yibao_brain.tools import ToolRegistry

    def factory():
        reg = ToolRegistry()
        reg.register(_RecTool.make(executed, ref, risk), plugin="tdel")
        return reg

    return factory


def _patch_api(monkeypatch, **kw):
    from yibao_brain import plugins
    from yibao_brain.plugins import ApiMethod

    kw.setdefault("name", "tdel.delete")
    kw.setdefault("handler", "tdel.delete")
    kw.setdefault("direct", True)
    kw.setdefault("intent", None)
    kw.setdefault("risk", None)
    kw.setdefault("plugin_id", "tdel")
    if isinstance(kw["risk"], str):  # "L2" → RiskLevel.L2_MEDIUM（与 _load_api 的解析一致）
        kw["risk"] = RiskLevel(int(kw["risk"][1]))
    monkeypatch.setitem(plugins._API, kw["name"], ApiMethod(**kw))


def test_panel_action_direct_end_to_end(tmp_path, monkeypatch):
    executed = []
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    _patch_api(monkeypatch)
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "tdel:list", {"type": "list"})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed, ref="tdel:list"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    kinds = [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]                       # tool 真的被执行
    ar = next(e for e in evs if e["kind"] == "action_result")
    assert ar["result"]["success"] and ar["result"]["data"] == {"deleted": "r1"}
    pe = next(e for e in evs if e["kind"] == "panel")       # 带 panel 引用 → panel 事件
    assert pe["payload"] == {"panel": "tdel:list", "title": "tdel:list", "schema": {"type": "list"}, "data": {"deleted": "r1"}}
    assert kinds.index("panel") > kinds.index("action_result")
    assert out[-1] == {"type": "run_done", "id": 1}
    from yibao_brain.work_graph import WorkGraphStore
    graph = WorkGraphStore(str(tmp_path / "data" / "work_graph.db"))
    try:
        assert [row["tool_id"] for row in graph.invocation_views()] == ["tdel.delete"]
    finally:
        graph.close()


def test_panel_action_api_panel_override_emits_webview(tmp_path, monkeypatch):
    """api.toml method 声明 panel 字段：直调成功后改用该面板发事件（覆盖 tool 自带引用），
    webview 面板 payload 带 html（schema 为 null），schema 面板 payload 形状不变。"""
    executed = []
    _patch_api(monkeypatch, panel="tdel:editor")
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "tdel:list", {"type": "list"})
    monkeypatch.setitem(plugins._PANELS, "tdel:editor", {"type": "webview", "html": "<html>编辑器</html>"})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed, ref="tdel:list"),  # tool 自带 tdel:list，应被 api.panel 覆盖
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    pe = next(e for e in evs if e["kind"] == "panel")
    assert pe["payload"] == {
        "panel": "tdel:editor",
        "title": "tdel:editor",
        "schema": None,
        "webview": {"html": "<html>编辑器</html>"},
        "data": {"deleted": "r1"},
    }
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_not_in_whitelist_rejected(tmp_path):
    executed = []
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.ghost", "params": {}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    err = next(e for e in evs if e["kind"] == "error")
    assert "白名单" in err["text"] and "tdel.ghost" in err["text"]
    assert err["action"]["id"] == "pa_1"                     # 错误带 rid 标签（壳侧桥按标签认领）
    assert executed == []                                    # 未执行
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_confirm_flow_rejected(tmp_path, monkeypatch):
    # api.risk="L2" 收紧（tool 默认 L1）→ 触发确认流；壳 confirm_batch 拒绝 → error，不执行
    # action.id = pa_<rid> 由 handle_panel_action 显式设置，客户端可预知
    executed = []
    _patch_api(monkeypatch, risk="L2")
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
                {"type": "confirm_batch", "items": [{"id": "pa_1", "approved": False}]},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    kinds = [e["kind"] for e in evs]
    assert "confirmation_needed" in kinds
    assert "action_result" not in kinds and executed == []   # 拒绝了就没执行
    err = next(e for e in evs if e["kind"] == "error")
    assert "拒绝" in err["text"]
    assert err["action"]["id"] == "pa_1"                     # 错误带 rid 标签（壳侧桥按标签认领）
    # 裁决出炉即在流内广播 confirmation_resolved（旁路批准的即时出队信号；主流事件，
    # 序确定性：confirmation_needed → confirmation_resolved → error → run_done）
    resolved = next(e for e in evs if e["kind"] == "confirmation_resolved")
    assert resolved["action_ids"] == ["pa_1"] and resolved["payload"]["verdicts"] == {"pa_1": False}
    assert kinds.index("confirmation_needed") < kinds.index("confirmation_resolved") < kinds.index("error")
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_confirm_flow_approved_executes(tmp_path, monkeypatch):
    executed = []
    _patch_api(monkeypatch, risk="L2")
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
                {"type": "confirm_batch", "items": [{"id": "pa_1", "approved": True}]},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_write_still_preempts_run(tmp_path, monkeypatch):
    """L1+ 直调仍占槽位：到达时抢占在跑的 run（面板写操作 = 用户最新意图，对话让路）。"""
    executed = []
    _patch_api(monkeypatch)  # tdel.delete 直调，tool 默认 L1
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "run", "text": "hi"},
                {"id": 2, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            # delay：让 run 在面板消息到达时仍在跑——否则 FakeProvider 秒回，run 已
            # 完成、final_reply 已发出，抢占只能发生在收尾阶段（测试意图落空）
            provider=FakeProvider(text="你好", delay=0.2),
            skills_factory=_pa_factory(executed),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    kinds = [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]                        # 写操作执行了
    assert "interrupted" in kinds                            # run 被抢占
    assert "final_reply" not in kinds                        # 没出回复


def test_panel_action_readonly_bypasses_slot_no_preempt(tmp_path, monkeypatch):
    """L0 只读直调不占槽位、不抢占：run 在跑时到达，run 完整收尾，直调并发执行。

    回归：面板数据加载（read_article 等）与对话 run 互相抢占 → 回复截断 + 编辑器「没反应」。
    """
    executed = []
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    _patch_api(monkeypatch, name="tread.get", handler="tdel.delete")
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "run", "text": "hi"},
                {"id": 2, "type": "panel_action", "method": "tread.get", "params": {"id": "r1"}},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(text="你好"),
            skills_factory=_pa_factory(executed, risk=RiskLevel.L0_READONLY),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    assert executed == [{"id": "r1"}]                        # 只读直调并发执行了
    reply = next(e for e in evs if e["kind"] == "final_reply")
    assert reply["text"] == "你好"                            # run 没被抢占，完整收尾
    assert not any(e["kind"] == "interrupted" for e in evs)
    assert {"type": "run_done", "id": 1} in out and {"type": "run_done", "id": 2} in out
    from yibao_brain.work_graph import WorkGraphStore
    graph = WorkGraphStore(str(tmp_path / "data" / "work_graph.db"))
    try:
        assert graph.invocation_views() == []                 # L0 Surface 取数不污染业务图
    finally:
        graph.close()


def test_panel_action_readonly_storm_executes_tool_once(tmp_path, monkeypatch):
    """同一个 Surface 的 L0 风暴共享一次执行；每个请求仍收到自己的 run_done。"""
    from yibao_brain.ipc import ActionResult as AR
    from yibao_brain.tools import Tool as _S, ToolRegistry

    executed = []
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    _patch_api(monkeypatch, name="tread.get", handler="tread.get")

    class SlowRead(_S):
        id = "tread.get"
        description = "读"
        default_risk = RiskLevel.L0_READONLY

        def run(self, params, ctx):
            time.sleep(0.03)
            executed.append(dict(params))
            return AR(success=True, data={"rows": [{"id": params.get("id")}]})

    def factory():
        reg = ToolRegistry()
        reg.register(SlowRead(), plugin="tread")
        return reg

    requests = [
        {"id": i, "type": "panel_action", "method": "tread.get",
         "params": {"id": "same"}, "surface": "panel:tread"}
        for i in range(1, 61)
    ]
    out = []
    _run_async(serve_async(
        make_reader(requests), lambda m: out.append(m), use_real=False,
        db_path=str(tmp_path / "a.db"), provider=FakeProvider(), skills_factory=factory,
    ))
    assert executed == [{"id": "same"}]
    assert {m["id"] for m in out if m.get("type") == "run_done"} == set(range(1, 61))
    errors = [m for m in out if m.get("type") == "event" and m["event"]["kind"] == "error"]
    assert errors == []


def test_panel_action_intent_goes_to_agent(tmp_path, monkeypatch):
    executed = []
    _patch_api(monkeypatch, name="tdel.clean", direct=False, intent="清理闪念 {id}")
    provider = FakeProvider(text="已清理")
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.clean", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            skills_factory=_pa_factory(executed),
        )
    )
    # intent 渲染后当作用户输入走了 agent 流程（FakeProvider 收到渲染文本）
    msgs = provider.astream_calls[0]["messages"]
    assert msgs[-1] == {"role": "user", "content": "清理闪念 r1"}
    assert executed == []                                     # 不直调 tool
    evs = [m["event"] for m in out if m["type"] == "event"]
    assert "final_reply" in [e["kind"] for e in evs]
    assert out[-1] == {"type": "run_done", "id": 1}


def test_render_intent_missing_key_kept_and_default():
    from yibao_brain.plugins import ApiMethod
    from yibao_brain.server import _render_intent

    api = ApiMethod(name="tdel.delete", handler="tdel.delete", direct=False,
                    intent="删 {id} {extra}", risk=None, plugin_id="tdel")
    assert _render_intent(api, {"id": "1"}) == "删 1 {extra}"  # 缺键保留原样不炸
    api2 = ApiMethod(name="tdel.delete", handler="tdel.delete", direct=False,
                     intent=None, risk=None, plugin_id="tdel")
    assert _render_intent(api2, {}) == "调用 tdel.delete"      # 无 intent 用默认


def test_panel_action_refresh_replaces_stale_panel_data(tmp_path, monkeypatch):
    """api.toml method 声明 refresh：直调成功后面板拿到的是刷新查询的新数据，而非操作回执。"""
    from yibao_brain import plugins
    from yibao_brain.ipc import ActionResult as AR
    from yibao_brain.tools import Tool as _S, ToolRegistry

    executed = []

    class Del(_S):
        id = "tdel.delete"; description = "删"; default_risk = RiskLevel.L1_LOW
        def run(self, params, ctx):
            executed.append(dict(params))
            return AR(success=True, data={"deleted": params.get("id")}, panel="tdel:list")

    class List_(_S):
        id = "tdel.list"; description = "列"; default_risk = RiskLevel.L0_READONLY
        def run(self, params, ctx):
            return AR(success=True, data={"rows": [{"id": "r2", "text": "还剩这条"}]}, panel="tdel:list")

    def factory():
        reg = ToolRegistry()
        reg.register(Del(), plugin="tdel")
        reg.register(List_(), plugin="tdel")
        return reg

    _patch_api(monkeypatch, refresh="tdel.list")
    monkeypatch.setitem(plugins._PANELS, "tdel:list", {"type": "list"})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=factory,
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    panels = [e for e in evs if e["kind"] == "panel"]
    # 只发一次 panel，且是刷新后的 rows（不是删除回执）
    assert len(panels) == 1
    assert panels[0]["payload"]["data"] == {"rows": [{"id": "r2", "text": "还剩这条"}]}
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_quiet_suppresses_panel_event(tmp_path, monkeypatch):
    """quiet=true 的 api 方法：直调执行 + action_result 照发，但不发 panel 事件（不弹面板窗）。"""
    executed = []
    _patch_api(monkeypatch, quiet=True)
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "tdel:list", {"type": "list"})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed, ref="tdel:list"),  # tool 自带 panel 引用，应被 quiet 抑制
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    kinds = [e["kind"] for e in evs]
    assert executed == [{"id": "r1"}]           # tool 真的被执行
    assert "action_result" in kinds             # 回执照发（壳侧气泡用）
    assert "panel" not in kinds                 # panel 事件被抑制（不弹窗）
    assert out[-1] == {"type": "run_done", "id": 1}


def test_load_api_parses_quiet(tmp_path):
    """api.toml quiet = true 解析进 ApiMethod.quiet（缺省 False）。"""
    from yibao_brain import plugins
    from yibao_brain.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register(_RecTool.make([]), plugin="tdel")
    api = tmp_path / "api.toml"
    api.write_text(
        '[[method]]\nname = "save"\nhandler = "tdel.delete"\ndirect = true\nquiet = true\n'
        '[[method]]\nname = "loud"\nhandler = "tdel.delete"\ndirect = true\n',
        encoding="utf-8",
    )
    plugins._load_api("tdel", api, reg)
    try:
        assert plugins.get_api("tdel.save").quiet is True
        assert plugins.get_api("tdel.loud").quiet is False
    finally:
        plugins._API.pop("tdel.save", None)
        plugins._API.pop("tdel.loud", None)


def test_serve_async_tts_cancelled_error_does_not_crash_brain(tmp_path):
    """TTS 抛 CancelledError（打断命中合成）：_pump_tts 视为正常取消，
    run 正常收尾 run_done，大脑不崩。"""
    provider = FakeProvider(chunks=["你好。"])
    out = []

    class _CancelVoice:
        async def speak_stream(self, text_iter, cancel):
            async for _ in text_iter:
                pass
            raise asyncio.CancelledError

    async def _go():
        await serve_async(
            make_reader([{"id": 1, "type": "run", "text": "hi", "tts": True}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=_CancelVoice(),
        )

    _run_async(_go())  # 不抛即过
    assert out[-1] == {"type": "run_done", "id": 1}


def test_serve_async_panel_context_sets_focus(tmp_path):
    """panel_context 消息更新焦点，随后的 run 把它注入 LLM 上下文；结束后复位防串测试。"""
    import yibao_brain.server as srv

    provider = FakeProvider(chunks=["在看 K3 那条"])
    focus = {
        "plugin": "zimeiti",
        "panel": "detail",
        "item": {"id": "abc123", "title": "K3 是垃圾", "status": "writing"},
    }
    out = []
    old = srv._FOCUS["value"]
    try:
        _run_async(
            serve_async(
                make_reader([
                    {"type": "panel_context", "focus": focus},
                    {"id": 1, "type": "run", "text": "这个怎么样"},
                ]),
                lambda m: out.append(m),
                use_real=False,
                db_path=str(tmp_path / "a.db"),
                provider=provider,
            )
        )
        messages = provider.astream_calls[0]["messages"]
        focus_msgs = [m for m in messages if m["role"] == "system" and "用户当前正在看" in m["content"]]
        assert len(focus_msgs) == 1
        assert "K3 是垃圾" in focus_msgs[0]["content"]
        assert out[-1] == {"type": "run_done", "id": 1}
    finally:
        srv._FOCUS["value"] = old


def test_serve_async_panel_context_clear(tmp_path):
    """面板关闭（focus=null）后 run 不带焦点消息。"""
    import yibao_brain.server as srv

    provider = FakeProvider(chunks=["你好"])
    out = []
    old = srv._FOCUS["value"]
    try:
        _run_async(
            serve_async(
                make_reader([
                    {"type": "panel_context", "focus": {"plugin": "zimeiti", "panel": "board"}},
                    {"type": "panel_context", "focus": None},
                    {"id": 1, "type": "run", "text": "你好"},
                ]),
                lambda m: out.append(m),
                use_real=False,
                db_path=str(tmp_path / "a.db"),
                provider=provider,
            )
        )
        messages = provider.astream_calls[0]["messages"]
        assert not any("用户当前正在看" in m["content"] for m in messages if m["role"] == "system")
    finally:
        srv._FOCUS["value"] = old


def test_serve_async_stalled_task_is_force_cancelled(tmp_path, monkeypatch):
    """上一任务 hung 在 provider 里（cancel 事件只在 chunk 间检查，叫不醒它）：
    超过宽限被强制取消，后续请求照常受理。"""
    import time

    import yibao_brain.server as srv

    monkeypatch.setattr(srv, "_PREEMPT_GRACE_S", 0.3)
    state = {"n": 0}

    class _HangThenFast:
        async def astream(self, messages, tools=None):
            state["n"] += 1
            if state["n"] == 1:
                await asyncio.sleep(60)  # hung：只能靠强制 cancel 收场
                return
            async for d in FakeProvider(chunks=["第二个好了"]).astream(messages, tools):
                yield d

    msgs = [{"id": 1, "type": "run", "text": "卡死"}, {"id": 2, "type": "run", "text": "再来"}, None]
    it = iter(msgs)

    def slow_reader():
        m = next(it)
        if m is not None and m.get("id") == 2:
            time.sleep(0.5)  # 让 run1 先真进 astream 挂起，再来第二个请求（读者线程内 sleep 无碍）
        return m

    out = []
    _run_async(
        serve_async(
            slow_reader,
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_HangThenFast(),
        )
    )
    # run2 正常完成并出了最终回复（没有被 run1 的 hung 永久排队）
    assert {"type": "run_done", "id": 2} in out
    assert any(m["type"] == "event" and m["event"].get("kind") == "final_reply" for m in out)


def test_serve_async_events_carry_surface(tmp_path):
    # 会话分流：run 带 surface 时，该 run 的所有事件都带同一标签（壳侧各窗按它过滤）
    provider = FakeProvider(chunks=["你好"])
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "你好", "surface": "panel:zimeiti"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
        )
    )
    events = [m for m in out if m["type"] == "event"]
    assert events and all(m.get("surface") == "panel:zimeiti" for m in events)


def test_serve_async_default_surface_is_pet(tmp_path):
    # 不带 surface 的老客户端：事件 surface = pet，宠物窗照单全收
    provider = FakeProvider(chunks=["你好"])
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "run", "text": "你好"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
        )
    )
    events = [m for m in out if m["type"] == "event"]
    assert events and all(m.get("surface") == "pet" for m in events)


# ---------- Feed 已读 IPC + stats.unread（Task 6）----------


def _seed_feed(db_path, entries):
    """serve_async 内部自建 FeedStore（按 db_path 同目录的 feed.db）。
    测试用独立连接先写几条未读，serve_async 打开同一 SQLite 文件即可读到。"""
    from yibao_brain.feed import FeedStore

    feed = FeedStore(str(db_path.parent / "feed.db"))
    try:
        for kind, text, meta in entries:
            feed.add(kind, text, meta)
    finally:
        feed.close()


def test_serve_async_feed_mark_read_lowers_unread(tmp_path):
    # 预先写两条未读 → 取一次 feed（stats.unread=2）→ 标 id=1 已读 → 再取 feed（unread=1）
    _seed_feed(tmp_path / "a.db", [
        ("task", "任务A完成", {"task": {"id": "a"}}),
        ("event", "事件B", {}),
    ])
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "feed"},
                {"type": "feed_mark_read", "id": 1},
                {"type": "feed"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    feeds = [m for m in out if m["type"] == "feed"]
    assert len(feeds) == 2
    assert feeds[0]["stats"]["unread"] == 2
    assert feeds[1]["stats"]["unread"] == 1
    # 已读回执：id 回传、ok=True（id=1 命中预写行）
    assert {"type": "feed_marked_read", "id": 1, "ok": True} in out


def test_serve_async_feed_stats_roundtrip(tmp_path):
    """设置页信任统计：feed_stats 命令回近 N 天聚合（_seed_feed 预写两条）。"""
    _seed_feed(tmp_path / "a.db", [
        ("task", "任务A完成", {"task": {"id": "a"}}),
        ("event", "事件B", {}),
    ])
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed_stats"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    msgs = [m for m in out if m["type"] == "feed_stats"]
    assert len(msgs) == 1
    stats = msgs[0]["stats"]
    assert stats["total"] == 2
    assert stats["by_kind"]["task"] == 1 and stats["by_kind"]["event"] == 1


def test_serve_async_feed_includes_running_watch_command(tmp_path):
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.tools import ToolRegistry

    jobs = BackgroundJobManager()
    started = jobs.start("sleep 5", cwd=str(tmp_path), name="后台构建")

    def skills_factory():
        registry = ToolRegistry()
        registry.background_jobs = jobs
        return registry

    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed"}]),
            lambda message: out.append(message),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=skills_factory,
        )
    )
    feed = [message for message in out if message["type"] == "feed"][0]
    task = next(item for item in feed["running_tasks"] if item["id"] == started["task_id"])
    assert task["label"] == "后台构建"
    assert task["prompt"] == "sleep 5"
    assert feed["stats"]["running_tasks"] == 1


def test_serve_async_feed_mark_status_roundtrip(tmp_path):
    # C 子项目：feed_mark_status IPC 往返 + stats.ignored + recent status
    _seed_feed(tmp_path / "a.db", [
        ("task", "任务A", {}),
        ("task", "任务B", {}),
    ])
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "feed_mark_status", "id": 1, "status": "ignore"},
                {"type": "feed_mark_status", "id": 2, "status": "follow"},
                {"type": "feed"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert {"type": "feed_status_set", "id": 1, "status": "ignore", "ok": True} in out
    assert {"type": "feed_status_set", "id": 2, "status": "follow", "ok": True} in out
    feed = [m for m in out if m["type"] == "feed"][0]
    assert feed["stats"]["ignored"] == 1
    by_id = {it["id"]: it for it in feed["items"]}
    assert by_id[1]["status"] == "ignore"
    assert by_id[2]["status"] == "follow"


def test_serve_async_feed_mark_read_unknown_id_returns_ok_false(tmp_path):
    # 不存在的 id：ok=False，不抛
    _seed_feed(tmp_path / "a.db", [("event", "仅一条", {})])
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed_mark_read", "id": 999}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert {"type": "feed_marked_read", "id": 999, "ok": False} in out


def test_serve_async_feed_mark_all_read(tmp_path):
    # 两条未读 → mark_all_read 回 n=2 → feed.stats.unread=0
    _seed_feed(tmp_path / "a.db", [
        ("task", "任务A完成", {}),
        ("event", "事件B", {}),
    ])
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "feed_mark_all_read"},
                {"type": "feed"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert {"type": "feed_all_read", "n": 2} in out
    feeds = [m for m in out if m["type"] == "feed"]
    assert len(feeds) == 1
    assert feeds[0]["stats"]["unread"] == 0


# ---------- Dock 组装（固定 + 频率，Task 8）----------


def _seed_audit_calls(db_path, counts: dict[str, int]) -> None:
    """直接往 audit.db 的 actions 表插若干行（按 plugin id 指定次数）。
    tool_id 用 `<plugin>.x` 形式以匹配 plugin_call_counts 的前缀解析。
    先经 AuditLog 建表（避免 raw sqlite 撞「no such table」）。"""
    from yibao_brain.audit import AuditLog

    log = AuditLog(str(db_path))
    try:
        for pid, n in counts.items():
            for _ in range(n):
                log.conn.execute(
                    "INSERT INTO actions (tool_id, success) VALUES (?, 1)",
                    (f"{pid}.x",),
                )
        log.conn.commit()
    finally:
        log.close()


def test_dock_list_pinned_first_then_frequency(tmp_path, monkeypatch):
    """pinned 在前（保序），未固定按调用频次降序补齐；零频次的排最后。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.server import _dock_list

    log = AuditLog(str(tmp_path / "a.db"))
    _seed_audit_calls(tmp_path / "a.db", {"notes": 5, "zimeiti": 3, "forge": 1})
    monkeypatch.setattr("yibao_brain.server.load_settings",
                        lambda: {"dock_pinned": ["forge"]})

    plugins = [
        {"id": "agents", "name": "Agents"},
        {"id": "forge", "name": "Forge"},
        {"id": "notes", "name": "Notes"},
        {"id": "zimeiti", "name": "Zimeiti"},
    ]
    dock = _dock_list(log, plugins)
    assert [(d["id"], d["pinned"]) for d in dock] == [
        ("forge", True),    # pinned 优先
        ("notes", False),   # 5 次
        ("zimeiti", False), # 3 次
        ("agents", False),  # 0 次（不在 audit 表）
    ]


def test_dock_list_caps_at_five(tmp_path, monkeypatch):
    """合计上限 5：pinned 满了不再塞频率补齐；pinned 不足时频率补到 5 截止。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.server import _dock_list

    log = AuditLog(str(tmp_path / "a.db"))
    _seed_audit_calls(tmp_path / "a.db", {f"p{i}": 10 - i for i in range(8)})
    monkeypatch.setattr("yibao_brain.server.load_settings",
                        lambda: {"dock_pinned": ["p0", "p1", "p2"]})
    plugins = [{"id": f"p{i}", "name": f"P{i}"} for i in range(8)]
    dock = _dock_list(log, plugins)
    assert len(dock) == 5
    # 前 3 是 pinned（保序），后 2 是按频次降序的前两个未固定（p3=7, p4=6）
    assert [d["id"] for d in dock] == ["p0", "p1", "p2", "p3", "p4"]
    assert all(d["pinned"] for d in dock[:3])
    assert all(not d["pinned"] for d in dock[3:])


def test_dock_list_pinned_overflow_truncated_to_five(tmp_path, monkeypatch):
    """dock_pinned 配置超 5（脏数据/旧上限放宽）：只取前 5，enforce 上限。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.server import _dock_list

    log = AuditLog(str(tmp_path / "a.db"))
    monkeypatch.setattr("yibao_brain.server.load_settings",
                        lambda: {"dock_pinned": ["a", "b", "c", "d", "e", "f", "g"]})
    plugins = [{"id": x, "name": x.upper()} for x in "abcdefg"]
    dock = _dock_list(log, plugins)
    assert len(dock) == 5
    assert [d["id"] for d in dock] == ["a", "b", "c", "d", "e"]
    assert all(d["pinned"] for d in dock)


def test_dock_list_no_pinned_no_counts_falls_back_to_alpha(tmp_path, monkeypatch):
    """无固定、无频次数据：字母序前 5（稳定默认，避免按加载随机序展示）。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.server import _dock_list

    log = AuditLog(str(tmp_path / "a.db"))  # 空 audit → counts={}
    monkeypatch.setattr("yibao_brain.server.load_settings", lambda: {"dock_pinned": []})
    plugins = [
        {"id": "zimeiti", "name": "自媒体"},
        {"id": "agents", "name": "Agents"},
        {"id": "forge", "name": "Forge"},
        {"id": "notes", "name": "Notes"},
    ]
    dock = _dock_list(log, plugins)
    # 全部零频次 → 退化字母序（按 name）
    assert [d["id"] for d in dock] == ["agents", "forge", "notes", "zimeiti"]
    assert all(not d["pinned"] for d in dock)


def test_dock_list_stale_pinned_id_keeps_entry_with_id_as_name(tmp_path, monkeypatch):
    """已固定的插件被卸载：dock 仍保留其占位（id 即 name），用户能看到并主动取消固定。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.server import _dock_list

    log = AuditLog(str(tmp_path / "a.db"))
    monkeypatch.setattr("yibao_brain.server.load_settings",
                        lambda: {"dock_pinned": ["ghost", "notes"]})
    plugins = [{"id": "notes", "name": "Notes"}]  # ghost 已卸载
    dock = _dock_list(log, plugins)
    ids = {d["id"]: d for d in dock}
    assert ids["ghost"]["name"] == "ghost"  # 退化为 id
    assert ids["ghost"]["pinned"] is True
    assert ids["notes"]["pinned"] is True


def test_dock_list_ipc_returns_dock(tmp_path, monkeypatch):
    """dock_list IPC：返回 {type:dock_list, dock:[...]}，每项带 pinned 标记。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import plugins

    monkeypatch.setattr(plugins, "_PLUGIN_INFO", {
        "notes": {"name": "Notes", "description": ""},
        "forge": {"name": "Forge", "description": ""},
    })
    _seed_audit_calls(tmp_path / "a.db", {"notes": 3})
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "dock_list"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    rs = [m for m in out if m["type"] == "dock_list"]
    assert len(rs) == 1
    dock = rs[0]["dock"]
    assert [d["id"] for d in dock] == ["notes", "forge"]  # notes 有频次在前
    assert dock[0]["pinned"] is False and dock[1]["pinned"] is False


def test_set_dock_pin_add_and_remove_roundtrip(tmp_path, monkeypatch):
    """set_dock_pin 加/移除往返：on=True 追加、on=False 移除；每次回新 dock。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import plugins
    from yibao_brain.config import load_settings

    monkeypatch.setattr(plugins, "_PLUGIN_INFO", {
        "notes": {"name": "Notes", "description": ""},
        "forge": {"name": "Forge", "description": ""},
        "agents": {"name": "Agents", "description": ""},
    })
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "set_dock_pin", "pid": "notes", "on": True},
                {"type": "set_dock_pin", "pid": "forge", "on": True},
                {"type": "set_dock_pin", "pid": "notes", "on": False},
                {"type": "dock_list"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    pins = [m for m in out if m["type"] == "dock_pin_set"]
    assert len(pins) == 3
    assert pins[0]["pid"] == "notes" and pins[0]["ok"] is True
    assert pins[1]["pid"] == "forge" and pins[1]["ok"] is True
    assert pins[2]["pid"] == "notes" and pins[2]["ok"] is True
    # 持久化：disk 上只剩 forge
    assert load_settings()["dock_pinned"] == ["forge"]
    # 最后 dock_list：forge 是 pinned，notes 不是
    last = [m for m in out if m["type"] == "dock_list"][0]
    by_id = {d["id"]: d for d in last["dock"]}
    assert by_id["forge"]["pinned"] is True
    assert by_id["notes"]["pinned"] is False


def test_set_dock_pin_rejects_above_five(tmp_path, monkeypatch):
    """已固定 5 个时再加第 6 个：ok=False，dock 不变，磁盘不写。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import plugins
    from yibao_brain.config import load_settings

    monkeypatch.setattr(plugins, "_PLUGIN_INFO", {
        f"p{i}": {"name": f"P{i}", "description": ""} for i in range(6)
    })
    # 预置 5 个 pinned
    from yibao_brain.config import save_settings
    save_settings({"dock_pinned": ["p0", "p1", "p2", "p3", "p4"]})

    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "set_dock_pin", "pid": "p5", "on": True}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    pin = [m for m in out if m["type"] == "dock_pin_set"][0]
    assert pin["pid"] == "p5" and pin["ok"] is False
    # dock 仍是原 5 个（无 p5）
    assert len(pin["dock"]) == 5
    assert not any(d["id"] == "p5" for d in pin["dock"])
    # 磁盘未写：仍 5 个
    assert load_settings()["dock_pinned"] == ["p0", "p1", "p2", "p3", "p4"]


def test_set_dock_pin_idempotent(tmp_path, monkeypatch):
    """重复 pin 同一个：ok=True（幂等，不重复追加）；pin 不存在的再 unpin：ok=True（幂等）。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import plugins
    from yibao_brain.config import load_settings

    monkeypatch.setattr(plugins, "_PLUGIN_INFO", {
        "notes": {"name": "Notes", "description": ""},
    })
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "set_dock_pin", "pid": "notes", "on": True},
                {"type": "set_dock_pin", "pid": "notes", "on": True},  # 幂等
                {"type": "set_dock_pin", "pid": "ghost", "on": False},  # 未固定也卸
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    pins = [m for m in out if m["type"] == "dock_pin_set"]
    assert all(p["ok"] for p in pins)
    assert load_settings()["dock_pinned"] == ["notes"]  # 没重复


def test_recover_background_jobs_wiring(tmp_path):
    """启动钩子（模块级）：孤儿重跑/标失败 + Feed 记账 + store 挂接。"""
    from yibao_brain.background_jobs import BackgroundJobManager
    from yibao_brain.feed import FeedStore
    from yibao_brain.jobstore import JobsStore
    from yibao_brain.server import _recover_background_jobs

    store = JobsStore(str(tmp_path / "jobs.db"))
    store.add({"task_id": "job_ok", "command": "exit 0", "cwd": str(tmp_path),
               "name": "可重跑", "timeout": 60.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 1.0, "finished_at": None})
    store.add({"task_id": "job_bad", "command": "exit 0", "cwd": "/definitely/not/exist",
               "name": "不可重跑", "timeout": 60.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 2.0, "finished_at": None})
    feed = FeedStore(str(tmp_path / "feed.db"))
    jobs = BackgroundJobManager()

    _recover_background_jobs(feed, jobs, store, lambda e: None)

    assert jobs._store is store
    items = feed.recent()
    assert any("已重新执行" in it["text"] and "job_ok" in it["text"] for it in items), items
    assert any("中断，未重跑" in it["text"] and "job_bad" in it["text"] for it in items), items
    # 重跑的新任务已落库（running 或瞬完 completed）；job_bad 已标 interrupted
    assert all(j["task_id"] != "job_bad" for j in store.running())
    jobs.shutdown()
    store.close()
    feed.close()


def test_recover_background_jobs_no_manager_is_noop(tmp_path):
    from yibao_brain.feed import FeedStore
    from yibao_brain.jobstore import JobsStore
    from yibao_brain.server import _recover_background_jobs

    feed = FeedStore(str(tmp_path / "feed.db"))
    store = JobsStore(str(tmp_path / "jobs.db"))
    _recover_background_jobs(feed, None, store, lambda e: None)  # 不抛
    assert feed.recent() == []
    store.close()
    feed.close()


def test_serve_async_invoke_context_injects_into_next_run(tmp_path):
    """invoke_context 暂存描述 → 下一次 run 的 LLM 输入带 [屏幕上下文] 前缀。"""
    provider = FakeProvider(chunks=["你好"])
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "run", "text": "这个报错什么意思"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            invoke_context_text="用户在看 VS Code 的报错弹窗",
        )
    )
    assert provider.astream_calls, out
    first_msgs = provider.astream_calls[0]["messages"]
    assert any("[屏幕上下文] 用户在看 VS Code 的报错弹窗" in str(m.get("content"))
               for m in first_msgs), first_msgs


def test_consume_invoke_context_fresh_once_and_stale():
    """一次性：新鲜→返回并清空；再取→None；过期→None 并清空。"""
    import time

    from yibao_brain.server import _consume_invoke_context

    stash = {"text": "用户在看浏览器", "ts": time.time()}
    assert _consume_invoke_context(stash) == "用户在看浏览器"
    assert _consume_invoke_context(stash) is None  # 已一次性清空
    stale = {"text": "旧屏幕", "ts": time.time() - 120}
    assert _consume_invoke_context(stale) is None
    assert stale["text"] is None


def test_serve_async_invoke_context_branch_silent_without_host(tmp_path):
    """use_real=False（无 host/vision）：invoke_context 分支静默跳过，不炸。"""
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "invoke_context"}, {"type": "ping"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert any(m.get("type") == "pong" for m in out)


def test_serve_async_feed_feedback_roundtrip(tmp_path):
    """feed_feedback IPC 往返：👎 落 meta，坏 id 返 ok=False。"""
    _seed_feed(tmp_path / "a.db", [("reminder", "坐久了", {"type": "health_nudge"})])
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "feed_feedback", "id": 1, "feedback": "down"},
                {"type": "feed_feedback", "id": 999, "feedback": "down"},
                {"type": "feed"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    receipts = [m for m in out if m["type"] == "feed_feedback_set"]
    assert {"type": "feed_feedback_set", "id": 1, "ok": True} in receipts
    assert {"type": "feed_feedback_set", "id": 999, "ok": False} in receipts
    feed_msg = [m for m in out if m["type"] == "feed"][0]
    assert feed_msg["items"][0]["meta"].get("feedback") == "down"


def test_system_prompt_distinguishes_frontmost_from_visible():
    from yibao_brain.loop import SYSTEM_PROMPT

    assert "可见窗口" in SYSTEM_PROMPT and "前台应用" in SYSTEM_PROMPT


def test_config_perception_screen_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain.config import load_settings

    s = load_settings()
    assert s.get("perception.screen") is False
    assert isinstance(s.get("perception.blacklist"), list)


def test_config_perception_screen_keys_saveable(tmp_path, monkeypatch):
    """新键是已知键：save_settings 落 perception.screen/blacklist，未知键仍被拒。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain.config import load_settings, save_settings

    save_settings({"perception.screen": True,
                   "perception.blacklist": ["com.example.bank"],
                   "perception.unknown": True})
    s = load_settings()
    assert s["perception.screen"] is True
    assert s["perception.blacklist"] == ["com.example.bank"]
    assert "perception.unknown" not in s


# ---------- 截图即问（Task 3：snip_capture / vision_query 分支）----------


def test_peek_snip_fresh_stale_empty():
    """_peek_snip：新鲜→返回且不清空（可追问）；过期→None 并清空；空→None。"""
    from yibao_brain.server import _peek_snip

    stash = {"b64": "data:image/png;base64,AAA", "ts": time.time()}
    assert _peek_snip(stash) == "data:image/png;base64,AAA"
    assert _peek_snip(stash) == "data:image/png;base64,AAA"  # 不清空，可多次提问
    stale = {"b64": "data:image/png;base64,BBB", "ts": time.time() - 9999}
    assert _peek_snip(stale) is None
    assert stale["b64"] is None
    assert _peek_snip({"b64": None, "ts": 0.0}) is None


class _FakeVisionCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, model, messages, **kw):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))])


class _FakeVisionClient:
    def __init__(self, content):
        self.model = "fake-v"
        self.client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeVisionCompletions(content)))


def test_serve_async_vision_query_answers_with_stashed_snip(tmp_path):
    """vision_query：暂存截图 + 问题 → final_reply 事件带答案 + run_done 复位。"""
    import yibao_brain.server as srv

    srv.snip_ctx.update({"b64": "data:image/png;base64,AAA", "ts": time.time()})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 7, "type": "vision_query", "question": "这是什么？"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            vision_client=_FakeVisionClient("图上是一个对话框"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    fr = next(e for e in evs if e["kind"] == "final_reply")
    assert "对话框" in fr["text"]
    assert out[-1] == {"type": "run_done", "id": 7}


def test_serve_async_vision_query_stale_snip_errors(tmp_path):
    """vision_query：无暂存截图 → error 事件提示重新框选 + run_done。"""
    import yibao_brain.server as srv

    srv.snip_ctx.update({"b64": None, "ts": 0.0})
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 8, "type": "vision_query", "question": "q"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            vision_client=_FakeVisionClient("不应被调用"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    err = next(e for e in evs if e["kind"] == "error")
    assert "重新框选" in err["text"]
    assert out[-1] == {"type": "run_done", "id": 8}


def test_serve_async_snip_capture_silent_without_host(tmp_path):
    """use_real=False（无 host）：snip_capture 分支静默跳过，不炸。"""
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "snip_capture", "left": 0, "top": 0, "width": 10, "height": 10},
                {"type": "ping"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert any(m.get("type") == "pong" for m in out)


_HELD_DONE = threading.Event()  # 挂起态 reader 的放行闸：done 置位 → 读线程返回 None 结束


def _held_reader():
    _HELD_DONE.clear()

    def _r():
        import time as _t
        while not _HELD_DONE.is_set():
            _t.sleep(0.01)
        return None

    return _r, None


def _held_reader_done():
    _HELD_DONE.set()


def test_mobile_submit_run_uses_mobile_surface(tmp_path):
    """serve_async 内 /v1/chat 走 mobile surface：受理事件带 surface=mobile、final_reply、run_done。
    （interrupt 域内语义由 test_mobile_interrupt_scoped_to_mobile_surface 覆盖。）"""
    async def main():
        out = []
        # 直接驱动 serve_async 太重；此处借 http_enabled 走真 HTTP（端口 19862 避冲突）
        import os
        os.environ["YIBAO_HTTP_PORT"] = "19862"
        provider = FakeProvider(text="手机你好")
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _held_reader()[0], lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "m.db"), provider=provider, http_enabled=True))
            await asyncio.sleep(0.4)  # 等服务起
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                async with sess.post("http://127.0.0.1:19862/v1/chat",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"text": "你好", "conversation_id": "c9"}) as r:
                    body = await r.json()
                    assert r.status == 200 and body["run_id"].startswith("mob_")
            await asyncio.sleep(0.3)
            surfaces = [m.get("surface") for m in out if m.get("type") == "event"]
            assert "mobile" in surfaces
            kinds = [m["event"]["kind"] for m in out if m.get("type") == "event" and m.get("surface") == "mobile"]
            assert "final_reply" in kinds or "final_reply_chunk" in kinds
            assert any(m.get("type") == "run_done" for m in out)
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            _held_reader_done()
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_mobile_interrupt_scoped_to_mobile_surface(tmp_path):
    """_interrupt_mobile 域内打断（真 HTTP 路径）：
    负路径——pet run 在跑时手机 /v1/interrupt 返回 interrupted=false，桌面对话照常完成；
    正路径——mobile run 在跑时打断返回 true，interrupted 事件带 surface=mobile。"""
    import os
    import queue as _q
    os.environ["YIBAO_HTTP_PORT"] = "19863"
    provider = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.08)
    import yibao_brain.server as S

    orig_load = S.load_settings
    S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
    inbox = _q.Queue()  # 测试可控的 stdin：put 消息 = 壳投递，put None = stdin 关闭

    async def main():
        out = []
        serve_task = asyncio.ensure_future(S.serve_async(
            inbox.get, lambda m: out.append(m), use_real=False,
            db_path=str(tmp_path / "mi.db"), provider=provider, http_enabled=True))
        try:
            await asyncio.sleep(0.4)  # 等服务起
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # 负路径：pet run 在跑（慢流式 0.08s/chunk），手机打断不误伤
                inbox.put({"id": 11, "type": "run", "surface": "pet", "text": "桌面长回答"})
                await asyncio.sleep(0.15)  # 此刻 pet run 仍在流式中
                assert not any(m.get("type") == "run_done" for m in out)
                async with sess.post("http://127.0.0.1:19863/v1/interrupt",
                                     headers={"X-Yibao-Token": "mtok"}, json={}) as r:
                    assert r.status == 200
                    assert await r.json() == {"ok": True, "interrupted": False}
                for _ in range(100):
                    if any(m.get("type") == "run_done" and m.get("id") == 11 for m in out):
                        break
                    await asyncio.sleep(0.05)
                assert any(m.get("type") == "run_done" and m.get("id") == 11 for m in out)  # 照常完成
                kinds = [m["event"]["kind"] for m in out if m.get("type") == "event"]
                assert "final_reply" in kinds and "interrupted" not in kinds

                # 正路径：mobile run 在跑，手机打断生效
                async with sess.post("http://127.0.0.1:19863/v1/chat",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"text": "手机长回答", "conversation_id": "c1"}) as r:
                    body = await r.json()
                    assert r.status == 200 and body["run_id"].startswith("mob_")
                await asyncio.sleep(0.15)  # 此刻 mobile run 仍在流式中
                async with sess.post("http://127.0.0.1:19863/v1/interrupt",
                                     headers={"X-Yibao-Token": "mtok"}, json={}) as r:
                    assert r.status == 200
                    assert await r.json() == {"ok": True, "interrupted": True}
                for _ in range(100):
                    if any(m.get("type") == "run_done" and m.get("id") == body["run_id"] for m in out):
                        break
                    await asyncio.sleep(0.05)
                assert any(m.get("type") == "run_done" and m.get("id") == body["run_id"] for m in out)
                assert any(m.get("type") == "event" and m.get("surface") == "mobile"
                           and m["event"]["kind"] == "interrupted" for m in out)
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)  # 结束 stdin 读线程
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_confirm_mobile_unknown_bound_and_shell_cross_surface_dedup(tmp_path):
    """/v1/confirm 两个负路径（真 HTTP 路径，serve_async 级）：
    ① 未知 id 且 early_answers 已满 32 条 → 404（垃圾 id 不无界堆积）；
    ② 壳 confirm_batch 已处理的 cid → 手机端再点 → 404（跨端防重，答案不滞留）。"""
    import os
    import queue as _q
    os.environ["YIBAO_HTTP_PORT"] = "19864"
    import yibao_brain.server as S

    orig_load = S.load_settings
    S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
    inbox = _q.Queue()

    async def main():
        out = []
        serve_task = asyncio.ensure_future(S.serve_async(
            inbox.get, lambda m: out.append(m), use_real=False,
            db_path=str(tmp_path / "cm.db"), provider=FakeProvider(text="ok"), http_enabled=True))
        try:
            await asyncio.sleep(0.4)  # 等服务起
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # ② 先做跨端防重（干净状态）：壳 confirm_batch 兑现/缓存 shell_1
                inbox.put({"type": "confirm_batch",
                           "items": [{"id": "shell_1", "approved": True, "remember": False}]})
                for _ in range(100):
                    if any(m.get("type") == "confirm_batched" for m in out):
                        break
                    await asyncio.sleep(0.05)
                assert any(m.get("type") == "confirm_batched" for m in out)
                async with sess.post("http://127.0.0.1:19864/v1/confirm",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"id": "shell_1", "approved": True}) as r:
                    assert r.status == 404  # 壳已处理 → 跨端防重

                # ① 未知 id 灌满 early_answers（32 条上界；shell_1 已占 1 条 → 再收 31 条）
                for i in range(31):
                    async with sess.post("http://127.0.0.1:19864/v1/confirm",
                                         headers={"X-Yibao-Token": "mtok"},
                                         json={"id": f"junk_{i}", "approved": False}) as r:
                        assert r.status == 200  # 上界内：当作早到答案收下
                async with sess.post("http://127.0.0.1:19864/v1/confirm",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"id": "junk_31", "approved": False}) as r:
                    assert r.status == 404  # 已满：未知/垃圾 id 不再堆积

                # 垃圾 id 不该出现在待批列表（confirm_meta 只由 batch_confirmer 登记）
                async with sess.get("http://127.0.0.1:19864/v1/state",
                                    headers={"X-Yibao-Token": "mtok"}) as r:
                    body = await r.json()
                    assert r.status == 200 and body["pending"] == []
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)  # 结束 stdin 读线程
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_confirm_meta_summary_readable_k_v(tmp_path):
    """confirm_meta.summary 可读化：params 非空 dict → k=v 逗号形式（不再是 dict-repr），
    空 params 回落 tool_id。手机审批页直接展示该字段。"""

    async def main():
        import os

        from yibao_brain.tools import Tool
        from yibao_brain.ipc import ActionResult

        class DangerTool(Tool):
            id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
            def run(self, params, ctx): return ActionResult(success=True)

        class BareTool(Tool):
            id = "bare"; description = "无参占位"; default_risk = RiskLevel.L3_HIGH
            def run(self, params, ctx): return ActionResult(success=True)

        os.environ["YIBAO_HTTP_PORT"] = "19868"

        # 两轮顺序确认（同一 run 内）：第一轮 t1 有参，第二轮 t2 空 dict
        class _ChainProvider:
            def __init__(self, steps):
                self._steps, self._n = list(steps), 0

            def chat(self, messages, tools=None):
                i = min(self._n, len(self._steps) - 1); self._n += 1
                return self._steps[i].chat(messages, tools)

            async def astream(self, messages, tools=None):
                i = min(self._n, len(self._steps) - 1); self._n += 1
                async for d in self._steps[i].astream(messages, tools):
                    yield d

        provider = _ChainProvider([
            FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="danger",
                                              params={"path": "/tmp/x", "force": True})]),
            FakeProvider(tool_calls=[ToolCall(id="t2", tool_id="bare", params={})]),
            FakeProvider(text="done"),
        ])
        out: list = []
        inbox = queue.Queue()
        inbox.put({"id": 1, "type": "run", "text": "做两件危险事"})
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        summaries: dict = {}

        async def grab_pending_id() -> str:
            # 确认挂起期间拉 /v1/state，把当前挂起项按 id 汇入 summaries
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                async with sess.get("http://127.0.0.1:19868/v1/state",
                                    headers={"X-Yibao-Token": "mtok"}) as r:
                    assert r.status == 200
                    body = await r.json()
                    for p in body["pending"]:
                        summaries[p["id"]] = p["summary"]
                    return body["pending"][0]["id"]

        def writer(m):
            out.append(m)

        async def wait_for(pred):
            for _ in range(200):
                if pred():
                    return
                await asyncio.sleep(0.05)

        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                inbox.get, writer, use_real=False,
                db_path=str(tmp_path / "sm.db"), provider=provider, http_enabled=True,
                skills_factory=lambda: _registry_with(DangerTool(), BareTool())))
            # 第一轮：确认挂起时拉 state → 批准 t1
            await wait_for(lambda: any(m.get("type") == "event"
                                       and m["event"].get("kind") == "confirmation_needed" for m in out))
            cid1 = await grab_pending_id()
            inbox.put({"type": "confirm_batch", "items": [{"id": cid1, "approved": True, "remember": False}]})
            # 第二轮：t2 确认挂起时拉 state → 批准 t2 → 等 run 收尾
            await wait_for(lambda: sum(1 for m in out if m.get("type") == "event"
                                       and m["event"].get("kind") == "confirmation_needed") >= 2)
            cid2 = await grab_pending_id()
            inbox.put({"type": "confirm_batch", "items": [{"id": cid2, "approved": True, "remember": False}]})
            await wait_for(lambda: any(m.get("type") == "run_done" for m in out))
            inbox.put(None)  # 结束 stdin 读线程
            await asyncio.wait_for(serve_task, 5)
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)

        # action id 由 loop 自动生成（act_*），按 summary 内容断言
        vals = list(summaries.values())
        assert "path=/tmp/x, force=True" in vals  # k=v 可读形式（非 dict-repr）
        assert "bare" in vals                     # 空 params → 回落 tool_id

    asyncio.run(main())


def test_mobile_end_to_end_sse_receives_stream(tmp_path):
    """/v1/chat → 经 EventTap → /v1/events 收到 final_reply(_chunk) 与 run_done 帧。"""

    async def main():
        import os

        # brief 给的 19863 与既有 test_mobile_interrupt_scoped_to_mobile_surface 冲突 → 用 19865
        os.environ["YIBAO_HTTP_PORT"] = "19865"
        out = []
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _held_reader()[0], lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "e2e.db"), provider=FakeProvider(text="端到端回复"),
                http_enabled=True))
            await asyncio.sleep(0.4)
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                events = await sess.get("http://127.0.0.1:19865/v1/events", params={"token": "mtok"})
                chat = await sess.post("http://127.0.0.1:19865/v1/chat",
                                       headers={"X-Yibao-Token": "mtok"}, json={"text": "你好"})
                assert chat.status == 200
                buf = b""
                deadline = time.monotonic() + 5
                while b"run_done" not in buf and time.monotonic() < deadline:
                    chunk = await asyncio.wait_for(events.content.read(64), 2)
                    buf += chunk
                assert b"final_reply" in buf  # 流式回复帧（kind=final_reply 或 final_reply_chunk）
                assert b'"surface": "mobile"' in buf  # 帧带信封归属字段（spec §4.3）
                assert b"run_done" in buf
                events.close()
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            _held_reader_done()
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_mobile_chat_does_not_consume_invoke_context(tmp_path):
    """手机 /v1/chat 不消费截图唤起上下文：invoke_ctx 是桌面截图唤起的一次性暂存，
    手机偷吃会让桌面下一次 run 拿不到、还会把 [屏幕上下文] 注进手机回复。
    断言：手机回复（事件与 LLM 输入）均不含 [屏幕上下文]，且随后的桌面 run 仍拿到它。"""
    import os
    import queue as _q

    os.environ["YIBAO_HTTP_PORT"] = "19866"
    provider = FakeProvider(text="手机回复")
    import yibao_brain.server as S

    orig_load = S.load_settings
    S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
    inbox = _q.Queue()

    async def main():
        out = []
        serve_task = asyncio.ensure_future(S.serve_async(
            inbox.get, lambda m: out.append(m), use_real=False,
            db_path=str(tmp_path / "ic.db"), provider=provider, http_enabled=True,
            invoke_context_text="用户在看 VS Code 的报错弹窗"))
        try:
            await asyncio.sleep(0.4)  # 等服务起
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                async with sess.post("http://127.0.0.1:19866/v1/chat",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"text": "你好", "conversation_id": "c1"}) as r:
                    body = await r.json()
                    assert r.status == 200 and body["run_id"].startswith("mob_")
            for _ in range(100):
                if any(m.get("type") == "run_done" and m.get("id") == body["run_id"] for m in out):
                    break
                await asyncio.sleep(0.05)
            # 手机 run 的 LLM 输入与回复事件都不含 [屏幕上下文]
            assert provider.astream_calls
            mobile_msgs = provider.astream_calls[0]["messages"]
            assert not any("[屏幕上下文]" in str(m.get("content")) for m in mobile_msgs), mobile_msgs
            finals = [m["event"].get("text") for m in out
                      if m.get("type") == "event" and m.get("surface") == "mobile"
                      and m["event"].get("kind") in ("final_reply", "final_reply_chunk")]
            assert finals and not any("[屏幕上下文]" in (t or "") for t in finals), finals

            # 桌面 run 仍能拿到（未被手机消费）
            inbox.put({"id": 21, "type": "run", "surface": "pet", "text": "桌面问"})
            for _ in range(100):
                if any(m.get("type") == "run_done" and m.get("id") == 21 for m in out):
                    break
                await asyncio.sleep(0.05)
            assert len(provider.astream_calls) >= 2
            desktop_msgs = provider.astream_calls[1]["messages"]
            assert any("[屏幕上下文] 用户在看 VS Code 的报错弹窗" in str(m.get("content"))
                       for m in desktop_msgs), desktop_msgs
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)  # 结束 stdin 读线程
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_mobile_interrupt_does_not_kill_desktop_while_mobile_queued(tmp_path):
    """spec §14：pet 慢流式在跑、mobile chat 跨 surface 排队中——此刻手机 interrupt
    应返回 False 且 pet 轮不死（旧行为会连环杀：preempt 顶掉 pet + 排队的 mobile 秒跳）。"""
    inbox = queue.Queue()
    inbox.put({"id": 10, "type": "run", "surface": "pet", "text": "长任务"})

    def _reader():
        return inbox.get()  # 阻塞读：测试全程可控，结束时放 None 收尾（= inbox.get 本身）

    async def main():
        import os

        os.environ["YIBAO_HTTP_PORT"] = "19867"
        out = []
        import yibao_brain.server as S

        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(S.serve_async(
                _reader, lambda m: out.append(m), use_real=False,
                db_path=str(tmp_path / "q.db"),
                provider=FakeProvider(chunks=["桌", "面", "回", "复"], delay=0.3),
                http_enabled=True))
            await asyncio.sleep(0.4)
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # pet 在跑（0.3s/chunk × 4 ≈ 1.2s 窗口），手机 chat 跨 surface 排队
                r = await sess.post("http://127.0.0.1:19867/v1/chat",
                                    headers={"X-Yibao-Token": "mtok"}, json={"text": "手机消息"})
                assert r.status == 200
                ir = await sess.post("http://127.0.0.1:19867/v1/interrupt",
                                     headers={"X-Yibao-Token": "mtok"}, json={})
                assert (await ir.json()) == {"ok": True, "interrupted": False}  # 排队中不误杀
            # 等桌面轮自然跑完：run_done id=10 到达且 final_reply 完整
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if any(m.get("type") == "run_done" and m.get("id") == 10 for m in out):
                    break
                await asyncio.sleep(0.05)
            assert any(m.get("type") == "run_done" and m.get("id") == 10 for m in out), "桌面轮被误杀或未完成"
            assert any(m.get("type") == "event" and m.get("surface") == "pet"
                       and m.get("event", {}).get("kind") == "final_reply" for m in out), "桌面回复被截断"
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_http_pair_info_ipc(tmp_path):
    """http_pair_info IPC：回 lan_ip/port/bind（桌面设置页配对 URL 用）。
    monkeypatch load_settings 隔离真实 settings.json（本机 http.bind 可能非默认值）。"""
    async def main():
        import os

        import yibao_brain.server as S

        out = []
        orig_load = S.load_settings
        S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
        try:
            serve_task = asyncio.ensure_future(serve_async(
                make_reader([{"id": 1, "type": "http_pair_info"}]),
                lambda m: out.append(m), use_real=False, db_path=str(tmp_path / "p.db"),
                provider=FakeProvider(text="x")))
            await asyncio.wait_for(serve_task, 5)
            msg = next(m for m in out if m.get("type") == "http_pair_info")
            assert msg["port"] == 19527 and msg["bind"] == "127.0.0.1"
            assert isinstance(msg["lan_ip"], str)  # 环境相关（可能空串），只断言类型与格式
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)  # 防上游用例失败泄漏端口，覆盖默认 19527 断言

    asyncio.run(main())


def test_pick_en_ip_prefers_physical_over_utun():
    """_lan_ip 的网卡挑选：物理网卡（en*）私网地址优先，跳过 VPN（utun）与链路本地。"""
    from yibao_brain.server import _pick_en_ip

    sample = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
\tinet 192.168.255.10 --> 192.168.255.9 netmask 0xfffffff8
en5: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.1.7 netmask 0xffff0000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.31.52 netmask 0xffffff00 broadcast 192.168.31.255
"""
    assert _pick_en_ip(sample) == "192.168.31.52"  # 跳过 lo/utun/169.254，取 en0
    assert _pick_en_ip("") == ""


# ---------- （mobile M2）/v1/feed + /v1/reminders + /v1/memories ----------


class _DirectInvoker:
    """直调闭包最小 invoker：propose/decide/execute 全记录，decide 与结果可换。"""

    def __init__(self, decision=None, result=None):
        from yibao_brain.ipc import Action, ActionResult
        from yibao_brain.safety import Decision

        self._Action, self._ActionResult, self._Decision = Action, ActionResult, Decision
        self.decision = decision or self._Decision.AUTO
        self.result = result or self._ActionResult(success=True, data={})
        self.calls = []

    def propose(self, call):
        self.calls.append(("propose", call.tool_id, dict(call.params)))
        return self._Action(id=call.id, tool_id=call.tool_id)

    def decide(self, action):
        self.calls.append(("decide", action.tool_id))
        return self.decision

    def execute(self, action, params):
        self.calls.append(("execute", action.tool_id, dict(params)))
        return self.result


def test_reminders_payloads_direct_call_and_degradation(monkeypatch):
    """（mobile M2）/v1/reminders 载荷真形状：直连（_bridge_save 同款 propose/decide/execute）
    list rows→items；白名单缺席/执行失败/被拦 → list 降级空列表不炸、cancel 带 error 供路由转 500。"""
    import yibao_brain.plugins as plugins
    from yibao_brain.ipc import ActionResult
    from yibao_brain.plugins import ApiMethod
    from yibao_brain.safety import Decision
    from yibao_brain.server import _reminders_cancel_payload, _reminders_list_payload

    def _seed_api(name, handler):
        monkeypatch.setitem(plugins._API, name, ApiMethod(
            name=name, handler=handler, direct=True, intent=None, risk=None,
            plugin_id="reminders"))

    agent = SimpleNamespace(invoker=_DirectInvoker())

    # 未种白名单（插件缺席）→ list 空列表不 500、cancel 带可读 error。
    # 显式摘除：同进程上游用例可能已 load_plugins 灌入真白名单，不能依赖全局缺省
    monkeypatch.delitem(plugins._API, "reminders.list", raising=False)
    monkeypatch.delitem(plugins._API, "reminders.cancel", raising=False)
    assert _run_async(_reminders_list_payload(agent)) == {"ok": True, "items": []}
    out = _run_async(_reminders_cancel_payload(agent, "r9"))
    assert out["ok"] is False and "不可用" in out["error"]

    # list：直调成功 → rows 映射 items；走的是 reminders.list handler
    _seed_api("reminders.list", "reminders.list")
    agent.invoker = _DirectInvoker(result=ActionResult(success=True, data={
        "rows": [{"id": "r1", "text": "喝水", "when": "每天 09:00"}]}))
    payload = _run_async(_reminders_list_payload(agent))
    assert payload == {"ok": True, "items": [{"id": "r1", "text": "喝水", "when": "每天 09:00"}]}
    assert agent.invoker.calls[0] == ("propose", "reminders.list", {})

    # list：执行失败（如存储缺席）→ 降级空列表，不抛
    agent.invoker = _DirectInvoker(result=ActionResult(success=False, error="底座未提供提醒存储"))
    assert _run_async(_reminders_list_payload(agent)) == {"ok": True, "items": []}

    # cancel：成功 → {"ok": True}；参数 id 透传 handler
    _seed_api("reminders.cancel", "reminders.cancel")
    agent.invoker = _DirectInvoker(result=ActionResult(success=True, data={"id": "r1"}))
    assert _run_async(_reminders_cancel_payload(agent, "r1")) == {"ok": True}
    assert agent.invoker.calls == [("propose", "reminders.cancel", {"id": "r1"}),
                                   ("decide", "reminders.cancel"),
                                   ("execute", "reminders.cancel", {"id": "r1"})]

    # cancel：没找到（执行失败）→ {"ok": False, "error"}（路由层转 500）
    agent.invoker = _DirectInvoker(result=ActionResult(success=False, error="没找到待触发的提醒：rX"))
    out = _run_async(_reminders_cancel_payload(agent, "rX"))
    assert out == {"ok": False, "error": "没找到待触发的提醒：rX"}

    # cancel：策略非 AUTO（浏览场景无确认通道）→ 拒绝执行，error 带原因
    agent.invoker = _DirectInvoker(decision=Decision.CONFIRM)
    out = _run_async(_reminders_cancel_payload(agent, "r1"))
    assert out["ok"] is False and "确认" in out["error"]
    assert ("execute", "reminders.cancel", {"id": "r1"}) not in agent.invoker.calls


def test_mobile_feed_endpoint_same_shape_as_ipc(tmp_path):
    """（mobile M2）/v1/feed 与桌面 feed IPC 完全同形：_seed_feed 预写两条 → HTTP 拉流
    与 stdio feed 消息 items/stats/running_tasks 逐键一致（组装收敛在 _mobile_feed）；
    reminders 测试态未装插件 → 空列表不 500；memories → 空桶形状（FakeMemory 无记录）。"""
    import os
    import queue as _q

    os.environ["YIBAO_HTTP_PORT"] = "19867"  # 19863-19866 已被上游用例占用
    _seed_feed(tmp_path / "m2.db", [
        ("task", "任务A完成", {"task": {"id": "a"}}),
        ("event", "事件B", {}),
    ])
    import yibao_brain.server as S

    orig_load = S.load_settings
    S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
    inbox = _q.Queue()

    async def main():
        out = []
        serve_task = asyncio.ensure_future(S.serve_async(
            inbox.get, lambda m: out.append(m), use_real=False,
            db_path=str(tmp_path / "m2.db"), provider=FakeProvider(), http_enabled=True))
        try:
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # 等服务起（轮询 /v1/health，固定 sleep 在机器慢时会假红）
                for _ in range(50):
                    try:
                        async with sess.get("http://127.0.0.1:19867/v1/health",
                                            headers={"X-Yibao-Token": "mtok"}) as r:
                            if r.status == 200:
                                break
                    except aiohttp.ClientConnectorError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP 面未在 5s 内就绪")
                async with sess.get("http://127.0.0.1:19867/v1/feed",
                                    headers={"X-Yibao-Token": "mtok"}) as r:
                    assert r.status == 200
                    feed_body = await r.json()
                async with sess.get("http://127.0.0.1:19867/v1/reminders",
                                    headers={"X-Yibao-Token": "mtok"}) as r:
                    assert r.status == 200
                    reminders_body = await r.json()
                async with sess.get("http://127.0.0.1:19867/v1/memories",
                                    headers={"X-Yibao-Token": "mtok"}) as r:
                    assert r.status == 200
                    memories_body = await r.json()
            inbox.put({"type": "feed"})  # 桌面 IPC 同源对照
            for _ in range(200):
                if any(m.get("type") == "feed" for m in out):
                    break
                await asyncio.sleep(0.05)
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)
            await asyncio.wait_for(serve_task, 5)

        ipc = next(m for m in out if m.get("type") == "feed")
        assert feed_body["ok"] is True
        assert feed_body["items"] == ipc["items"]  # 与桌面 feed IPC 完全同形
        assert feed_body["stats"] == ipc["stats"]
        assert feed_body["running_tasks"] == ipc["running_tasks"]
        assert feed_body["stats"]["unread"] == 2 and len(feed_body["items"]) == 2
        assert reminders_body == {"ok": True, "items": []}  # 插件缺席 → 空列表不 500
        assert memories_body == {"ok": True, "items": []}

    asyncio.run(main())


# ---------- P2 B1：coding 审批统一进 L2 确认体系（perm_ 路由 + running_tasks）----------


def _fake_coding_runner_module(monkeypatch, perms):
    """挂一个假的 yibao_plugin_coding__runner 模块单例（生产由 coding 插件 _sibling 加载），
    供 server._fulfill_coding_perm 路由 _PERM；monkeypatch teardown 自动还原。"""
    import sys
    import types
    mod = types.ModuleType("yibao_plugin_coding__runner")
    mod._PERM = perms
    monkeypatch.setitem(sys.modules, "yibao_plugin_coding__runner", mod)
    return mod


def test_fulfill_coding_perm_routes_and_idempotent(monkeypatch):
    """perm_ cid 直写插件 _PERM（allow + event.set）；与 coding.decide 双通道幂等——
    先到（allow 已非 None）不被后到覆盖；未知 cid / 插件未加载 → False。"""
    import sys
    import threading as _th
    from yibao_brain import server as S

    ev = _th.Event()
    perms = {"perm_abc_1": {"event": ev, "allow": None}}
    _fake_coding_runner_module(monkeypatch, perms)
    assert S._fulfill_coding_perm("perm_abc_1", True) is True
    assert perms["perm_abc_1"]["allow"] is True and ev.is_set()
    # 双通道幂等：后到不覆盖先到
    assert S._fulfill_coding_perm("perm_abc_1", False) is True
    assert perms["perm_abc_1"]["allow"] is True
    # 未知 cid / 无 _PERM 注册表
    assert S._fulfill_coding_perm("perm_ghost_9", True) is False
    monkeypatch.delitem(sys.modules, "yibao_plugin_coding__runner", raising=False)
    assert S._fulfill_coding_perm("perm_abc_1", True) is False


def test_serve_confirm_batch_routes_coding_perm(tmp_path, monkeypatch):
    """confirm_batch 全链路：items 里 perm_ 前缀的 cid 路由插件 _PERM 兑现，
    不落 pending_confirms/early_answers；回执 confirm_batched 照常。"""
    import threading as _th

    ev = _th.Event()
    perms = {"perm_s1_1": {"event": ev, "allow": None}}
    _fake_coding_runner_module(monkeypatch, perms)
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "confirm_batch",
                          "items": [{"id": "perm_s1_1", "approved": True}]}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    assert perms["perm_s1_1"]["allow"] is True and ev.is_set()
    assert any(m.get("type") == "confirm_batched" and m.get("ok") for m in out)


def test_serve_feed_includes_running_coding_session(tmp_path, monkeypatch):
    """_running_tasks 追加 coding 运行中会话：sessions 表 running 行列出，done 不列。"""
    import sqlite3

    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    cdir = tmp_path / "plugins" / "coding"
    cdir.mkdir(parents=True)
    conn = sqlite3.connect(str(cdir / "data.db"))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, prompt TEXT, status TEXT,"
                 " created_at INTEGER)")
    conn.execute("INSERT INTO sessions VALUES ('cs1', '改登录 bug', 'running', 1700000000)")
    conn.execute("INSERT INTO sessions VALUES ('cs2', '旧会话', 'done', 1700000001)")
    conn.commit()
    conn.close()

    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    feed = [m for m in out if m["type"] == "feed"][0]
    coding_tasks = [t for t in feed["running_tasks"] if t["kind"] == "coding"]
    assert len(coding_tasks) == 1
    assert coding_tasks[0]["id"] == "cs1"
    assert coding_tasks[0]["label"] == "编码会话"
    assert coding_tasks[0]["prompt"] == "改登录 bug"
    assert coding_tasks[0]["status"] == "running"


def test_serve_feed_running_coding_session_waiting_flag(tmp_path, monkeypatch):
    """P2 督导补遗：_running_tasks 的 coding 条目，_PERM 有该 sid 挂起审批（allow is None）
    → waiting: true；无挂起 / 已裁决（allow 非 None）→ 不带 waiting 键（additive）。"""
    import sqlite3
    import threading as _th

    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    cdir = tmp_path / "plugins" / "coding"
    cdir.mkdir(parents=True)
    conn = sqlite3.connect(str(cdir / "data.db"))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, prompt TEXT, status TEXT,"
                 " created_at INTEGER)")
    conn.execute("INSERT INTO sessions VALUES ('cs-w', '等审批的会话', 'running', 1700000002)")
    conn.execute("INSERT INTO sessions VALUES ('cs-r', '纯跑会话', 'running', 1700000001)")
    conn.commit()
    conn.close()
    perms = {
        "perm_cs-w_3": {"event": _th.Event(), "allow": None},    # 挂起 → waiting
        "perm_cs-r_1": {"event": _th.Event(), "allow": True},    # 已裁决 → 不算 waiting
    }
    _fake_coding_runner_module(monkeypatch, perms)

    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    feed = [m for m in out if m["type"] == "feed"][0]
    tasks = {t["id"]: t for t in feed["running_tasks"] if t["kind"] == "coding"}
    assert tasks["cs-w"]["waiting"] is True
    assert "waiting" not in tasks["cs-r"]


def test_mobile_state_merges_coding_perm_pending(tmp_path, monkeypatch):
    """_PERM 挂起项只读合并进 /v1/state 的 pending（真 HTTP 路径，serve_async 级）：
    挂起（allow is None）出现且字段齐（id/tool_id/summary/risk/created_at），
    已裁决（allow 非 None）不出现；手机裁决后条目从 pending 收敛。
    顺带钉死 _confirm_mobile 的 perm_ 分支：未知 rid → 404（不再恒 200 假 ok）。"""
    import os
    import threading as _th
    os.environ["YIBAO_HTTP_PORT"] = "19869"  # 19862-19868 已被上游用例占用
    import yibao_brain.server as S

    ev = _th.Event()
    perms = {
        "perm_cs1_1": {"event": ev, "allow": None, "tool": "Bash",
                       "summary": "rm -rf /tmp/x", "params": {"command": "rm -rf /tmp/x"},
                       "created_at": 1700000000},
        "perm_cs1_2": {"event": _th.Event(), "allow": True, "tool": "Write",
                       "summary": "/tmp/y", "params": {}, "created_at": 1700000001},
    }
    _fake_coding_runner_module(monkeypatch, perms)
    orig_load = S.load_settings
    S.load_settings = lambda: {"http.token": "btok", "http.mobile_token": "mtok"}
    inbox = queue.Queue()

    async def main():
        out = []
        serve_task = asyncio.ensure_future(S.serve_async(
            inbox.get, lambda m: out.append(m), use_real=False,
            db_path=str(tmp_path / "mp.db"), provider=FakeProvider(text="ok"), http_enabled=True))
        try:
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                # 等服务起（轮询 /v1/health，固定 sleep 在机器慢时会假红）
                for _ in range(50):
                    try:
                        async with sess.get("http://127.0.0.1:19869/v1/health",
                                            headers={"X-Yibao-Token": "mtok"}) as r:
                            if r.status == 200:
                                break
                    except aiohttp.ClientConnectorError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP 面未在 5s 内就绪")

                async def get_state():
                    async with sess.get("http://127.0.0.1:19869/v1/state",
                                        headers={"X-Yibao-Token": "mtok"}) as r:
                        assert r.status == 200
                        return (await r.json())["pending"]

                pend = await get_state()
                assert [p["id"] for p in pend] == ["perm_cs1_1"]  # 已裁决项不出现
                item = pend[0]
                assert item["tool_id"] == "coding"
                assert item["summary"] == "rm -rf /tmp/x"
                assert item["risk"] == 1
                assert item["created_at"] == 1700000000

                # 未知 perm_ rid：兑现失败 → 404（修前恒 200 假 ok）
                async with sess.post("http://127.0.0.1:19869/v1/confirm",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"id": "perm_ghost_9", "approved": True}) as r:
                    assert r.status == 404

                # 手机裁决挂起项：200 + 写 allow + set 事件；条目从 pending 收敛
                async with sess.post("http://127.0.0.1:19869/v1/confirm",
                                     headers={"X-Yibao-Token": "mtok"},
                                     json={"id": "perm_cs1_1", "approved": True}) as r:
                    assert r.status == 200
                assert perms["perm_cs1_1"]["allow"] is True and ev.is_set()
                assert await get_state() == []
        finally:
            S.load_settings = orig_load
            os.environ.pop("YIBAO_HTTP_PORT", None)
            inbox.put(None)  # 结束 stdin 读线程
            await asyncio.wait_for(serve_task, 5)

    asyncio.run(main())


def test_speech_stop_after_final_reply_is_not_run_interrupt(tmp_path):
    """停止分离（P0）：final_reply 已产出后用户按停止 = 只停 TTS 播报——
    服务端补发的事件必须是可区分的 speech_stopped，不得把已完成的 run 标成 interrupted。
    （对照：执行中打断仍是 interrupted，由 test_serve_async_interrupt_stops_run 锁定。）"""
    from fakes import FakeVoice

    provider = FakeProvider(chunks=["甲一。", "甲二。"], delay=0.01)
    voice = FakeVoice("你好", stream_delay=0.2)  # 拉长播报窗口，interrupt 落在播报中段

    def _delayed_reader(specs):
        it = iter(specs)

        def _r():
            try:
                msg, delay = next(it)
            except StopIteration:
                return None
            if delay:
                time.sleep(delay)
            return msg

        return _r

    out = []
    _run_async(
        serve_async(
            _delayed_reader([
                ({"id": 1, "type": "run", "text": "说两句", "conversation_id": "conv-a", "tts": True}, 0.0),
                ({"type": "interrupt", "conversation_id": "conv-a"}, 0.15),  # final_reply 后、播报中按停
                (None, 1.0),
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            voice=voice,
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"
             and m.get("conversation_id") == "conv-a"]
    assert "final_reply" in kinds
    assert "speech_stopped" in kinds  # 只停播报：可区分事件
    assert "interrupted" not in kinds  # run 已完成，不得标成已打断
    assert voice.stream_interrupted is True  # 播报确实被停在中段
    assert {"type": "run_done", "id": 1, "conversation_id": "conv-a"} in out


def test_serve_async_events_carry_run_epoch_and_seq(tmp_path):
    """运行代数（P0）：同会话每次新 run 分配单调递增 run_epoch；run 流出的每个事件
    带 run_epoch + seq（seq 在 run 内单调递增），conversation_id 随信封——
    前端据此丢弃被抢占旧 run 的迟到事件（旧 epoch 不得改 UI）。"""
    provider = FakeProvider(chunks=["好"])
    out = []

    def _delayed_reader(specs):
        it = iter(specs)

        def _r():
            try:
                msg, delay = next(it)
            except StopIteration:
                return None
            if delay:
                time.sleep(delay)
            return msg

        return _r

    _run_async(
        serve_async(
            _delayed_reader([
                ({"id": 1, "type": "run", "text": "第一句", "conversation_id": "conv-a"}, 0.0),
                ({"id": 2, "type": "run", "text": "第二句", "conversation_id": "conv-a"}, 0.3),  # run1 收尾后再来
                ({"id": 3, "type": "run", "text": "别会话", "conversation_id": "conv-b"}, 0.3),
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
        )
    )
    evs = [m for m in out if m["type"] == "event"]
    assert evs, "应至少有 run 事件"
    a_epochs = sorted({m["event"]["run_epoch"] for m in evs if m.get("conversation_id") == "conv-a"})
    assert a_epochs == [1, 2]  # 同会话两次 run 代数单调递增
    b_epochs = {m["event"]["run_epoch"] for m in evs if m.get("conversation_id") == "conv-b"}
    assert b_epochs == {1}  # 代数 per 会话独立
    # 每个 (会话, epoch) 内 seq 从 1 起严格递增
    for conv, epoch in (("conv-a", 1), ("conv-a", 2), ("conv-b", 1)):
        seqs = [m["event"]["seq"] for m in evs
                if m.get("conversation_id") == conv and m["event"]["run_epoch"] == epoch]
        assert seqs == list(range(1, len(seqs) + 1)), f"{conv}#{epoch} seq 应严格递增：{seqs}"


def test_serve_async_preempted_run_events_keep_own_epoch(tmp_path):
    """抢占场景：旧 run 被打断，其迟到事件（含 interrupted）仍盖旧 epoch；
    新 run 的事件盖更新 epoch——前端凭 epoch 差即可丢弃旧 run 的迟到回复。"""
    slow = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.05)
    fast = FakeProvider(chunks=["ok"])
    state = {"n": 0}

    class _Switch:
        async def astream(self, messages, tools=None):
            state["n"] += 1
            src = slow if state["n"] == 1 else fast
            async for d in src.astream(messages, tools):
                yield d

    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "run", "text": "slow", "conversation_id": "conv-a"},
                {"id": 2, "type": "run", "text": "fast", "conversation_id": "conv-a"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=_Switch(),
        )
    )
    evs = [m for m in out if m["type"] == "event" and m.get("conversation_id") == "conv-a"]
    interrupted = next(m for m in evs if m["event"]["kind"] == "interrupted")
    final = next(m for m in evs if m["event"]["kind"] == "final_reply")
    assert interrupted["event"]["run_epoch"] < final["event"]["run_epoch"]
    assert all("run_epoch" in m["event"] and "seq" in m["event"] for m in evs)


def test_project_switch_emits_scope_notice(tmp_path, monkeypatch):
    """N1：切换工作语境必须在会话里留可见 notice（SessionScopeChanged 的最小落地）——
    不许静默改绑；notice 带会话归属，project_switched 回包照常。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"type": "project_create", "name": "甲项目", "conversation_id": "c1"},
                {"type": "project_create", "name": "乙项目", "conversation_id": "c1"},
                {"type": "project_switch", "name": "甲项目", "conversation_id": "c1"},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    notices = [m for m in out if m.get("type") == "event"
               and m.get("event", {}).get("kind") == "notice"
               and "工作语境已切换" in str(m["event"].get("text"))]
    assert len(notices) == 1
    assert "甲项目" in notices[0]["event"]["text"]
    assert notices[0].get("conversation_id") == "c1"
    assert any(m.get("type") == "project_switched" and m.get("ok") for m in out)


def test_panel_action_quiet_method_emits_no_panel(tmp_path, monkeypatch):
    """N6：quiet=true 的直调（如编辑器内保存）不发 panel 事件——
    保存回执只走 action_result 回桥，不顶掉工作台持久化快照的 rows 载荷。"""
    executed = []
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    _patch_api(monkeypatch, quiet=True)
    out = []
    _run_async(
        serve_async(
            make_reader([{"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=_pa_factory(executed, ref="tdel:list"),
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    assert executed == [{"id": "r1"}]
    assert "panel" not in [e["kind"] for e in evs]  # quiet：零 panel 事件
    assert any(e["kind"] == "action_result" and e["result"]["success"] for e in evs)
    assert out[-1] == {"type": "run_done", "id": 1}
