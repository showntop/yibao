"""插件加载器 + capability 权限模型 + 命名空间强制（v2 方案 §3）。"""
from pathlib import Path

import pytest

from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, ScopedMemory, load_plugins
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.skills import EchoSkill, Skill, SkillContext, SkillRegistry


# ---------- 测试素材 ----------


class FakeHttp:
    """假 http 客户端：记录调用，返回固定 json。"""

    def __init__(self, payload=None):
        self.payload = {"ok": True} if payload is None else payload
        self.calls: list = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.payload

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.payload


NOTES_MANIFEST = """
id = "notes"
name = "闪念盘"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "text", type = "text"},
  {name = "created_at", type = "integer"},
]
indexes = ["created_at"]

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
[tool.params]
text = {type = "string", description = "内容"}
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "list"
type = "db"
description = "列出闪念"
[tool.db]
op = "query"
table = "notes"
"""


def _write_plugin(root: Path, name: str, manifest: str, files: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "manifest.toml").write_text(manifest, encoding="utf-8")
    for rel, content in (files or {}).items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def _load(root, reg, **kw):
    kw.setdefault("memory", FakeMemory())
    kw.setdefault("http", FakeHttp())
    kw.setdefault("llm", LlmChat(FakeProvider()))
    return load_plugins(root, reg, **kw)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """把插件数据目录指到 tmp（db 落盘不碰真实用户目录）。"""
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


# ---------- 声明式 db tool 端到端 ----------


def test_db_tool_end_to_end(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = SkillRegistry()
    results = _load(tmp_path, reg)
    assert results == {"notes": "ok"}

    keep = reg.get("notes.keep")
    assert keep.default_risk == RiskLevel.L1_LOW
    r = keep.run({"text": "买点牛奶", "created_at": 1}, keep.plugin_ctx)
    assert r.success and r.data["id"]

    lst = reg.get("notes.list")
    r2 = lst.run({"order": "created_at DESC"}, lst.plugin_ctx)
    assert r2.success
    assert [row["text"] for row in r2.data["rows"]] == ["买点牛奶"]
    # 数据真的落在了插件自己的 data.db
    assert (data_dir / "plugins" / "notes" / "data.db").is_file()


def test_db_tool_openai_schema_uses_manifest(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = SkillRegistry()
    _load(tmp_path, reg)
    schema = reg.get("notes.keep").openai_schema()
    assert schema["name"] == "notes.keep"
    assert schema["description"] == "记一条闪念"
    assert "text" in schema["parameters"]["properties"]


# ---------- capability 权限模型 ----------


def test_capability_scoping_unset_capabilities_are_none(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)  # 只声明 db
    reg = SkillRegistry()
    _load(tmp_path, reg)
    ctx = reg.get("notes.keep").plugin_ctx
    assert ctx.db is not None
    assert ctx.memory is None and ctx.http is None and ctx.llm is None and ctx.host is None
    assert reg.get("notes.keep").plugin_capabilities == frozenset({"db"})


def test_prompt_tool_without_llm_capability_fails_but_isolated(data_dir, tmp_path):
    bad = """
id = "badp"
capabilities = []

[[tool]]
id = "sum"
type = "prompt"
description = "总结"
[tool.prompt]
template = "请总结：{{text}}"
"""
    _write_plugin(tmp_path, "badp", bad)
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = SkillRegistry()
    results = _load(tmp_path, reg)
    assert results["notes"] == "ok"          # 坏插件不拖累好插件
    assert "llm" in results["badp"]          # 报错指明缺的能力
    with pytest.raises(KeyError):
        reg.get("badp.sum")                  # 加载失败不留半成品 tool


def test_memory_capability_wraps_scoped_memory(data_dir, tmp_path):
    manifest = """
id = "memo"
capabilities = ["memory"]
mem_namespace = "memo_ns"

[[tool]]
id = "noop"
type = "composite"
description = "空编排"
[tool.composite]
steps = []
"""
    _write_plugin(tmp_path, "memo", manifest)
    reg = SkillRegistry()
    _load(tmp_path, reg)
    ctx = reg.get("memo.noop").plugin_ctx
    assert isinstance(ctx.memory, ScopedMemory)
    assert ctx.db is None


# ---------- ScopedMemory ----------


def test_scoped_memory_prefixes_user_id():
    mem = FakeMemory()
    sm = ScopedMemory(mem, "notes")
    sm.add("hello world", "u1")
    assert list(mem._by_user.keys()) == ["notes:u1"]
    assert sm.recall("hello", "u1") == ["hello world"]
    assert mem.recall("hello", "u1") == []  # 不带前缀查不到（命名空间隔离）


# ---------- prompt / http tool ----------


def test_prompt_tool_renders_template(data_dir, tmp_path):
    manifest = """
id = "writer"
capabilities = ["llm"]

[[tool]]
id = "sum"
type = "prompt"
description = "总结文本"
[tool.params]
text = {type = "string"}
[tool.prompt]
template = "请总结：{{text}}"
"""
    _write_plugin(tmp_path, "writer", manifest)
    prov = FakeProvider(text="摘要")
    reg = SkillRegistry()
    _load(tmp_path, reg, llm=LlmChat(prov))
    skill = reg.get("writer.sum")
    r = skill.run({"text": "一长段"}, skill.plugin_ctx)
    assert r.success and r.data == {"text": "摘要"}
    assert prov.calls[0]["messages"] == [{"role": "user", "content": "请总结：一长段"}]


def test_http_tool_renders_url_and_returns_json(data_dir, tmp_path):
    manifest = """
id = "fetcher"
capabilities = ["http"]

[[tool]]
id = "fetch"
type = "http"
description = "取一条"
[tool.params]
eid = {type = "string"}
[tool.http]
method = "GET"
url = "https://api.example.com/items/{{eid}}"
"""
    _write_plugin(tmp_path, "fetcher", manifest)
    http = FakeHttp({"id": "7", "name": "x"})
    reg = SkillRegistry()
    _load(tmp_path, reg, http=http)
    skill = reg.get("fetcher.fetch")
    r = skill.run({"eid": "7"}, skill.plugin_ctx)
    assert r.success and r.data == {"id": "7", "name": "x"}
    assert http.calls == [("GET", "https://api.example.com/items/7", {})]


def test_llm_chat_adapter():
    prov = FakeProvider(text="hi")
    assert LlmChat(prov).chat("x") == "hi"
    assert prov.calls[0]["messages"] == [{"role": "user", "content": "x"}]


# ---------- composite ----------


def test_composite_two_steps_with_templates(data_dir, tmp_path):
    manifest = """
id = "notes"
capabilities = ["db", "llm"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "text", type = "text"},
  {name = "created_at", type = "integer"},
]

[[tool]]
id = "keep"
type = "db"
description = "记一条"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "sum"
type = "prompt"
description = "总结"
[tool.params]
text = {type = "string"}
[tool.prompt]
template = "请总结：{{text}}"

[[tool]]
id = "keep_and_sum"
type = "composite"
description = "记一条并总结"
[tool.params]
text = {type = "string"}
[tool.composite]
steps = [
  {tool = "notes.keep", params = {text = "{{input.text}}", created_at = 1}},
  {tool = "notes.sum", params = {text = "{{input.text}}（上一步：{{steps.0.data}}）"}},
]
"""
    _write_plugin(tmp_path, "notes", manifest)
    prov = FakeProvider(text="摘要")
    reg = SkillRegistry()
    results = _load(tmp_path, reg, llm=LlmChat(prov))
    assert results == {"notes": "ok"}

    combo = reg.get("notes.keep_and_sum")
    r = combo.run({"text": "牛奶"}, combo.plugin_ctx)
    assert r.success and r.data == {"text": "摘要"}  # 返回最后一步的 data

    prompt = prov.calls[0]["messages"][0]["content"]
    assert prompt.startswith("请总结：牛奶（上一步：{")
    assert '"id"' in prompt  # steps.0.data 是 insert 返回的 {"id": ...} 的 json

    lst_rows = reg.get("notes.keep").plugin_ctx.db.query("notes")
    assert [row["text"] for row in lst_rows] == ["牛奶"]


def test_composite_stops_on_failure(data_dir, tmp_path):
    manifest = """
id = "notes"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [{name = "id", type = "text", pk = true}, {name = "text", type = "text"}]

[[tool]]
id = "keep"
type = "db"
description = "记一条"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "combo"
type = "composite"
description = "第一步必炸"
[tool.composite]
steps = [
  {tool = "notes.ghost", params = {}},
  {tool = "notes.keep", params = {text = "不应执行"}},
]
"""
    _write_plugin(tmp_path, "notes", manifest)
    reg = SkillRegistry()
    _load(tmp_path, reg)
    combo = reg.get("notes.combo")
    r = combo.run({}, combo.plugin_ctx)
    assert not r.success and "notes.ghost" in r.error
    assert reg.get("notes.keep").plugin_ctx.db.query("notes") == []  # 后续步未执行


# ---------- 命名空间强制 ----------


class _SomeSkill(Skill):
    id = "s"
    description = "占位"

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        return ActionResult(success=True)


def test_plugin_tool_without_prefix_rejected():
    reg = SkillRegistry()
    with pytest.raises(ValueError):
        reg.register(_SomeSkill(), plugin="notes")  # id "s" 不带 "notes." 前缀


def test_plugin_tool_with_prefix_ok():
    reg = SkillRegistry()

    class P(Skill):
        id = "notes.keep"

        def run(self, params, ctx):
            return ActionResult(success=True)

    reg.register(P(), plugin="notes")
    assert reg.get("notes.keep").id == "notes.keep"


def test_duplicate_id_rejected():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    with pytest.raises(ValueError):
        reg.register(EchoSkill())


def test_duplicate_plugin_tool_id_rejected():
    reg = SkillRegistry()

    class P(Skill):
        id = "notes.keep"

        def run(self, params, ctx):
            return ActionResult(success=True)

    reg.register(P(), plugin="notes")
    with pytest.raises(ValueError):
        reg.register(P(), plugin="notes")


def test_base_skill_id_with_dot_rejected():
    reg = SkillRegistry()

    class Evil(Skill):
        id = "notes.fake"  # 底座注册伪装成插件 id

        def run(self, params, ctx):
            return ActionResult(success=True)

    with pytest.raises(ValueError):
        reg.register(Evil())


# ---------- 失败隔离 / 目录扫描 ----------


def test_failure_isolation_bad_manifest(data_dir, tmp_path):
    _write_plugin(tmp_path, "broken", 'id = [unclosed')  # TOML 语法错误
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = SkillRegistry()
    results = _load(tmp_path, reg)
    assert results["notes"] == "ok"
    assert results["broken"] != "ok" and results["broken"]  # 有错误信息
    assert reg.get("notes.keep").id == "notes.keep"


def test_skip_underscore_dirs(data_dir, tmp_path):
    _write_plugin(tmp_path, "_staging", NOTES_MANIFEST.replace('id = "notes"', 'id = "stg"'))
    reg = SkillRegistry()
    results = _load(tmp_path, reg)
    assert results == {}  # _staging 暂存区不加载也不上报
    assert reg.list() == []


def test_missing_plugins_dir_is_noop(tmp_path):
    assert _load(tmp_path / "nonexistent", SkillRegistry()) == {}


# ---------- 代码插件（最小支持）----------


def test_code_plugin(data_dir, tmp_path):
    manifest = """
id = "coder"
capabilities = ["db"]

[[table]]
name = "t"
columns = [{name = "id", type = "text", pk = true}]

[code]
entry = "tools"
"""
    hello_py = '''
from yibao_brain.ipc import ActionResult
from yibao_brain.skills import Skill


class Hello(Skill):
    id = "coder.hello"
    description = "代码插件示例"

    def run(self, params, ctx):
        return ActionResult(success=True, data={"has_db": ctx.db is not None})


def make_tools(ctx):
    return [Hello()]
'''
    _write_plugin(tmp_path, "coder", manifest, {"tools/hello.py": hello_py})
    reg = SkillRegistry()
    results = _load(tmp_path, reg)
    assert results == {"coder": "ok"}
    skill = reg.get("coder.hello")
    assert skill.plugin_ctx is not None and skill.plugin_capabilities == frozenset({"db"})
    r = skill.run({}, skill.plugin_ctx)
    assert r.success and r.data == {"has_db": True}


# ---------- invoker 的 plugin_ctx / host 嫁接 ----------


def _make_invoker(tmp_path, reg, host=None):
    return ToolInvoker(
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L4_CRITICAL)),
        log=AuditLog(tmp_path / "a.db"),
        host=host,
    )


def test_invoker_uses_plugin_ctx_and_grafts_host(tmp_path):
    rec = {}

    class Probe(Skill):
        id = "probe.host"

        def run(self, params, ctx):
            rec["ctx"] = ctx
            return ActionResult(success=True)

    skill = Probe()
    skill.plugin_ctx = SkillContext()  # 加载器构造的 ctx：host 为 None
    skill.plugin_capabilities = frozenset({"host"})
    reg = SkillRegistry()
    reg.register(skill, plugin="probe")
    sentinel = object()
    inv = _make_invoker(tmp_path, reg, host=sentinel)
    action = inv.propose(ToolCall(id="t", skill_id="probe.host", params={}))
    assert inv.execute(action, {}).success
    assert rec["ctx"] is skill.plugin_ctx      # 用的是插件 ctx，不是新建的
    assert rec["ctx"].host is sentinel          # 声明了 host capability → invoker 嫁接


def test_invoker_no_host_graft_without_capability(tmp_path):
    rec = {}

    class Probe(Skill):
        id = "probe.nohost"

        def run(self, params, ctx):
            rec["ctx"] = ctx
            return ActionResult(success=True)

    skill = Probe()
    skill.plugin_ctx = SkillContext()
    skill.plugin_capabilities = frozenset({"db"})  # 没声明 host
    reg = SkillRegistry()
    reg.register(skill, plugin="probe")
    inv = _make_invoker(tmp_path, reg, host=object())
    action = inv.propose(ToolCall(id="t", skill_id="probe.nohost", params={}))
    assert inv.execute(action, {}).success
    assert rec["ctx"].host is None  # 未声明 host capability → 不给
