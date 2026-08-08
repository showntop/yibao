"""zimeiti.mat_save：url+text 同给不重抓（浏览器扩展链路：正文由扩展提取，url 仅作来源）。"""
import importlib.util
from pathlib import Path


def _load_mat_save():
    """按插件加载器同款方式按文件自包含加载 mat_save.py。"""
    path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "tools" / "mat_save.py"
    spec = importlib.util.spec_from_file_location("zimeiti_mat_save", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeLlm:
    def chat(self, prompt):
        return '{"title": "测试标题", "summary": "测试摘要", "tags": ["a"]}'


class _FakeDb:
    def __init__(self):
        self.rows = []

    def insert(self, table, row):
        self.rows.append((table, dict(row)))
        return "rid-1"


class _FakeCtx:
    def __init__(self):
        self.llm = _FakeLlm()
        self.db = _FakeDb()


def _skill(mod):
    return mod.make_tools(None)[0]


def test_url_and_text_skips_fetch_and_keeps_source_url(monkeypatch):
    mod = _load_mat_save()
    monkeypatch.setattr(mod, "_fetch_text", lambda url: (_ for _ in ()).throw(AssertionError("不应重抓")))
    ctx = _FakeCtx()
    r = _skill(mod).run({"url": "https://example.com/a", "text": "页面正文"}, ctx)
    assert r.success, r.error
    table, row = ctx.db.rows[0]
    assert table == "materials"
    assert row["url"] == "https://example.com/a"   # 来源留住
    assert row["content"] == "页面正文"            # 正文用扩展给的
    assert row["kind"] == "link"


def test_url_only_fetches(monkeypatch):
    mod = _load_mat_save()
    calls = []
    monkeypatch.setattr(mod, "_fetch_text", lambda url: calls.append(url) or "抓到的正文")
    ctx = _FakeCtx()
    r = _skill(mod).run({"url": "https://example.com/b"}, ctx)
    assert r.success, r.error
    assert calls == ["https://example.com/b"]
    assert ctx.db.rows[0][1]["content"] == "抓到的正文"


def test_text_only_no_fetch(monkeypatch):
    mod = _load_mat_save()
    monkeypatch.setattr(mod, "_fetch_text", lambda url: (_ for _ in ()).throw(AssertionError("不应抓")))
    ctx = _FakeCtx()
    r = _skill(mod).run({"text": "纯文本"}, ctx)
    assert r.success, r.error
    assert ctx.db.rows[0][1]["kind"] == "note"
