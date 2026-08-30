"""表面层 tool（design §3）：裁决器不变量在 tool 侧把守。"""

from yibao_brain.surface import SurfaceBridge
from yibao_brain.surface_tools import make_surface_tools


def make():
    bridge = SurfaceBridge()
    tools = {t.id: t for t in make_surface_tools(bridge)}
    return bridge, tools


def test_surface_open_rejects_stage_and_focus():
    _, tools = make()
    for pres in ("stage", "focus", "window"):
        r = tools["surface.open"].run({"panel": "zimeiti:editor", "presentation": pres}, None)
        assert r.success is False, pres
        assert "亲手" in r.error


def test_surface_open_allows_inline_and_peek_via_panel_channel():
    _, tools = make()
    r = tools["surface.open"].run({"panel": "zimeiti:editor", "presentation": "peek"}, None)
    assert r.success and r.panel == "zimeiti:editor" and r.presentation == "peek"
    r2 = tools["surface.open"].run({}, None)
    assert r2.success and r2.panel == "zimeiti:editor"


def test_editor_write_dispatches_and_leaves_decision_to_instrument():
    bridge, tools = make()
    sent = []
    bridge.bind(sent.append)
    r = tools["editor.replace_range"].run({"start": 0, "end": 2, "text": "新文本", "quote": "旧"}, None)
    assert r.success and r.data["dispatched"] is True
    assert sent[-1]["command"] == "editor.replace_range"
    assert sent[-1]["panel"] == "zimeiti:editor"
    assert sent[-1]["params"]["text"] == "新文本"


def test_surface_read_returns_snapshots():
    bridge, tools = make()
    bridge.record("zimeiti", "zimeiti.doc_snapshot", {"id": "1", "content": "全文"})
    bridge.record("zimeiti", "zimeiti.selection_changed", {"start": 0, "end": 2, "quote": "全文"})
    r = tools["surface.read"].run({}, None)
    assert r.success
    assert r.data["doc"]["content"] == "全文"
    assert r.data["selection"]["quote"] == "全文"


def test_surface_read_fails_honest_when_nothing_reported():
    _, tools = make()
    r = tools["surface.read"].run({}, None)
    assert r.success is False and "没打开" in r.error


def test_surface_tools_always_visible_registration():
    from yibao_brain.tools.core import ToolRegistry

    bridge, tools = make()
    reg = ToolRegistry()
    for t in tools.values():
        reg.register(t, plugin=t.id.split(".", 1)[0], always_visible=True)
    # active_plugins 为空集时，插件 tool 被折叠，表面 tool 仍可见（LLM 见安全名：点→下划线）
    schema_names = [s.get("name") for s in reg.openai_tools(set())]
    assert "surface_open" in schema_names and "editor_replace_range" in schema_names
