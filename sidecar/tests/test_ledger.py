"""能力台账管理（P3 操作闭环 + B/E）：卸载/停用/启用/状态/更新 + SourceStore 持久化 + 特权保护。"""
from pathlib import Path

import pytest

from yibao_brain.tools.ledger import ToolDisableTool, ToolEnableTool, ToolStatusTool, ToolUninstallTool, ToolUpdateTool
from yibao_brain.tools.management import PluginManager, SkillManager, SourceStore
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import get_plugin_summaries, load_plugins
from yibao_brain.tools import EchoTool, ToolRegistry, UsePluginTool

PLUGIN_A = """
id = "reload_a"
name = "普通插件 A"
capabilities = ["llm"]

[[tool]]
id = "hi"
type = "prompt"
description = "A 打招呼"
risk = "L1"
[prompt.template]
text = "hi from A"
"""

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


def _write_plugin(root: Path, name: str, manifest: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.toml").write_text(manifest, encoding="utf-8")
    return d


def _tool_names(reg: ToolRegistry, active: set | None = None) -> set[str]:
    return {s["function"]["name"] if "function" in s else s["name"] for s in reg.openai_tools(active_plugins=active)}


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "reload_a", PLUGIN_A)
    _write_plugin(root, "priv_plug", PLUGIN_PRIVILEGED)
    reg = ToolRegistry()
    reg.register(EchoTool())  # 底座：不可卸/不可停
    load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)
    active: set = set()
    store = SourceStore(tmp_path / "sources.json")
    managers = {"plugin": PluginManager(reg, root), "skill": SkillManager(tmp_path / "skills")}
    return reg, active, root, managers, store


def _toolkit(reg, active, managers, store):
    return (ToolDisableTool(reg, active, managers, store),
            ToolEnableTool(reg, active, managers, store),
            ToolUninstallTool(reg, active, managers, store),
            ToolStatusTool(reg, active, managers, store),
            ToolUpdateTool(reg, active, managers, store))


# ---------- 停用 / 启用 ----------


def test_tool_disable_hides_plugin_and_rejects_expand(env):
    reg, active, _, managers, store = env
    disable, *_ = _toolkit(reg, active, managers, store)
    res = disable.run({"id": "reload_a"}, None)
    assert res.success
    assert reg.source_disabled("reload_a")
    names = _tool_names(reg, {"reload_a"})
    assert "reload_a_hi" not in names
    r2 = UsePluginTool(reg, active, get_plugin_summaries()).run({"plugin": "reload_a"}, None)
    assert not r2.success
    assert "停用" in r2.error


def test_tool_enable_restores_source(env):
    reg, active, _, managers, store = env
    disable, enable, *_ = _toolkit(reg, active, managers, store)
    disable.run({"id": "reload_a"}, None)
    res = enable.run({"id": "reload_a"}, None)
    assert res.success
    assert not reg.source_disabled("reload_a")
    assert "reload_a_hi" in _tool_names(reg, {"reload_a"})


def test_tool_disable_core_rejected(env):
    reg, _, _, managers, store = env
    disable, *_ = _toolkit(reg, set(), managers, store)
    res = disable.run({"id": "echo"}, None)
    assert not res.success
    assert "不可停用" in res.error


def test_tool_disable_privileged_rejected(env):
    reg, _, _, managers, store = env
    disable, *_ = _toolkit(reg, set(), managers, store)
    res = disable.run({"id": "priv_plug"}, None)
    assert not res.success
    assert "特权" in res.error


# ---------- 卸载 ----------


def test_tool_uninstall_plugin_deletes_dir_and_unregisters(env):
    reg, active, root, managers, store = env
    active.add("reload_a")
    _, _, uninstall, *_ = _toolkit(reg, active, managers, store)
    res = uninstall.run({"id": "reload_a"}, None)
    assert res.success
    assert "reload_a.hi" not in [t.id for t in reg.list()]
    assert "reload_a" not in reg.plugin_ids()
    assert not (root / "reload_a").exists()
    assert "reload_a" not in active


def test_tool_uninstall_privileged_rejected(env):
    reg, active, root, managers, store = env
    _, _, uninstall, *_ = _toolkit(reg, active, managers, store)
    res = uninstall.run({"id": "priv_plug"}, None)
    assert not res.success
    assert "特权" in res.error
    assert reg.get("priv_plug.x") is not None
    assert (root / "priv_plug").exists()


def test_tool_uninstall_core_rejected(env):
    reg, active, _, managers, store = env
    _, _, uninstall, *_ = _toolkit(reg, active, managers, store)
    res = uninstall.run({"id": "echo"}, None)
    assert not res.success
    assert "不可卸载" in res.error


# ---------- 状态 / 更新 ----------


def test_tool_status_reflects_plugin(env):
    reg, _, _, managers, store = env
    _, _, _, status, _ = _toolkit(reg, set(), managers, store)
    res = status.run({"id": "reload_a"}, None)
    assert res.success
    assert res.data["status"] == "active"
    assert res.data["source_type"] == "plugin"


def test_tool_update_plugin_reloads(env):
    reg, _, _, managers, store = env
    _, _, _, _, update = _toolkit(reg, set(), managers, store)
    res = update.run({"id": "reload_a"}, None)
    assert res.success
    assert reg.get("reload_a.hi") is not None


# ---------- SourceStore 持久化（disabled 跨重启保留） ----------


def test_source_store_persists_disabled_across_restart(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(root, "reload_a", PLUGIN_A)
    store_path = tmp_path / "sources.json"

    # 第一次：加载 + 停用 → 落盘
    reg1 = ToolRegistry()
    load_plugins(root, reg1, memory=FakeMemory(), http=None, llm=None)
    store1 = SourceStore(store_path)
    pm1 = PluginManager(reg1, root)
    discovered = {r.id: r for r in pm1.discover()}
    store1.save(discovered)
    disable = ToolDisableTool(reg1, set(), {"plugin": pm1}, store1)
    disable.run({"id": "reload_a"}, None)
    assert store1.load()["reload_a"]["status"] == "disabled"

    # 第二次（重启）：discover + merge_status → disabled 恢复
    reg2 = ToolRegistry()
    load_plugins(root, reg2, memory=FakeMemory(), http=None, llm=None)
    store2 = SourceStore(store_path)
    pm2 = PluginManager(reg2, root)
    merged = store2.merge_status({r.id: r for r in pm2.discover()})
    assert merged["reload_a"].status == "disabled"
    for rid, rec in merged.items():
        if rec.status == "disabled":
            reg2.disable_source(rid)
    assert reg2.source_disabled("reload_a")


def test_plugin_discover_fills_bundled_skills(tmp_path):
    """PluginManager.discover 填 bundled_skills：包内 skills/**/SKILL.md 随加载注册的 id 列表。"""
    root = tmp_path / "plugins"
    d = _write_plugin(root, "reload_a", PLUGIN_A)
    skill_dir = d / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: 演示\n---\n\n# Demo\n", encoding="utf-8")
    reg = ToolRegistry()
    load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)
    rec = {r.id: r for r in PluginManager(reg, root).discover()}["reload_a"]
    assert rec.bundled_skills == ["reload_a:demo"]


def test_unload_plugin_removes_bundled_skills(tmp_path):
    """卸插件：包内技能一起走（spec：卸插件时包内 Skill 一起走）。"""
    from yibao_brain.plugins import unload_plugin
    from yibao_brain.tools.skills_index import index

    root = tmp_path / "plugins"
    d = _write_plugin(root, "reload_a", PLUGIN_A)
    skill_dir = d / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\ndescription: 演示\n---\n\n# Demo\n", encoding="utf-8")
    reg = ToolRegistry()
    load_plugins(root, reg, memory=FakeMemory(), http=None, llm=None)
    assert "reload_a:demo" in index()
    unload_plugin(reg, "reload_a")
    assert "reload_a:demo" not in index()
