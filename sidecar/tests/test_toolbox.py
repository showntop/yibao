"""工具箱插件：JSON 格式化 / 文本对比。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def env(data_dir):
    from yibao_brain.llm import FakeProvider
    from yibao_brain.memory import FakeMemory
    from yibao_brain.plugins import LlmChat, load_plugins
    from yibao_brain.skills import SkillRegistry

    reg = SkillRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg, results


def _run(reg, sid, params):
    t = reg.get(sid)
    assert t is not None, f"技能未注册: {sid}"
    return t.run(params, t.plugin_ctx)


def test_plugin_loads(env):
    _, results = env
    assert results.get("toolbox") == "ok"


def test_api_methods_registered_with_full_prefix(env):
    """面板桥以完整名（toolbox.xxx）调用，api.toml 必须能查到。"""
    from yibao_brain.plugins import get_api

    env[0]  # 触发加载
    for name in ("toolbox.list", "toolbox.json_format", "toolbox.text_diff"):
        api = get_api(name)
        assert api is not None and api.direct, name
    assert get_api("toolbox.list").panel == "toolbox:main"


def test_open_defaults_to_json_tab(env):
    reg, _ = env
    r = _run(reg, "toolbox.open", {})
    assert r.success and r.panel == "toolbox:main"
    assert r.data["tool"] == "json"


def test_json_format_pretty(env):
    reg, _ = env
    r = _run(reg, "toolbox.json_format", {"text": '{"a":1,"b":[2,3]}'})
    assert r.success and r.panel == "toolbox:main"
    assert r.data["output"] == '{\n  "a": 1,\n  "b": [\n    2,\n    3\n  ]\n}'
    assert r.data["input"] == '{"a":1,"b":[2,3]}'


def test_json_format_minify_and_unicode(env):
    reg, _ = env
    r = _run(reg, "toolbox.json_format", {"text": '{ "a" : "译宝" }', "mode": "minify"})
    assert r.success
    assert r.data["output"] == '{"a":"译宝"}'


def test_json_format_invalid_reports_position(env):
    reg, _ = env
    r = _run(reg, "toolbox.json_format", {"text": '{"a": }'})
    assert not r.success
    assert "JSON 不合法" in r.error and "行" in r.error and "列" in r.error


def test_json_format_empty_and_unknown_mode(env):
    reg, _ = env
    assert not _run(reg, "toolbox.json_format", {"text": "  "}).success
    assert not _run(reg, "toolbox.json_format", {"text": "{}", "mode": "wat"}).success


def test_json_format_indent_clamped(env):
    reg, _ = env
    r = _run(reg, "toolbox.json_format", {"text": '{"a":1}', "indent": 99})
    assert r.success and r.data["output"] == '{\n        "a": 1\n}'


def test_diff_stats(env):
    reg, _ = env
    r = _run(reg, "toolbox.text_diff", {"old": "a\nb\nc", "new": "a\nx\nc\nd"})
    assert r.success and r.panel == "toolbox:main"
    assert (r.data["added"], r.data["removed"]) == (2, 1)
    assert not r.data["identical"]
    tags = [l["t"] for l in r.data["lines"]]
    assert tags.count("add") == 2 and tags.count("del") == 1


def test_diff_identical(env):
    reg, _ = env
    r = _run(reg, "toolbox.text_diff", {"old": "same\ntext", "new": "same\ntext"})
    assert r.success and r.data["identical"]
    assert all(l["t"] == "same" for l in r.data["lines"])


def test_diff_empty_sides(env):
    reg, _ = env
    assert not _run(reg, "toolbox.text_diff", {"old": "", "new": ""}).success
    r = _run(reg, "toolbox.text_diff", {"old": "", "new": "x"})
    assert r.success and r.data["added"] == 1 and r.data["removed"] == 0
    r = _run(reg, "toolbox.text_diff", {"old": "a\nb", "new": ""})
    assert r.success and r.data["removed"] == 2 and r.data["added"] == 0


def test_diff_large_input_rejected(env):
    reg, _ = env
    big = "x" * (600 * 1024)
    assert not _run(reg, "toolbox.text_diff", {"old": big, "new": ""}).success
