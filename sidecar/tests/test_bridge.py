"""浏览器扩展桥：token 确保 + 路由函数级测试（不起 socket；同步 + asyncio.run，仓内无 pytest-asyncio）。"""
import asyncio

import pytest

import yibao_brain.plugins as plugins
from yibao_brain.ipc import Action, ActionResult
from yibao_brain.llm import ToolCall
from yibao_brain.plugins import ApiMethod
from yibao_brain.safety import Decision
from yibao_brain.server import _ensure_bridge_token, _make_bridge_route


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _bridge_api_whitelist(monkeypatch):
    """函数级测试不跑 load_plugins：桥依赖的两个 api 方法直接种进白名单（仿 test_server._patch_api）。
    条目与真 api.toml 一致：invoke_mat_save 已存在；invoke_add_topic 由 Task 4 真注册。"""
    for name, handler in (("zimeiti.invoke_mat_save", "zimeiti.mat_save"),
                          ("zimeiti.invoke_add_topic", "zimeiti.add")):
        monkeypatch.setitem(plugins._API, name, ApiMethod(
            name=name, handler=handler, direct=True, intent=None, risk=None,
            plugin_id="zimeiti", quiet=True))


def test_ensure_bridge_token_generates_and_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: saved.update(v))
    settings = {"http.token": ""}
    tok = _ensure_bridge_token(settings)
    assert len(tok) == 32 and settings["http.token"] == tok
    assert saved == {"http.token": tok}


def test_ensure_bridge_token_keeps_existing(monkeypatch):
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: (_ for _ in ()).throw(AssertionError("不应再存")))
    settings = {"http.token": "abc123"}
    assert _ensure_bridge_token(settings) == "abc123"


class _FakeInvoker:
    def __init__(self, decision=Decision.AUTO, result=None):
        self.decision = decision
        self.result = result or ActionResult(success=True, data={"title": "存好了"})
        self.calls = []

    def propose(self, call):
        self.calls.append(("propose", call.skill_id, dict(call.params)))
        return Action(id=call.id, skill_id=call.skill_id)

    def decide(self, action):
        self.calls.append(("decide", action.skill_id))
        return self.decision

    def execute(self, action, params):
        self.calls.append(("execute", action.skill_id, dict(params)))
        return self.result


class _FakeAgent:
    def __init__(self, invoker):
        self.invoker = invoker


def _route(invoker, events):
    agent = _FakeAgent(invoker)
    return _make_bridge_route(agent, lambda m: None, "tok", emit=lambda e: events.append(e))


def test_route_wrong_token_401():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, obj = await route("POST", "/save", {"x-yibao-token": "bad"}, {"text": "x"})
        assert status == 401 and obj["ok"] is False

    _run(main())


def test_route_health():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, obj = await route("GET", "/health", {"x-yibao-token": "tok"}, {})
        assert status == 200 and obj["ok"] is True

    _run(main())


def test_route_material_executes_mat_save_and_emits_pa_http():
    async def main():
        events = []
        invoker = _FakeInvoker()
        route = _route(invoker, events)
        status, obj = await route("POST", "/save", {"x-yibao-token": "tok"},
                                  {"url": "https://a.com/x", "title": "标题", "text": "正文", "mode": "material"})
        assert status == 200 and obj == {"ok": True, "title": "存好了"}
        exe = [c for c in invoker.calls if c[0] == "execute"]
        assert exe and exe[0][1] == "zimeiti.mat_save"
        assert exe[0][2]["url"] == "https://a.com/x"
        assert "正文" in exe[0][2]["text"] and "标题" in exe[0][2]["text"]
        ev = events[0]
        assert ev["kind"] == "action_result"
        assert ev["action"]["id"].startswith("pa_http_")
        assert ev["action"]["skill_id"] == "zimeiti.mat_save"

    _run(main())


def test_route_topic_executes_add():
    async def main():
        invoker = _FakeInvoker()
        route = _route(invoker, [])
        status, obj = await route("POST", "/save", {"x-yibao-token": "tok"},
                                  {"url": "https://a.com/y", "title": "选题标题", "text": "正文", "mode": "topic"})
        assert status == 200
        exe = [c for c in invoker.calls if c[0] == "execute"]
        assert exe[0][1] == "zimeiti.add"
        assert exe[0][2]["title"] == "选题标题"
        assert exe[0][2]["source"] == "https://a.com/y"

    _run(main())


def test_route_empty_text_400_and_bad_mode_400_and_confirm_403():
    async def main():
        route = _route(_FakeInvoker(), [])
        status, _ = await route("POST", "/save", {"x-yibao-token": "tok"}, {"text": ""})
        assert status == 400
        status, _ = await route("POST", "/save", {"x-yibao-token": "tok"}, {"text": "x", "mode": "ghost"})
        assert status == 400
        route2 = _route(_FakeInvoker(decision=Decision.CONFIRM), [])
        status, obj = await route2("POST", "/save", {"x-yibao-token": "tok"}, {"text": "x", "mode": "material"})
        assert status == 403 and obj["ok"] is False

    _run(main())


def test_zimeiti_api_toml_has_quiet_bridge_entries():
    """真 api.toml：invoke_mat_save / invoke_add_topic 都是 direct+quiet（桥回执不发 panel 事件）。"""
    from pathlib import Path

    from yibao_brain import plugins
    from yibao_brain.skills import Skill, SkillRegistry

    class _Dummy(Skill):
        description = "dummy"

        def run(self, params, ctx):
            raise NotImplementedError

    reg = SkillRegistry()
    for sid in ("zimeiti.mat_save", "zimeiti.add"):
        d = _Dummy()
        d.id = sid
        reg.register(d, plugin="zimeiti")
    # fixture 已种入桥条目（Task 3 路由测试用）；先摘掉，确保断言命中真 api.toml 加载结果
    plugins._API.pop("zimeiti.invoke_mat_save", None)
    plugins._API.pop("zimeiti.invoke_add_topic", None)
    api_path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "api.toml"
    plugins._load_api("zimeiti", api_path, reg)
    try:
        for name in ("zimeiti.invoke_mat_save", "zimeiti.invoke_add_topic"):
            m = plugins.get_api(name)
            assert m is not None, name
            assert m.direct is True and m.quiet is True, name
    finally:
        plugins._API.pop("zimeiti.invoke_mat_save", None)
        plugins._API.pop("zimeiti.invoke_add_topic", None)


def test_route_material_defers_and_schedules_enrich():
    """material：mat_save 带 defer/title 秒回 → 响应后后台调度 mat_enrich 补元数据（失败静默）。"""

    async def main():
        invoker = _FakeInvoker(result=ActionResult(success=True, data={"id": "m1", "title": "页面标题", "pending": True}))
        route = _route(invoker, [])
        status, obj = await route(
            "POST", "/save", {"x-yibao-token": "tok"},
            {"url": "https://a.com/x", "title": "页面标题", "text": "正文", "mode": "material"},
        )
        assert status == 200 and obj["title"] == "页面标题"
        save_call = [c for c in invoker.calls if c[0] == "execute"][0]
        assert save_call[1] == "zimeiti.mat_save"
        assert save_call[2]["defer"] is True and save_call[2]["title"] == "页面标题"
        await asyncio.sleep(0.05)  # 让后台 enrich 任务跑一轮
        enrich = [c for c in invoker.calls if c[0] == "execute" and c[1] == "zimeiti.mat_enrich"]
        assert enrich and enrich[0][2] == {"id": "m1"}

    _run(main())
