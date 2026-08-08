"""zimeiti.mat_save：url+text 同给不重抓（浏览器扩展链路：正文由扩展提取，url 仅作来源）。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_tool(name):
    """按插件加载器同款方式按文件自包含加载 tools/<name>.py。"""
    path = Path(__file__).resolve().parents[2] / "plugins" / "zimeiti" / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"zimeiti_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_mat_save():
    return _load_tool("mat_save")


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


# ---------- 先存后整理（defer 秒回 + mat_enrich 后台补元数据）----------


def test_defer_saves_immediately_without_llm():
    """defer=true：不碰 LLM 先落库——即席标题取调用方 title，tags 空，data 带 pending。"""
    mod = _load_mat_save()
    ctx = _FakeCtx()
    ctx.llm = None  # defer 路径不需要 LLM
    r = _skill(mod).run({"text": "正文内容", "title": "页面标题", "defer": True}, ctx)
    assert r.success, r.error
    assert r.data["title"] == "页面标题"
    assert r.data["pending"] is True
    table, row = ctx.db.rows[0]
    assert row["title"] == "页面标题"
    assert row["summary"] == "正文内容"[:200]
    assert row["tags"] == ""


def test_defer_title_fallback_to_first_line():
    """defer 无 title：首句当标题（保底）。"""
    mod = _load_mat_save()
    ctx = _FakeCtx()
    ctx.llm = None
    r = _skill(mod).run({"text": "首句当标题\n\n后面是正文", "defer": True}, ctx)
    assert r.success, r.error
    assert r.data["title"] == "首句当标题"


def test_non_defer_still_requires_llm():
    """非 defer（旧路径）：无 LLM 能力仍报错，行为不变。"""
    mod = _load_mat_save()
    ctx = _FakeCtx()
    ctx.llm = None
    r = _skill(mod).run({"text": "正文"}, ctx)
    assert not r.success


class _EnrichDb:
    def __init__(self, row):
        self._row = row
        self.updated = []

    def query(self, table, where=None, order=None, limit=None):
        if where and where.get("id") == self._row.get("id"):
            return [dict(self._row)]
        return []

    def update(self, table, row_id, fields):
        self.updated.append((table, row_id, dict(fields)))


def test_mat_enrich_updates_row_with_llm_meta():
    """mat_enrich：按 id 读正文 → LLM 摘要打标 → update 回行（title/summary/tags + updated_at）。"""
    mod = _load_tool("mat_enrich")
    row = {"id": "m1", "title": "即席", "summary": "", "tags": "", "content": "正文正文"}
    db = _EnrichDb(row)
    ctx = SimpleNamespace(llm=_FakeLlm(), db=db)
    r = mod.make_tools(None)[0].run({"id": "m1"}, ctx)
    assert r.success, r.error
    table, rid, fields = db.updated[0]
    assert (table, rid) == ("materials", "m1")
    assert fields["title"] == "测试标题"
    assert fields["summary"] == "测试摘要"
    assert fields["tags"] == "a"
    assert "updated_at" in fields


def test_mat_enrich_missing_id_and_ghost_row():
    """mat_enrich：缺 id / 行不存在 → 失败不炸。"""
    mod = _load_tool("mat_enrich")
    ctx = SimpleNamespace(llm=_FakeLlm(), db=_EnrichDb({"id": "m1", "content": "x"}))
    assert not mod.make_tools(None)[0].run({}, ctx).success
    assert not mod.make_tools(None)[0].run({"id": "ghost"}, ctx).success
