"""插件测试套件（v2 方案 §10）：fake ctx 一键构造，插件 TDD 与 agent 自测的地基。"""
from yibao_brain.testing import FakeHttp, FakeLlm, PanelRecorder, make_ctx


def test_make_ctx_full_capabilities(tmp_path):
    ctx = make_ctx(tmp_path)
    assert ctx.db is not None and ctx.memory is not None
    assert ctx.http is not None and ctx.llm is not None
    assert ctx.emit_panel is not None


def test_make_ctx_respects_capability_subset(tmp_path):
    ctx = make_ctx(tmp_path, capabilities=frozenset({"db"}))
    assert ctx.db is not None
    assert ctx.memory is None and ctx.http is None and ctx.llm is None


def test_ctx_db_is_real_sqlite_on_tmp(tmp_path):
    ctx = make_ctx(tmp_path, plugin_id="notes")
    ctx.db.apply_schema([{
        "name": "notes",
        "columns": [{"name": "id", "type": "text", "pk": True}, {"name": "text", "type": "text"}],
    }])
    ctx.db.insert("notes", {"text": "hello"})
    rows = ctx.db.query("notes")
    assert rows[0]["text"] == "hello"
    # 落在 tmp 目录，不碰真实数据目录
    assert str(tmp_path) in ctx.db.path


def test_fake_llm_records_and_answers():
    llm = FakeLlm("回答")
    assert llm.chat("问题1") == "回答"
    assert llm.calls == ["问题1"]


def test_fake_http_canned_response():
    http = FakeHttp({"http://x/api": {"ok": 1}})
    assert http.get("http://x/api") == {"ok": 1}
    assert http.calls == [("GET", "http://x/api")]
    # 未预置的 url 返回空 dict 而不是炸
    assert http.post("http://y", {"a": 1}) == {}


def test_panel_recorder_captures(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.emit_panel({"panel": "notes:list", "data": {"notes": []}})
    assert isinstance(ctx.emit_panel, PanelRecorder)
    assert ctx.emit_panel.events[0]["panel"] == "notes:list"
