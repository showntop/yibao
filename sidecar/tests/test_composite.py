"""复合技能单测：subprocess 走 monkeypatch，AX 走 FakeHost。"""
from __future__ import annotations

import subprocess

from yibao_brain.ipc import RiskLevel
from yibao_brain.skills import SkillContext, SkillRegistry
from yibao_brain.skills_composite import (
    FindFileSkill,
    OpenPathSkill,
    WebSearchSkill,
    WriteNoteSkill,
    register_composite_skills,
)

from .fakes import FakeHost, _FakeHandle


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---- find_file ----


def test_find_file_returns_paths(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return _cp("/Users/d/报表.xlsx\n/Users/d/报销单.pdf\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = FindFileSkill().run({"query": "报销"}, SkillContext())
    assert r.success
    assert r.data["paths"] == ["/Users/d/报表.xlsx", "/Users/d/报销单.pdf"]
    assert r.data["count"] == 2
    assert calls[0][0] == "mdfind"


def test_find_file_empty_query():
    r = FindFileSkill().run({"query": "  "}, SkillContext())
    assert not r.success


def test_find_file_mdfind_missing(monkeypatch):
    def fake_run(argv, **kw):
        raise FileNotFoundError("mdfind")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = FindFileSkill().run({"query": "x"}, SkillContext())
    assert not r.success


# ---- web_search ----


def test_web_search_opens_engine_url(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or _cp())
    r = WebSearchSkill(engine="baidu").run({"query": "译宝 AI"}, SkillContext())
    assert r.success
    assert calls[0][0] == "open"
    url = calls[0][1]
    assert url.startswith("https://www.baidu.com/s?wd=")
    assert "%E8%AF%91%E5%AE%9D" in url  # urllib.parse.quote 编码


def test_web_search_engine_from_config(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or _cp())
    monkeypatch.setenv("YIBAO_SEARCH_ENGINE", "bing")
    r = WebSearchSkill().run({"query": "yibao"}, SkillContext())
    assert r.success
    assert calls[0][1].startswith("https://www.bing.com/search?q=")


def test_web_search_empty_query():
    r = WebSearchSkill().run({"query": ""}, SkillContext())
    assert not r.success


# ---- open_path ----


def test_open_path_existing(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or _cp())
    f = tmp_path / "a.txt"
    f.write_text("x")
    r = OpenPathSkill().run({"path": str(f)}, SkillContext())
    assert r.success
    assert calls[0] == ["open", str(f)]


def test_open_path_reveal(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv) or _cp())
    f = tmp_path / "a.txt"
    f.write_text("x")
    r = OpenPathSkill().run({"path": str(f), "reveal": True}, SkillContext())
    assert r.success
    assert calls[0] == ["open", "-R", str(f)]


def test_open_path_missing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: _cp())
    r = OpenPathSkill().run({"path": "/nonexistent/yibao-xyz"}, SkillContext())
    assert not r.success


# ---- write_note ----


def test_write_note_via_ax_set_value(monkeypatch):
    monkeypatch.setattr("yibao_brain.skills_composite.time.sleep", lambda s: None)
    host = FakeHost()
    host.a11y.handles[("AXTextArea", None)] = _FakeHandle(role="AXTextArea")
    r = WriteNoteSkill().run({"text": "你好译宝"}, SkillContext(host=host))
    assert r.success
    assert host.a11y.launch_calls == ["TextEdit"]
    assert host.a11y.set_value_calls[0][1] == "你好译宝"
    assert host.input.types == []


def test_write_note_fallback_type_text(monkeypatch):
    monkeypatch.setattr("yibao_brain.skills_composite.time.sleep", lambda s: None)
    host = FakeHost()
    host.a11y.handles[("AXTextArea", None)] = _FakeHandle(role="AXTextArea")
    host.a11y.set_value_ok = False
    r = WriteNoteSkill().run({"text": "abc"}, SkillContext(host=host))
    assert r.success
    assert r.data["method"] == "type"
    assert host.input.types == ["abc"]


def test_write_note_no_text():
    r = WriteNoteSkill().run({"text": ""}, SkillContext(host=FakeHost()))
    assert not r.success


def test_write_note_no_host():
    r = WriteNoteSkill().run({"text": "x"}, SkillContext())
    assert not r.success


def test_write_note_launch_failure(monkeypatch):
    monkeypatch.setattr("yibao_brain.skills_composite.time.sleep", lambda s: None)
    host = FakeHost()
    host.a11y.launch_pid = None
    r = WriteNoteSkill().run({"text": "x"}, SkillContext(host=host))
    assert not r.success


# ---- 注册 ----


def test_register_composite_skills():
    reg = SkillRegistry()
    register_composite_skills(reg)
    ids = {s.id for s in reg.list()}
    assert ids == {"find_file", "web_search", "open_path", "write_note"}
    assert all("parameters" in t for t in reg.openai_tools())
    assert reg.get("find_file").default_risk == RiskLevel.L0_READONLY
    assert reg.get("web_search").default_risk == RiskLevel.L1_LOW
    assert reg.get("open_path").default_risk == RiskLevel.L1_LOW
    assert reg.get("write_note").default_risk == RiskLevel.L2_MEDIUM
