import asyncio
import json
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
    assert executed == []                                    # 未执行
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_confirm_flow_rejected(tmp_path, monkeypatch):
    # api.risk="L2" 收紧（tool 默认 L1）→ 触发确认流；壳拒绝 → error，不执行
    executed = []
    _patch_api(monkeypatch, risk="L2")
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
                {"type": "confirm", "approved": False},
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
    assert out[-1] == {"type": "run_done", "id": 1}


def test_panel_action_confirm_flow_approved_executes(tmp_path, monkeypatch):
    executed = []
    _patch_api(monkeypatch, risk="L2")
    out = []
    _run_async(
        serve_async(
            make_reader([
                {"id": 1, "type": "panel_action", "method": "tdel.delete", "params": {"id": "r1"}},
                {"type": "confirm", "approved": True},
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
