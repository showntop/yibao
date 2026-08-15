"""/save 扩展桥：token 确保 + aiohttp TestClient 路由级测试（同步 + asyncio.run，仓内无 pytest-asyncio）。"""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

import yibao_brain.plugins as plugins
from yibao_brain.http_api import EventTap, MobileDeps, build_app
from yibao_brain.ipc import Action, ActionResult
from yibao_brain.llm import ToolCall
from yibao_brain.plugins import ApiMethod
from yibao_brain.safety import Decision
from yibao_brain.server import _bridge_save, _ensure_http_token


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


def test_ensure_http_token_generates_and_persists(monkeypatch):
    saved = {}
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: saved.update(v))
    settings = {"http.mobile_token": ""}
    tok = _ensure_http_token(settings, "http.mobile_token")
    assert len(tok) == 32 and settings["http.mobile_token"] == tok
    assert saved == {"http.mobile_token": tok}


def test_ensure_http_token_keeps_existing(monkeypatch):
    monkeypatch.setattr("yibao_brain.server.save_settings", lambda v: (_ for _ in ()).throw(AssertionError("不应再存")))
    settings = {"http.token": "abc123"}
    assert _ensure_http_token(settings, "http.token") == "abc123"


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


def _mkdeps(invoker, events):
    from yibao_brain.server import _bridge_save

    agent = _FakeAgent(invoker)

    def emit(action, result):
        events.append({"kind": "action_result", "action": {"id": action.id, "skill_id": action.skill_id}})

    async def save(body):
        return await _bridge_save(agent, emit, body)

    return MobileDeps(save=save)


def _mkclient(invoker, events):
    app = build_app(get_bridge_token=lambda: "btok", get_mobile_token=lambda: "mtok",
                    tap=EventTap(lambda m: None), deps=_mkdeps(invoker, events))
    return TestClient(TestServer(app))


def test_save_material_executes_mat_save_and_emits_pa_http():
    async def main():
        events = []
        invoker = _FakeInvoker()
        client = _mkclient(invoker, events)
        await client.start_server()
        try:
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"},
                                  json={"url": "https://a.com/x", "title": "标题", "text": "正文", "mode": "material"})
            assert r.status == 200 and await r.json() == {"ok": True, "title": "存好了"}
            exe = [c for c in invoker.calls if c[0] == "execute"]
            assert exe and exe[0][1] == "zimeiti.mat_save"
            assert exe[0][2]["url"] == "https://a.com/x"
            assert "正文" in exe[0][2]["text"] and "标题" in exe[0][2]["text"]
            ev = events[0]
            assert ev["kind"] == "action_result"
            assert ev["action"]["id"].startswith("pa_http_")
            assert ev["action"]["skill_id"] == "zimeiti.mat_save"
        finally:
            await client.close()

    _run(main())


def test_save_topic_executes_add():
    async def main():
        invoker = _FakeInvoker()
        client = _mkclient(invoker, [])
        await client.start_server()
        try:
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"},
                                  json={"url": "https://a.com/y", "title": "选题标题", "text": "正文", "mode": "topic"})
            assert r.status == 200
            exe = [c for c in invoker.calls if c[0] == "execute"]
            assert exe[0][1] == "zimeiti.add"
            assert exe[0][2]["title"] == "选题标题"
            assert exe[0][2]["source"] == "https://a.com/y"
        finally:
            await client.close()

    _run(main())


def test_save_empty_text_400_and_bad_mode_400_and_confirm_403():
    async def main():
        client = _mkclient(_FakeInvoker(), [])
        await client.start_server()
        try:
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"}, json={"text": ""})
            assert r.status == 400
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"}, json={"text": "x", "mode": "ghost"})
            assert r.status == 400
        finally:
            await client.close()
        client2 = _mkclient(_FakeInvoker(decision=Decision.CONFIRM), [])
        await client2.start_server()
        try:
            r = await client2.post("/save", headers={"X-Yibao-Token": "btok"},
                                   json={"text": "x", "mode": "material"})
            assert r.status == 403 and (await r.json())["ok"] is False
        finally:
            await client2.close()

    _run(main())


def test_zimeiti_api_toml_has_quiet_bridge_entries():
    """真 api.toml：invoke_mat_save / invoke_add_topic / mat_peek 都是 direct+quiet（桥回执/抽屉取数不发 panel 事件）。"""
    from pathlib import Path

    from yibao_brain import plugins
    from yibao_brain.skills import Skill, SkillRegistry

    class _Dummy(Skill):
        description = "dummy"

        def run(self, params, ctx):
            raise NotImplementedError

    reg = SkillRegistry()
    for sid in ("zimeiti.mat_save", "zimeiti.add", "zimeiti.mat_list"):
        d = _Dummy()
        d.id = sid
        reg.register(d, plugin="zimeiti")
    # fixture 已种入桥条目（Task 3 路由测试用）；先摘掉，确保断言命中真 api.toml 加载结果
    plugins._API.pop("zimeiti.invoke_mat_save", None)
    plugins._API.pop("zimeiti.invoke_add_topic", None)
    plugins._API.pop("zimeiti.mat_peek", None)
    api_path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "api.toml"
    plugins._load_api("zimeiti", api_path, reg)
    try:
        for name in ("zimeiti.invoke_mat_save", "zimeiti.invoke_add_topic", "zimeiti.mat_peek"):
            m = plugins.get_api(name)
            assert m is not None, name
            assert m.direct is True and m.quiet is True, name
    finally:
        plugins._API.pop("zimeiti.invoke_mat_save", None)
        plugins._API.pop("zimeiti.invoke_add_topic", None)
        plugins._API.pop("zimeiti.mat_peek", None)


def test_save_material_defers_and_schedules_enrich():
    """material：mat_save 带 defer/title 秒回 → 响应后后台调度 mat_enrich 补元数据（失败静默）。"""

    async def main():
        invoker = _FakeInvoker(result=ActionResult(success=True, data={"id": "m1", "title": "页面标题", "pending": True}))
        client = _mkclient(invoker, [])
        await client.start_server()
        try:
            r = await client.post("/save", headers={"X-Yibao-Token": "btok"},
                                  json={"url": "https://a.com/x", "title": "页面标题", "text": "正文", "mode": "material"})
            assert r.status == 200 and (await r.json())["title"] == "页面标题"
            save_call = [c for c in invoker.calls if c[0] == "execute"][0]
            assert save_call[1] == "zimeiti.mat_save"
            assert save_call[2]["defer"] is True and save_call[2]["title"] == "页面标题"
            await asyncio.sleep(0.05)  # 让后台 enrich 任务跑一轮
            enrich = [c for c in invoker.calls if c[0] == "execute" and c[1] == "zimeiti.mat_enrich"]
            assert enrich and enrich[0][2] == {"id": "m1"}
        finally:
            await client.close()

    _run(main())
