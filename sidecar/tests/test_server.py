import asyncio
import json
from datetime import datetime, timedelta
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
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
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
    from yibao_brain.skills import Skill, SkillRegistry
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
        second=FakeProvider(text="done"),
    )
    inbox = [
        {"id": 1, "type": "run", "text": "做危险的事"},
        {"id": 2, "type": "confirm", "confirmation_id": "x", "approved": False},
    ]
    loop = build_loop(make_reader(inbox), use_real=False, db_path=str(tmp_path / "a.db"),
                      provider=provider, skills_factory=lambda: _registry_with(DangerSkill()))
    out = []
    serve(loop, make_reader(inbox), lambda m: out.append(m))
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert "error" in kinds  # 用户拒绝后产出 error
    assert not any(m["type"] == "event" and m["event"].get("kind") == "action_result"
                   and m["event"]["result"]["data"].get("did") for m in out)


def _registry_with(*skills):
    from yibao_brain.skills import SkillRegistry
    reg = SkillRegistry()
    for s in skills:
        reg.register(s)
    return reg


# ---------- serve_async（Plan 4b：流式 + 打断）----------


def _run_async(coro):
    return asyncio.run(coro)


def test_serve_async_streams_events_and_run_done(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
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


def test_serve_async_cross_surface_run_queues_not_preempts(tmp_path):
    # 跨 surface（主窗 pet 在跑 → 面板来新 run）：不抢占，排队等它说完；
    # 面板侧应收到 notice 轻提示，两个 run 都完整完成（先 1 后 2）。
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

    from yibao_brain.skills import Skill
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
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
            skills_factory=lambda: _registry_with(DangerSkill()),
        )
    )
    kinds = [m["event"]["kind"] for m in out if m["type"] == "event"]
    assert "confirmation_needed" in kinds
    assert "error" in kinds
    assert not any(
        m["type"] == "event" and m["event"].get("kind") == "action_result"
        and m["event"]["result"]["data"].get("did") for m in out
    )


def test_serve_async_confirm_remember_skips_future_prompts(tmp_path):
    """勾选「本会话不再询问」并批准：同技能后续调用免确认直接执行（会话级，不落盘）。"""
    import queue as _queue
    import threading as _th
    import time as _time

    from yibao_brain.skills import Skill
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
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
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
        FakeProvider(text="第一次完成"),
        FakeProvider(tool_calls=[ToolCall(id="t2", skill_id="danger", params={})]),
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
            skills_factory=lambda: _registry_with(DangerSkill()),
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

    from yibao_brain.skills import Skill
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True, "n": params.get("n")})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="t1", skill_id="danger", params={"n": 1}),
            ToolCall(id="t2", skill_id="danger", params={"n": 2}),
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
            skills_factory=lambda: _registry_with(DangerSkill()),
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

    from yibao_brain.skills import Skill, SkillRegistry
    from yibao_brain.ipc import ActionResult, RiskLevel

    class DangerSkill(Skill):
        id = "danger"; description = "危险占位"; default_risk = RiskLevel.L3_HIGH
        def run(self, params, ctx): return ActionResult(success=True, data={"did": True})

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
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
            skills_factory=lambda: _registry_with(DangerSkill()),
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
                    skill_id="load_user_activity",
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
    from yibao_brain.skills import SkillRegistry

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
    reg = SkillRegistry()
    _load_plugins_safe(reg, FakeMemory(), FakeProvider(), None)
    assert reg.get("notes.keep").id == "notes.keep"
    assert "[yibao] 插件 notes: ok" in capsys.readouterr().err


def test_load_plugins_safe_never_raises(tmp_path, monkeypatch):
    """插件系统整体异常也不许拖垮底座启动（外层兜底 try）。"""
    from yibao_brain.memory import FakeMemory
    from yibao_brain.server import _load_plugins_safe
    from yibao_brain.skills import SkillRegistry

    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(tmp_path / "nonexistent"))
    _load_plugins_safe(SkillRegistry(), FakeMemory(), FakeProvider(), None)  # 不抛


def test_load_plugins_safe_passes_reminders_store(tmp_path, monkeypatch):
    """回归：_load_plugins_safe 必须把 reminders 透传给 load_plugins——
    漏传时 reminders 插件 ctx.reminders=None，面板直调全报「底座未提供提醒存储」，面板打不开。"""
    from yibao_brain.memory import FakeMemory
    from yibao_brain.reminders import ReminderStore
    from yibao_brain.server import _load_plugins_safe
    from yibao_brain.skills import SkillRegistry

    plugin = tmp_path / "rem"
    (plugin / "tools").mkdir(parents=True)
    (plugin / "manifest.toml").write_text(
        'id = "rem"\ncapabilities = ["reminders"]\n[code]\nentry = "tools"\n',
        encoding="utf-8",
    )
    (plugin / "tools" / "x.py").write_text(
        "from yibao_brain.ipc import ActionResult, RiskLevel\n"
        "from yibao_brain.skills import Skill\n"
        "class X(Skill):\n"
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
    reg = SkillRegistry()
    store = ReminderStore(str(tmp_path / "data" / "reminders.json"))
    _load_plugins_safe(reg, FakeMemory(), FakeProvider(), None, reminders=store)
    sk = reg.get("rem.x")
    assert sk is not None and sk.plugin_ctx.reminders is store


# ---------- ⑦py：panel_action（面板直调方法，过白名单 + 闸门）----------


class _RecSkill:
    """记录执行的删除 tool（plugin 命名空间注册）。"""

    @staticmethod
    def make(executed, ref=None, risk=RiskLevel.L1_LOW):
        from yibao_brain.ipc import ActionResult as AR
        from yibao_brain.skills import Skill as _S

        class Rec(_S):
            id = "tdel.delete"
            description = "删除一条"
            default_risk = risk

            def run(self, params, ctx):
                executed.append(dict(params))
                return AR(success=True, data={"deleted": params.get("id")}, panel=ref)

        return Rec()


def _pa_factory(executed, ref=None, risk=RiskLevel.L1_LOW):
    from yibao_brain.skills import SkillRegistry

    def factory():
        reg = SkillRegistry()
        reg.register(_RecSkill.make(executed, ref, risk), plugin="tdel")
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
            provider=FakeProvider(text="你好"),
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
    from yibao_brain.skills import Skill as _S, SkillRegistry

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
        reg = SkillRegistry()
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
            make_reader([{"id": 1, "type": "run", "text": "hi"}]),
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
    skill_id 用 `<plugin>.x` 形式以匹配 plugin_call_counts 的前缀解析。
    先经 AuditLog 建表（避免 raw sqlite 撞「no such table」）。"""
    from yibao_brain.audit import AuditLog

    log = AuditLog(str(db_path))
    try:
        for pid, n in counts.items():
            for _ in range(n):
                log.conn.execute(
                    "INSERT INTO actions (skill_id, success) VALUES (?, 1)",
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
