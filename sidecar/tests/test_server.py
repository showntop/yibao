import asyncio
import json
from yibao_brain.server import serve, serve_async, build_loop
from yibao_brain.llm import FakeProvider, ToolCall


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
    out = []
    _run_async(
        serve_async(
            make_reader(inbox),
            lambda m: out.append(m),
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


# ---------- 协议扩展：hello / ping / permissions ----------


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
    assert set(out[0]["permissions"]) >= {"ax", "screen"}


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
    assert set(perms[0]["permissions"]) >= {"ax", "screen"}


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
