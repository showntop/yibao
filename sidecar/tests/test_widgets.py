"""主屏 widget（OS 感 §4.2）：manifest [[panel]] type="widget" 加载校验 + serve_async widgets 查询。"""
import asyncio
import json
import tomllib

import pytest

from yibao_brain import plugins
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.llm import FakeProvider
from yibao_brain.server import serve_async
from yibao_brain.tools import Tool, ToolRegistry


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def _run_async(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _restore_registries():
    """_load_panels 直接改模块级注册表：快照恢复，别污染其他测试。"""
    saved = (
        dict(plugins._WIDGETS),
        dict(plugins._PANELS),
        dict(plugins._PANEL_TITLES),
    )
    yield
    plugins._WIDGETS.clear(); plugins._WIDGETS.update(saved[0])
    plugins._PANELS.clear(); plugins._PANELS.update(saved[1])
    plugins._PANEL_TITLES.clear(); plugins._PANEL_TITLES.update(saved[2])


class _L0Tool(Tool):
    id = "tdel.list"
    description = "列出"
    default_risk = RiskLevel.L0_READONLY

    def run(self, params, ctx):
        return ActionResult(success=True, data={"rows": [{"text": "一"}]})


class _L1Tool(Tool):
    id = "tdel.write"
    description = "写入"
    default_risk = RiskLevel.L1_LOW

    def run(self, params, ctx):
        return ActionResult(success=True, data={})


class _FailTool(Tool):
    id = "tdel.broken"
    description = "必失败"
    default_risk = RiskLevel.L0_READONLY

    def run(self, params, ctx):
        return ActionResult(success=False, error="炸了")


def _registry_with(*skills):
    reg = ToolRegistry()
    for s in skills:
        reg.register(s, plugin="tdel")
    return reg


def _mk_plugin(tmp_path, widget_toml):
    """最小插件目录：widget schema + manifest（[[panel]] 片段由用例给）。"""
    child = tmp_path / "tdel"
    (child / "panel").mkdir(parents=True)
    (child / "panel" / "w.schema.json").write_text(
        json.dumps({"version": 1, "type": "list", "bind": {"items": "$data.rows"},
                    "item": {"title": "$item.text"}}),
        encoding="utf-8",
    )
    (child / "manifest.toml").write_text('id = "tdel"\nname = "测试"\n' + widget_toml, encoding="utf-8")
    return child


def _load(child, registry):
    manifest = tomllib.loads((child / "manifest.toml").read_text(encoding="utf-8"))
    plugins._load_panels(child, "tdel", manifest, registry)


_WIDGET_OK = (
    '[[panel]]\ntype = "widget"\nname = "widget"\nlabel = "最近"\n'
    'src = "panel/w.schema.json"\nmethod = "list"\nopen = "list"\n'
)


# ---------- 加载校验 ----------


def test_widget_registered(tmp_path):
    child = _mk_plugin(tmp_path, _WIDGET_OK)
    _load(child, _registry_with(_L0Tool()))
    decl = plugins.get_widgets().get("tdel:widget")
    assert decl == {"method": "tdel.list", "open": "tdel.list", "title": "测试 · 最近"}
    # widget 的 schema 也进面板注册表（panel_payload 复用 schema 分支）
    assert plugins.get_panel("tdel:widget")["type"] == "list"


def test_widget_full_method_id_accepted(tmp_path):
    toml = _WIDGET_OK.replace('method = "list"', 'method = "tdel.list"')
    child = _mk_plugin(tmp_path, toml)
    _load(child, _registry_with(_L0Tool()))
    assert plugins.get_widgets()["tdel:widget"]["method"] == "tdel.list"


def test_widget_without_open_is_none(tmp_path):
    toml = _WIDGET_OK.replace('open = "list"\n', "")
    child = _mk_plugin(tmp_path, toml)
    _load(child, _registry_with(_L0Tool()))
    assert plugins.get_widgets()["tdel:widget"]["open"] is None


def test_widget_requires_method(tmp_path):
    toml = _WIDGET_OK.replace('method = "list"\n', "")
    child = _mk_plugin(tmp_path, toml)
    _load(child, _registry_with(_L0Tool()))
    assert "tdel:widget" not in plugins.get_widgets()
    assert plugins.get_panel("tdel:widget") is None  # 无效 widget 不留面板残骸


def test_widget_method_must_be_registered(tmp_path):
    toml = _WIDGET_OK.replace('method = "list"', 'method = "ghost"')
    child = _mk_plugin(tmp_path, toml)
    _load(child, _registry_with(_L0Tool()))
    assert "tdel:widget" not in plugins.get_widgets()


def test_widget_method_must_be_l0(tmp_path):
    toml = _WIDGET_OK.replace('method = "list"', 'method = "write"')
    child = _mk_plugin(tmp_path, toml)
    _load(child, _registry_with(_L0Tool(), _L1Tool()))
    assert "tdel:widget" not in plugins.get_widgets()


def test_widget_bad_schema_skipped(tmp_path):
    child = _mk_plugin(tmp_path, _WIDGET_OK)
    (child / "panel" / "w.schema.json").write_text("{bad json", encoding="utf-8")
    _load(child, _registry_with(_L0Tool()))
    assert "tdel:widget" not in plugins.get_widgets()


# ---------- serve_async 集成：{"type":"widgets"} → payload 列表 ----------


def _serve_widgets(tmp_path, monkeypatch, widgets, skills):
    for ref, decl in widgets.items():
        monkeypatch.setitem(plugins._WIDGETS, ref, decl)
        monkeypatch.setitem(plugins._PANELS, ref, {"version": 1, "type": "list"})
        monkeypatch.setitem(plugins._PANEL_TITLES, ref, decl["title"])
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "widgets"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=lambda: _registry_with(*skills),
        )
    )
    return [m for m in out if m["type"] == "widgets"]


def test_serve_async_widgets_query(tmp_path, monkeypatch):
    msgs = _serve_widgets(
        tmp_path, monkeypatch,
        {"tdel:widget": {"method": "tdel.list", "open": "tdel.list", "title": "测试 · 最近"}},
        [_L0Tool()],
    )
    assert len(msgs) == 1
    payloads = msgs[0]["widgets"]
    assert len(payloads) == 1
    p = payloads[0]
    assert p["panel"] == "tdel:widget" and p["title"] == "测试 · 最近"
    assert p["schema"] == {"version": 1, "type": "list"}
    assert p["data"] == {"rows": [{"text": "一"}]}
    assert p["open"] == "tdel.list"


def test_serve_async_widgets_failure_skipped(tmp_path, monkeypatch):
    msgs = _serve_widgets(
        tmp_path, monkeypatch,
        {"tdel:widget": {"method": "tdel.broken", "open": None, "title": "测试 · 坏"}},
        [_FailTool()],
    )
    assert len(msgs) == 1
    assert msgs[0]["widgets"] == []  # 单个失败只跳过，响应照常


def test_serve_async_widgets_empty(tmp_path, monkeypatch):
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "widgets"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    msgs = [m for m in out if m["type"] == "widgets"]
    assert len(msgs) == 1 and msgs[0]["widgets"] == []
