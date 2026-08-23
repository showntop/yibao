"""能力热重载（P1 reload 地基）：ToolRegistry 注销/替换 + load_plugins 增量 + capability_refresh。

覆盖 spec §F：增量注册（同 id 替换）、注销、load_plugins existing 跳过、capability_refresh 热加载新插件。
"""
from pathlib import Path

import pytest

from yibao_brain.ipc import ActionResult
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import get_plugin_summaries, load_plugins
from yibao_brain.tools import CapabilityRefreshTool, EchoTool, Tool, ToolListTool, ToolRegistry

PLUGIN_A = """
id = "reload_a"
name = "重载测试 A"
capabilities = ["llm"]

[[tool]]
id = "hi"
type = "prompt"
description = "A 打招呼"
risk = "L1"
[prompt.template]
text = "hi from A"
"""

PLUGIN_B = """
id = "reload_b"
name = "重载测试 B"
capabilities = ["llm"]

[[tool]]
id = "hello"
type = "prompt"
description = "B 打招呼"
risk = "L1"
[prompt.template]
text = "hi from B"
"""


def _write_plugin(root: Path, name: str, manifest: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.toml").write_text(manifest, encoding="utf-8")
    return d


def _demo_tool() -> Tool:
    class DemoTool(Tool):
        id = "demo.x"
        label = "demo"

        def run(self, params, ctx):
            return ActionResult(success=True, data={})

    return DemoTool()


def test_registry_replace_same_id():
    reg = ToolRegistry()
    a, b = _demo_tool(), _demo_tool()
    reg.register(a, plugin="demo")
    with pytest.raises(ValueError):  # 不带 replace 禁止覆盖（重复注册仍是哨兵）
        reg.register(b, plugin="demo")
    reg.register(b, plugin="demo", replace=True)  # 热重载场景同 id 覆盖
    assert reg.get("demo.x") is b


def test_registry_unregister_updates_plugin_ids():
    reg = ToolRegistry()
    reg.register(_demo_tool(), plugin="demo")
    assert reg.plugin_ids() == {"demo"}
    reg.unregister("demo.x")
    assert reg.plugin_ids() == set()
    assert "demo.x" not in [t.id for t in reg.list()]
    reg.unregister("demo.x")  # 幂等：不存在静默


def test_load_plugins_existing_skips_known(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "reload_a", PLUGIN_A)
    reg = ToolRegistry()
    res1 = load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)
    assert res1["reload_a"] == "ok"
    assert "reload_a.hi" in [t.id for t in reg.list()]
    # 增量重载：existing 传已注册集合 → 跳过 reload_a，只加载新目录
    _write_plugin(root, "reload_b", PLUGIN_B)
    res2 = load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None, existing=reg.plugin_ids())
    assert res2.get("reload_b") == "ok"
    assert "reload_a" not in res2  # 已注册的没被重跑
    assert "reload_b.hello" in [t.id for t in reg.list()]
    # 插件 id 集合更新后，再次增量不再加载
    res3 = load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None, existing=reg.plugin_ids())
    assert res3 == {}


def test_capability_refresh_hot_loads_new_plugin(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "reload_a", PLUGIN_A)
    reg = ToolRegistry()
    load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)

    def loader(existing=None):
        return load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None, existing=existing)

    tool = CapabilityRefreshTool(reg, loader)
    # 无新插件
    res = tool.run({}, None)
    assert res.success
    assert res.data["added"] == []
    # 放入新插件 → 热加载生效（无需重启）
    _write_plugin(root, "reload_b", PLUGIN_B)
    res2 = tool.run({}, None)
    assert res2.success
    assert res2.data["added"] == ["reload_b"]
    assert "reload_b.hello" in [t.id for t in reg.list()]
    # 再扫一次无新增
    res3 = tool.run({}, None)
    assert res3.data["added"] == []


PLUGIN_PRIVILEGED = """
id = "priv_plug"
name = "特权插件"
description = "与底座深度集成，不可装卸"
privileged = true
capabilities = ["llm"]

[[tool]]
id = "x"
type = "prompt"
description = "特权工具"
risk = "L1"
[prompt.template]
text = "hi"
"""


def test_tool_list_ledger_categories_privileged_and_expanded(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "reload_a", PLUGIN_A)
    _write_plugin(root, "priv_plug", PLUGIN_PRIVILEGED)
    reg = ToolRegistry()
    reg.register(EchoTool())  # 底座 id（无点号）→ core
    load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)
    active: set = set()
    tool = ToolListTool(reg, active, get_plugin_summaries())
    res = tool.run({}, None)
    assert res.success
    rows = {r["id"]: r for r in res.data["tools"]}
    # 形态分类
    assert rows["echo"]["source_type"] == "core"
    assert rows["reload_a.hi"]["source_type"] == "plugin"
    # privileged 标记
    assert rows["reload_a.hi"]["privileged"] is False
    assert rows["priv_plug.x"]["privileged"] is True
    # 展开态：默认隐藏的插件 tool 未展开
    assert rows["reload_a.hi"]["expanded"] is False
    active.add("reload_a")
    res2 = tool.run({}, None)
    rows2 = {r["id"]: r for r in res2.data["tools"]}
    assert rows2["reload_a.hi"]["expanded"] is True
