"""技能桥（2026-08-23 use_skill 重构）端到端测试。

- use_skill（底座）：展开技能说明书到主上下文（返回 SKILL.md+references 全文，不单轮生成）
- skills 插件（管理）：list / refresh / install / import
"""
import os
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import load_plugins
from yibao_brain.tools.skills_index import refresh_index
from yibao_brain.tools import ToolRegistry, UseSkillTool

REPO_ROOT = Path(__file__).resolve().parents[2]

SAMPLE_SKILL = """---
name: sample-demo
description: 演示技能：把用户任务原样回显。
---

# Sample Demo

When to Use: 演示用。
Routing: 直接回显 $ARGUMENTS。
"""


@pytest.fixture
def skills_dir(tmp_path, monkeypatch):
    d = tmp_path / "skills"
    (d / "sample").mkdir(parents=True)
    (d / "sample" / "SKILL.md").write_text(SAMPLE_SKILL, encoding="utf-8")
    monkeypatch.setenv("YIBAO_SKILLS_DIR", str(d))
    return d


@pytest.fixture
def env(skills_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    refresh_index()  # 用测试技能目录重建索引
    reg = ToolRegistry()
    reg.register(UseSkillTool())  # 底座展开工具

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=FakeProvider(text=""),
    )
    return reg, results


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


# ---------- use_skill（底座展开说明书） ----------


def test_use_skill_expands_doc_into_context(env):
    reg, _ = env
    res = _run(reg, "use_skill", {"skill": "sample"})
    assert res.success
    assert res.data["skill"] == "skill:sample"
    # 说明书全文（SKILL.md）进 data.text → loop 回填 messages
    assert "Sample Demo" in res.data["text"]
    assert "When to Use" in res.data["text"]


def test_use_skill_accepts_skill_prefix(env):
    reg, _ = env
    res = _run(reg, "use_skill", {"skill": "skill:sample"})
    assert res.success


def test_use_skill_unknown_skill_fails(env):
    reg, _ = env
    res = _run(reg, "use_skill", {"skill": "nope"})
    assert not res.success
    assert "没有这个技能" in res.error


# ---------- skills 插件（管理） ----------


def test_skills_loaded_and_list_shows_skill(env):
    reg, results = env
    assert "skills" in results, f"skills 插件加载失败：{results.get('skills')}"
    res = _run(reg, "skills.list", {})
    assert res.success
    assert any(r["id"] == "skill:sample" for r in res.data["skills"])


def test_skills_refresh_hot_loads_new_skill(env):
    reg, _ = env
    d = Path(os.environ["YIBAO_SKILLS_DIR"])
    (d / "ppt").mkdir()
    (d / "ppt" / "SKILL.md").write_text(
        "---\nname: ppt\n---\n\n# PPT\n\n做演示文稿。", encoding="utf-8"
    )
    res = _run(reg, "skills.refresh", {})
    assert res.success
    assert "skill:ppt" in res.data["skills"]
    # 热加载后 use_skill 立即可用（无需重启）
    res2 = _run(reg, "use_skill", {"skill": "ppt"})
    assert res2.success


def test_skills_install_local_dir_then_auto_refresh(env):
    reg, _ = env
    src = Path(os.environ["YIBAO_SKILLS_DIR"]).parent / "newskill"
    src.mkdir()
    (src / "SKILL.md").write_text("---\nname: newskill\n---\n\n新技能。", encoding="utf-8")
    res = _run(reg, "skills.install", {"url": str(src)})
    assert res.success
    assert "newskill" in res.data["path"]
    res2 = _run(reg, "skills.list", {})
    assert any(r["id"] == "skill:newskill" for r in res2.data["skills"])


# ---------- 嵌套集合（Anthropic 官方仓库结构） ----------


def test_skills_scan_finds_nested_collection(env):
    reg, _ = env
    d = Path(os.environ["YIBAO_SKILLS_DIR"])
    nested = d / "slides" / "skills" / "pptx"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: pptx\n---\n\n# PPTX\n\n做演示文稿。", encoding="utf-8"
    )
    res = _run(reg, "skills.refresh", {})
    assert res.success
    assert "skill:slides/skills/pptx" in res.data["skills"]
    # 短名唯一命中嵌套技能
    res2 = _run(reg, "use_skill", {"skill": "pptx"})
    assert res2.success
    assert res2.data["skill"] == "skill:slides/skills/pptx"


def test_skills_install_collection_dir_then_recursive_scan(env):
    reg, _ = env
    src = Path(os.environ["YIBAO_SKILLS_DIR"]).parent / "col"
    (src / "skills" / "a").mkdir(parents=True)
    (src / "skills" / "a" / "SKILL.md").write_text("---\nname: a\n---\n\nA。", encoding="utf-8")
    res = _run(reg, "skills.install", {"url": str(src)})
    assert res.success
    assert "skill:col/skills/a" in res.data["skills"]


# ---------- 固化（skill_import → 声明式插件 + 单形态激活） ----------


def test_skills_import_generates_loadable_plugin(env, tmp_path, monkeypatch):
    reg, _ = env
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(plugins_dir))
    res = _run(reg, "skills.import", {"skill": "sample"})
    assert res.success
    reg2 = ToolRegistry()
    results = load_plugins(plugins_dir, reg2, memory=FakeMemory(), http=None, llm=None)
    assert "sample_demo" in results, f"固化插件加载失败：{results}"
    assert "sample_demo.run" in [t.id for t in reg2.list()]
    # 单形态激活（spec §G.3）：固化后桥条目停用，skills.list 不再显示该技能
    res2 = _run(reg, "skills.list", {})
    assert not any(r["id"] == "skill:sample" for r in res2.data["skills"])


# ---------- 插件包内技能（bundled_skills，spec §对象模型） ----------


def test_bundled_skills_registered_on_plugin_load(env):
    """插件加载即注册包内 skills/**/SKILL.md（<pid>:* 命名空间），进全量索引。"""
    from yibao_brain.tools.skills_index import bundled_for, index

    _, results = env
    assert results.get("zimeiti") == "ok" and results.get("forge") == "ok"
    idx = index()
    assert "zimeiti:write" in idx
    for name in ("triage", "challenge", "scan", "prd"):
        assert f"forge:{name}" in idx, name
    assert bundled_for("forge") == ["forge:challenge", "forge:prd", "forge:scan", "forge:triage"]


def test_use_skill_expands_bundled_skill(env):
    """use_skill 经 owner 前缀 id 展开插件包内技能说明书（替代面：原 zimeiti.guide）。"""
    reg, _ = env
    res = _run(reg, "use_skill", {"skill": "zimeiti:write"})
    assert res.success and "钩子" in res.data["text"]


def test_refresh_index_preserves_bundled(env):
    """refresh 重建根目录扫描不冲掉 bundled 注册（两条生命周期分离）。"""
    from yibao_brain.tools.skills_index import index, refresh_index

    env
    refresh_index()
    assert "zimeiti:write" in index()
