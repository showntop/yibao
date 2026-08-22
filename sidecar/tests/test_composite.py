"""复合技能单测：subprocess 走 monkeypatch，AX 走 FakeHost。"""
from __future__ import annotations

import json
import subprocess

import pytest

from yibao_brain.ipc import RiskLevel
from yibao_brain.skills import SkillContext, SkillRegistry
from yibao_brain.skills_composite import (
    ExtractUrlSkill,
    FindFileSkill,
    OpenPathSkill,
    WebSearchSkill,
    WriteNoteSkill,
    register_composite_skills,
)

from .fakes import FakeHost, _FakeHandle


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """隔离数据目录：避免读真机 settings.json（search.provider 等运行期配置）污染断言。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    return tmp_path


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
    assert ids == {"find_file", "web_search", "extract_url", "open_path", "write_note"}
    assert all("parameters" in t for t in reg.openai_tools())
    assert reg.get("find_file").default_risk == RiskLevel.L0_READONLY
    assert reg.get("web_search").default_risk == RiskLevel.L1_LOW
    assert reg.get("extract_url").default_risk == RiskLevel.L1_LOW
    assert reg.get("open_path").default_risk == RiskLevel.L1_LOW
    assert reg.get("write_note").default_risk == RiskLevel.L2_MEDIUM


# ---- web_search provider（结构化结果）----

_DDG_HTML = """<html><body><div class="result results_links results_links_deep web-result">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">译宝 AI 介绍</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">这是 <b>译宝</b> 的摘要</a>
</div></body></html>"""


def test_web_search_ddg_structured(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, headers=None):
        calls.append(url)
        return _DDG_HTML.encode("utf-8")

    monkeypatch.setattr("yibao_brain.skills_composite.get_bytes", fake_get)
    r = WebSearchSkill(provider="ddg").run({"query": "译宝 AI"}, SkillContext())
    assert r.success
    assert r.data["provider"] == "ddg"
    assert "html.duckduckgo.com" in calls[0]
    assert r.data["results"] == [{
        "title": "译宝 AI 介绍",
        "url": "https://example.com/a",
        "snippet": "这是 译宝 的摘要",
    }]
    assert r.data["browser_url"].startswith("https://www.baidu.com/s?wd=")


def test_web_search_searxng_structured(monkeypatch):
    calls: list[str] = []
    payload = json.dumps({"results": [
        {"title": "结果一", "url": "https://a.example.com", "content": "第一段摘要"},
        {"title": "结果二", "url": "https://b.example.com", "content": "第二段摘要"},
        {"title": "", "url": "https://c.example.com", "content": "没标题"},
    ]}).encode()

    def fake_get(url: str, headers=None):
        calls.append(url)
        return payload

    monkeypatch.setattr("yibao_brain.skills_composite.get_bytes", fake_get)
    r = WebSearchSkill(provider="searxng", searxng_url="http://127.0.0.1:8888").run({"query": "x"}, SkillContext())
    assert r.success
    assert "format=json" in calls[0]
    assert r.data["results"] == [
        {"title": "结果一", "url": "https://a.example.com", "snippet": "第一段摘要"},
        {"title": "结果二", "url": "https://b.example.com", "snippet": "第二段摘要"},
    ]


def test_web_search_searxng_missing_url():
    r = WebSearchSkill(provider="searxng").run({"query": "x"}, SkillContext())
    assert not r.success
    assert "SearXNG" in r.error


def test_web_search_brave_structured(monkeypatch):
    payload = json.dumps({"web": {"results": [
        {"title": "T1", "url": "https://x.com/1", "description": "S1"},
    ]}}).encode()
    monkeypatch.setenv("YIBAO_SEARCH_BRAVE_KEY", "k")
    calls: list[tuple[str, dict | None]] = []

    def fake_get(url: str, headers=None):
        calls.append((url, headers))
        return payload

    monkeypatch.setattr("yibao_brain.skills_composite.get_bytes", fake_get)
    r = WebSearchSkill(provider="brave").run({"query": "x"}, SkillContext())
    assert r.success
    assert calls[0][1]["X-Subscription-Token"] == "k"
    assert r.data["results"] == [{"title": "T1", "url": "https://x.com/1", "snippet": "S1"}]


def test_web_search_tavily_structured(monkeypatch):
    payload = json.dumps({"results": [
        {"title": "T", "url": "https://t.com", "content": "C"},
    ]}).encode()
    monkeypatch.setenv("YIBAO_SEARCH_TAVILY_KEY", "k")
    calls: list[str] = []

    def fake_post(url: str, body: bytes, headers: dict):
        calls.append(url)
        return payload

    monkeypatch.setattr("yibao_brain.skills_composite.post_bytes", fake_post)
    r = WebSearchSkill(provider="tavily").run({"query": "x"}, SkillContext())
    assert r.success
    assert calls[0] == "https://api.tavily.com/search"
    assert r.data["results"] == [{"title": "T", "url": "https://t.com", "snippet": "C"}]


def test_web_search_api_missing_key(monkeypatch):
    monkeypatch.delenv("YIBAO_SEARCH_BRAVE_KEY", raising=False)
    r = WebSearchSkill(provider="brave").run({"query": "x"}, SkillContext())
    assert not r.success
    assert "YIBAO_SEARCH_BRAVE_KEY" in r.error


# ---- extract_url ----


class _FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data
        self.headers = _FakeHeaders()

    def read(self, n=-1):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_extract_url_fetches_text(monkeypatch):
    html = ("<html><head><title>译宝官网</title></head><body>"
            "<script>var x=1;</script><nav>菜单</nav>"
            "<p>译宝是 AI 桌面助手，可以操作电脑。</p></body></html>").encode()
    monkeypatch.setattr(
        "yibao_brain.skills_composite.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(html),
    )
    r = ExtractUrlSkill().run({"url": "https://example.com/"}, SkillContext())
    assert r.success
    assert r.data["title"] == "译宝官网"
    assert "译宝是 AI 桌面助手" in r.data["text"]


def test_extract_url_bad_url():
    r = ExtractUrlSkill().run({"url": "ftp://x"}, SkillContext())
    assert not r.success


def test_extract_url_empty_body(monkeypatch):
    html = b"<html><head><title>x</title></head><body>  </body></html>"
    monkeypatch.setattr(
        "yibao_brain.skills_composite.urllib.request.urlopen",
        lambda req, timeout=10: _FakeResp(html),
    )
    r = ExtractUrlSkill().run({"url": "https://example.com/"}, SkillContext())
    assert not r.success
