"""module 面板(R4 插件运行时):manifest type="module" 注册 + 引用式 payload。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from yibao_brain import plugins


@pytest.fixture(autouse=True)
def _clean_registry():
    """_PANELS 等是进程级注册表,逐个测试后清掉本文件注册的 ref,防串扰。"""
    yield
    for ref in ("demo:main", "demo2:w", "demo2:s"):
        plugins._PANELS.pop(ref, None)
        plugins._PANEL_TITLES.pop(ref, None)
    plugins._PLUGIN_DIRS.pop("demo", None)
    plugins._PLUGIN_DIRS.pop("demo2", None)


def _make_plugin(tmp_path: Path, name: str = "demo", *, with_dist: bool = True) -> Path:
    child = tmp_path / name
    (child / "panel" / "dist").mkdir(parents=True)
    if with_dist:
        (child / "panel" / "dist" / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    return child


def _manifest() -> dict:
    return {
        "name": "演示",
        "panel": [{"type": "module", "name": "main", "label": "演示面板", "src": "panel/dist/index.html"}],
    }


def test_module_panel_registered_without_reading_file(tmp_path):
    child = _make_plugin(tmp_path)
    plugins._load_panels(child, "demo", _manifest(), None)  # registry 仅 widget 分支用,module 传 None
    panel = plugins.get_panel("demo:main")
    assert panel["type"] == "module"
    assert panel["entry"] == "panel/dist/index.html"
    assert "html" not in panel          # 不读全文进内存
    assert isinstance(panel["surfaces"], list) and panel["surfaces"]  # 表面声明逻辑与既有面板一致


def test_module_payload_is_reference_with_mtime(tmp_path):
    child = _make_plugin(tmp_path)
    plugins._load_panels(child, "demo", _manifest(), None)
    payload = plugins.panel_payload(SimpleNamespace(panel="demo:main", data={"x": 1}))
    assert payload["schema"] is None
    assert payload["webview"]["url"] == "yibao-plugin://demo/panel/dist/index.html"
    assert payload["webview"]["v"] > 0  # mtime 作版本号
    assert "html" not in payload["webview"]
    assert payload["data"] == {"x": 1}


def test_module_payload_missing_dist_still_registered_v0(tmp_path):
    # dist 未构建时也登记(先声明后构建的 dev 流程),payload v=0 由前端照样加载(404 由协议层兜底)
    child = _make_plugin(tmp_path, with_dist=False)
    plugins._load_panels(child, "demo", _manifest(), None)
    payload = plugins.panel_payload(SimpleNamespace(panel="demo:main", data={}))
    assert payload["webview"]["url"] == "yibao-plugin://demo/panel/dist/index.html"
    assert payload["webview"]["v"] == 0


def test_legacy_webview_and_schema_panels_unchanged(tmp_path):
    # 回归:旧类型仍读全文 / 解析 JSON
    child = _make_plugin(tmp_path, name="demo2")
    (child / "panel" / "a.html").write_text("<html>old</html>", encoding="utf-8")
    (child / "panel" / "b.schema.json").write_text('{"type": "list", "bind": {"items": "$data.rows"}}', encoding="utf-8")
    manifest = {"name": "旧", "panel": [
        {"type": "webview", "name": "w", "src": "panel/a.html"},
        {"type": "schema", "name": "s", "src": "panel/b.schema.json"},
    ]}
    plugins._load_panels(child, "demo2", manifest, None)
    assert plugins.get_panel("demo2:w") == {"type": "webview", "html": "<html>old</html>",
                                           "surfaces": plugins.get_panel("demo2:w")["surfaces"]}
    assert plugins.get_panel("demo2:s")["type"] == "list"
