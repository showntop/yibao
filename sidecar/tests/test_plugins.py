"""插件加载器 + capability 权限模型 + 命名空间强制（v2 方案 §3）。"""
from pathlib import Path

import pytest

from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, ScopedMemory, load_plugins
from yibao_brain.plugindb import PluginDb
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.tools import EchoTool, Tool, ToolContext, ToolRegistry


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


# ---------- emit_event 注入（插件后台线程主动播报通道）----------


def test_emit_event_injected_when_passed(data_dir, tmp_path):
    """load_plugins 传了 emit_event：插件 ctx.emit_event 即该通道（线程安全由底座包装保证）。"""
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = ToolRegistry()
    sent: list[dict] = []

    def emit(ev: dict) -> None:
        sent.append(ev)

    results = _load(tmp_path, reg, emit_event=emit)
    assert results == {"notes": "ok"}
    ctx = reg.get("notes.keep").plugin_ctx
    assert ctx.emit_event is emit
    ctx.emit_event({"kind": "reminder", "text": "任务完成"})
    assert sent == [{"kind": "reminder", "text": "任务完成"}]


def test_emit_event_defaults_to_none(data_dir, tmp_path):
    """load_plugins 不传 emit_event（兼容老调用方）：ctx.emit_event is None，插件应静默跳过。"""
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert reg.get("notes.keep").plugin_ctx.emit_event is None


def test_process_capability_accepted(data_dir, tmp_path):
    """process 是合法 capability（声明本插件会 spawn 子进程）；未知 capability 仍加载失败。"""
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST.replace(
        'capabilities = ["db"]', 'capabilities = ["db", "process"]'))
    _write_plugin(tmp_path, "badcap", NOTES_MANIFEST.replace(
        'id = "notes"', 'id = "badcap"').replace(
        'capabilities = ["db"]', 'capabilities = ["db", "teleport"]'))
    reg = ToolRegistry()
    results = _load(tmp_path, reg)
    assert results["notes"] == "ok"
    assert reg.get("notes.keep").plugin_capabilities == frozenset({"db", "process"})
    assert "teleport" in results["badcap"]  # 报错指明未知能力


def test_durable_capability_requires_and_injects_execution_engine(data_dir, tmp_path):
    manifest = NOTES_MANIFEST.replace(
        'capabilities = ["db"]', 'capabilities = ["db", "durable"]',
    )
    _write_plugin(tmp_path, "notes", manifest)
    missing = ToolRegistry()
    assert "DurableExecutionEngine" in _load(tmp_path, missing)["notes"]

    engine = object()
    reg = ToolRegistry()
    assert _load(tmp_path, reg, durable_engine=engine) == {"notes": "ok"}
    skill = reg.get("notes.keep")
    assert skill.plugin_capabilities == frozenset({"db", "durable"})
    assert skill.plugin_ctx.durable is engine


def test_blob_capability_injects_shared_content_store(data_dir, tmp_path):
    manifest = NOTES_MANIFEST.replace(
        'capabilities = ["db"]', 'capabilities = ["db", "blobs"]',
    )
    _write_plugin(tmp_path, "notes", manifest)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    ctx = reg.get("notes.keep").plugin_ctx
    staged = ctx.blobs.stage_text("portable artifact")
    ref = staged.finalize()
    assert ref.startswith("blob://sha256/")
    assert ctx.blobs.resolve(ref).read_text(encoding="utf-8") == "portable artifact"


# ---------- 声明式 db tool 端到端 ----------


def test_db_tool_end_to_end(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = ToolRegistry()
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


def test_plugin_db_business_write_and_work_outbox_share_one_transaction(tmp_path):
    db = PluginDb("atomic", str(tmp_path / "atomic.db"))
    db.apply_schema([{
        "name": "notes",
        "columns": [
            {"name": "id", "type": "text", "pk": True},
            {"name": "text", "type": "text"},
        ],
    }])
    try:
        with pytest.raises(RuntimeError, match="work_transaction"):
            db.enqueue_work_events("inv-outside", [{
                "event_type": "artifact.upsert", "payload": {"ref": "outside"},
            }])

        with db.work_transaction() as tx:
            db.insert("notes", {"id": "rolled-back", "text": "不应留下"})
            db.enqueue_work_events("inv-rollback", [{
                "event_type": "artifact.upsert", "payload": {"ref": "rolled-back"},
            }])
            tx.rollback()
        assert db.query("notes") == []
        assert db.work_outbox_events() == []

        with db.work_transaction():
            db.insert("notes", {"id": "committed", "text": "原子提交"})
            event_ids = db.enqueue_work_events("inv-commit", [{
                "event_type": "artifact.upsert", "payload": {"ref": "committed"},
            }])
        assert [row["id"] for row in db.query("notes")] == ["committed"]
        assert db.work_outbox_events() == [{
            "id": event_ids[0], "invocation_id": "inv-commit", "event_seq": 1,
            "event_type": "artifact.upsert", "status": "pending", "attempts": 0,
            "last_error": "",
        }]
    finally:
        db.close()


def test_db_tool_openai_schema_uses_manifest(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = ToolRegistry()
    _load(tmp_path, reg)
    schema = reg.get("notes.keep").openai_schema()
    assert schema["name"] == "notes.keep"
    assert schema["description"] == "记一条闪念"
    assert "text" in schema["parameters"]["properties"]


def test_plugin_registers_workflow_pack_and_declares_work_output(data_dir, tmp_path):
    manifest = NOTES_MANIFEST + """

[[workflow]]
id = "notes.research"
version = "1.0.0"
domain = "research"
label = "研究笔记"
matches = ["研究", "research"]
stages = [
  {id = "collect", label = "收集", artifact_patterns = ["note"]},
  {id = "deliver", label = "交付", artifact_patterns = ["report"]},
]
"""
    # work_output 属于 keep 这个 [[tool]]，插在其 [tool.db] 之后、下一个 [[tool]] 之前。
    manifest = manifest.replace(
        '[tool.db]\nop = "insert"\ntable = "notes"\n\n[[tool]]',
        '[tool.db]\nop = "insert"\ntable = "notes"\n'
        '[tool.work_output]\nkind = "artifact"\nartifact_type = "research.note"\n'
        'ref_from = "data.id"\nmetadata_fields = ["params.text"]\n\n[[tool]]',
        1,
    )
    _write_plugin(tmp_path, "notes", manifest)
    registered: list[tuple[dict, str]] = []

    def register(definition, *, source_plugin):
        registered.append((definition, source_plugin))

    reg = ToolRegistry()
    assert _load(tmp_path, reg, workflow_registrar=register) == {"notes": "ok"}
    assert registered[0][0]["id"] == "notes.research"
    assert registered[0][0]["source_plugin"] == "notes"
    assert registered[0][1] == "notes"
    assert reg.get("notes.keep").work_outputs[0]["artifact_type"] == "research.note"


def test_invalid_work_output_fails_plugin_at_load_time(data_dir, tmp_path):
    manifest = NOTES_MANIFEST.replace(
        '[tool.db]\nop = "insert"\ntable = "notes"',
        '[tool.db]\nop = "insert"\ntable = "notes"\n'
        '[tool.work_output]\nkind = "teleport"\nartifact_type = "note"\nref_from = "data.id"',
        1,
    )
    _write_plugin(tmp_path, "notes", manifest)
    result = _load(tmp_path, ToolRegistry())
    assert "未知 work_output kind" in result["notes"]


# ---------- capability 权限模型 ----------


def test_capability_scoping_unset_capabilities_are_none(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)  # 只声明 db
    reg = ToolRegistry()
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
    reg = ToolRegistry()
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
    reg = ToolRegistry()
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
    reg = ToolRegistry()
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
    reg = ToolRegistry()
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
    reg = ToolRegistry()
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
    reg = ToolRegistry()
    _load(tmp_path, reg)
    combo = reg.get("notes.combo")
    r = combo.run({}, combo.plugin_ctx)
    assert not r.success and "notes.ghost" in r.error
    assert reg.get("notes.keep").plugin_ctx.db.query("notes") == []  # 后续步未执行


# ---------- 命名空间强制 ----------


class _SomeTool(Tool):
    id = "s"
    description = "占位"

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
        return ActionResult(success=True)


def test_plugin_tool_without_prefix_rejected():
    reg = ToolRegistry()
    with pytest.raises(ValueError):
        reg.register(_SomeTool(), plugin="notes")  # id "s" 不带 "notes." 前缀


def test_plugin_tool_with_prefix_ok():
    reg = ToolRegistry()

    class P(Tool):
        id = "notes.keep"

        def run(self, params, ctx):
            return ActionResult(success=True)

    reg.register(P(), plugin="notes")
    assert reg.get("notes.keep").id == "notes.keep"


def test_duplicate_id_rejected():
    reg = ToolRegistry()
    reg.register(EchoTool())
    with pytest.raises(ValueError):
        reg.register(EchoTool())


def test_duplicate_plugin_tool_id_rejected():
    reg = ToolRegistry()

    class P(Tool):
        id = "notes.keep"

        def run(self, params, ctx):
            return ActionResult(success=True)

    reg.register(P(), plugin="notes")
    with pytest.raises(ValueError):
        reg.register(P(), plugin="notes")


def test_base_tool_id_with_dot_rejected():
    reg = ToolRegistry()

    class Evil(Tool):
        id = "notes.fake"  # 底座注册伪装成插件 id

        def run(self, params, ctx):
            return ActionResult(success=True)

    with pytest.raises(ValueError):
        reg.register(Evil())


# ---------- 失败隔离 / 目录扫描 ----------


def test_failure_isolation_bad_manifest(data_dir, tmp_path):
    _write_plugin(tmp_path, "broken", 'id = [unclosed')  # TOML 语法错误
    _write_plugin(tmp_path, "notes", NOTES_MANIFEST)
    reg = ToolRegistry()
    results = _load(tmp_path, reg)
    assert results["notes"] == "ok"
    assert results["broken"] != "ok" and results["broken"]  # 有错误信息
    assert reg.get("notes.keep").id == "notes.keep"


def test_skip_underscore_dirs(data_dir, tmp_path):
    _write_plugin(tmp_path, "_staging", NOTES_MANIFEST.replace('id = "notes"', 'id = "stg"'))
    reg = ToolRegistry()
    results = _load(tmp_path, reg)
    assert results == {}  # _staging 暂存区不加载也不上报
    assert reg.list() == []


def test_missing_plugins_dir_is_noop(tmp_path):
    assert _load(tmp_path / "nonexistent", ToolRegistry()) == {}


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
from yibao_brain.tools import Tool


class Hello(Tool):
    id = "coder.hello"
    description = "代码插件示例"

    def run(self, params, ctx):
        return ActionResult(success=True, data={"has_db": ctx.db is not None})


def make_tools(ctx):
    return [Hello()]
'''
    _write_plugin(tmp_path, "coder", manifest, {"tools/hello.py": hello_py})
    reg = ToolRegistry()
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

    class Probe(Tool):
        id = "probe.host"

        def run(self, params, ctx):
            rec["ctx"] = ctx
            return ActionResult(success=True)

    skill = Probe()
    skill.plugin_ctx = ToolContext()  # 加载器构造的 ctx：host 为 None
    skill.plugin_capabilities = frozenset({"host"})
    reg = ToolRegistry()
    reg.register(skill, plugin="probe")
    sentinel = object()
    inv = _make_invoker(tmp_path, reg, host=sentinel)
    action = inv.propose(ToolCall(id="t", tool_id="probe.host", params={}))
    assert inv.execute(action, {}).success
    assert rec["ctx"] is skill.plugin_ctx      # 用的是插件 ctx，不是新建的
    assert rec["ctx"].host is sentinel          # 声明了 host capability → invoker 嫁接


def test_invoker_no_host_graft_without_capability(tmp_path):
    rec = {}

    class Probe(Tool):
        id = "probe.nohost"

        def run(self, params, ctx):
            rec["ctx"] = ctx
            return ActionResult(success=True)

    skill = Probe()
    skill.plugin_ctx = ToolContext()
    skill.plugin_capabilities = frozenset({"db"})  # 没声明 host
    reg = ToolRegistry()
    reg.register(skill, plugin="probe")
    inv = _make_invoker(tmp_path, reg, host=object())
    action = inv.propose(ToolCall(id="t", tool_id="probe.nohost", params={}))
    assert inv.execute(action, {}).success
    assert rec["ctx"].host is None  # 未声明 host capability → 不给


# ---------- ⑤a：panel schema 注册 + DeclarativeTool panel 引用 ----------

NOTES_PANEL_MANIFEST = """
id = "notes"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "text", type = "text"},
  {name = "tags", type = "text", default = "[]"},
  {name = "created_at", type = "integer"},
]

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
panel = "notes:list"
required = ["text"]
[tool.params]
text = {type = "string", description = "内容"}
[tool.db]
op = "insert"
table = "notes"
auto = {created_at = "unixts"}

[[tool]]
id = "list"
type = "db"
description = "列出闪念"
risk = "L0"
panel = "notes:list"
[tool.db]
op = "query"
table = "notes"

[[tool]]
id = "delete"
type = "db"
description = "删除一条闪念"
risk = "L2"
panel = "notes:list"
[tool.params]
id = {type = "string", description = "闪念 id"}
[tool.db]
op = "delete"
table = "notes"

[[tool]]
id = "combo_fail"
type = "composite"
description = "必失败的编排"
panel = "notes:list"
[tool.composite]
steps = [{tool = "notes.ghost", params = {}}]

[[panel]]
type = "schema"
name = "list"
src = "panel/list.schema.json"
"""

LIST_SCHEMA = '{"type": "list", "bind": {"items": "$data.rows"}}'


def test_panel_schema_registered_and_tool_result_carries_ref(data_dir, tmp_path):
    from yibao_brain.plugins import get_panel

    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert get_panel("notes:list")["type"] == "list"
    assert get_panel("notes:list")["bind"] == {"items": "$data.rows"}
    keep = reg.get("notes.keep")
    r = keep.run({"text": "x"}, keep.plugin_ctx)
    assert r.success and r.panel == "notes:list"  # 成功才带 panel 引用


def test_panel_ref_not_set_on_failure(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    combo = reg.get("notes.combo_fail")
    r = combo.run({}, combo.plugin_ctx)
    assert not r.success and r.panel is None  # 失败不放 panel


def test_tool_required_params_in_schema(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    schema = reg.get("notes.keep").openai_schema()
    assert schema["parameters"]["required"] == ["text"]


def test_tool_with_panel_advertises_panel_opening(data_dir, tmp_path):
    """声明 panel 的 tool，LLM 可见描述尾部带「会打开面板」提示——模型本不知道面板存在，
    不告诉它，「打开看板」这类请求它只会用文字列数据、不调工具。"""
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    schema = reg.get("notes.list").openai_schema()
    assert schema["description"] == "列出闪念（调用成功会在屏幕面板窗打开「notes · list」）"
    # 未声明 panel 的 tool 描述保持原样（由 test_db_tool_openai_schema_uses_manifest 覆盖）


def test_webview_panel_loaded_as_html(data_dir, tmp_path):
    # 独立插件 id，避免与模块级 _PANELS 里其他测试注册的 notes:list 互相污染
    manifest = NOTES_PANEL_MANIFEST.replace('type = "schema"', 'type = "webview"').replace('id = "notes"', 'id = "webv"')
    manifest = manifest.replace("notes:", "webv:").replace('"notes.', '"webv.').replace('table = "notes"', 'table = "webv"')
    _write_plugin(tmp_path, "webv", manifest, {"panel/list.schema.json": "<html><body>hi</body></html>"})
    from yibao_brain.plugins import get_panel

    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"webv": "ok"}
    assert get_panel("webv:list")["type"] == "webview"
    assert get_panel("webv:list")["html"] == "<html><body>hi</body></html>"


def test_unknown_panel_type_skipped(data_dir, tmp_path, capsys):
    # 独立插件 id（holo），避免与模块级 _PANELS 里其他测试注册的 webv:list 互相污染
    manifest = NOTES_PANEL_MANIFEST.replace('type = "schema"', 'type = "hologram"').replace('id = "notes"', 'id = "holo"')
    manifest = manifest.replace("notes:", "holo:").replace('"notes.', '"holo.').replace('table = "notes"', 'table = "holo"')
    _write_plugin(tmp_path, "holo", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    from yibao_brain.plugins import get_panel

    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"holo": "ok"}  # panel 跳过不拖垮插件
    assert get_panel("holo:list") is None
    assert "跳过" in capsys.readouterr().err


def test_panel_missing_src_skipped(data_dir, tmp_path, capsys):
    manifest = NOTES_PANEL_MANIFEST.replace('id = "notes"', 'id = "nosrc"').replace("notes:", "nosrc:")
    _write_plugin(tmp_path, "nosrc", manifest)  # 不写 list.schema.json
    from yibao_brain.plugins import get_panel

    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"nosrc": "ok"}
    assert get_panel("nosrc:list") is None
    assert "跳过" in capsys.readouterr().err


# ---------- Phase 1 Task 2：manifest 面板声明 surfaces/min_width ----------


def test_panel_declares_supported_surfaces(data_dir, tmp_path):
    """manifest [[panel]] 的 surfaces/min_width 被解析进面板注册表（宿主裁决回落依据）。"""
    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\nsurfaces = ["stage", "focus"]\nmin_width = 720',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    from yibao_brain.plugins import get_panel

    panel = get_panel("notes:list")
    assert panel["surfaces"] == ["stage", "focus"]
    assert panel["min_width"] == 720


def test_panel_surfaces_peek_is_not_a_plugin_level(data_dir, tmp_path):
    """peek 是宿主对 Stage 的瞬态 compact placement（架构 §6.5），不进插件公共枚举：
    声明 peek 与声明非法值一样被静默过滤，只剩合法三态。"""
    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\nsurfaces = ["peek", "stage"]',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    from yibao_brain.plugins import get_panel

    assert get_panel("notes:list")["surfaces"] == ["stage"]


def test_panel_surfaces_peek_only_fails_closed(data_dir, tmp_path):
    """四档时代声明 surfaces = ["peek"] 的面板（旧语义：最高只允许 peek，是最强限制），
    过滤后为空时不能回落全档把限制放大成全许可——按最轻公共档 inline 收口（fail-closed）。
    纯非法值（笔误，如 hologram）才视为未声明、回落全档默认。"""
    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\nsurfaces = ["peek"]',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    from yibao_brain.plugins import get_panel

    assert get_panel("notes:list")["surfaces"] == ["inline"]


def test_panel_surfaces_default_all(data_dir, tmp_path):
    """未声明 → 默认全档支持（inline/stage/focus 三态），宿主裁决不误伤。"""
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    from yibao_brain.plugins import get_panel

    assert get_panel("notes:list")["surfaces"] == ["inline", "stage", "focus"]


def test_panel_surfaces_invalid_filtered(data_dir, tmp_path):
    """非法表面档位静默过滤（不报错不拖垮加载）；全非法 → 回落全档默认。"""
    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\nsurfaces = ["fullscreen", "stage", "bogus"]',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    from yibao_brain.plugins import get_panel

    assert get_panel("notes:list")["surfaces"] == ["stage"]

    # 全是非法值 → 等于未声明，默认全档
    manifest2 = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\nsurfaces = ["hologram"]',
    )
    _write_plugin(tmp_path, "notes2", manifest2.replace('id = "notes"', 'id = "notes2"').replace("notes:", "notes2:"), {"panel/list.schema.json": LIST_SCHEMA})
    reg2 = ToolRegistry()
    assert _load(tmp_path, reg2)["notes2"] == "ok"
    assert get_panel("notes2:list")["surfaces"] == ["inline", "stage", "focus"]


def test_panel_payload_webview_shape(data_dir, tmp_path):
    """webview 面板事件 payload：{panel, schema: None, webview: {html}, data}；schema 面板形状不变。"""
    from yibao_brain.plugins import panel_payload

    manifest = NOTES_PANEL_MANIFEST.replace('type = "schema"', 'type = "webview"').replace('id = "notes"', 'id = "webv"')
    manifest = manifest.replace("notes:", "webv:").replace('"notes.', '"webv.').replace('table = "notes"', 'table = "webv"')
    _write_plugin(tmp_path, "webv", manifest, {"panel/list.schema.json": "<html>wv</html>"})
    reg = ToolRegistry()
    _load(tmp_path, reg)

    r = ActionResult(success=True, data={"rows": [1]}, panel="webv:list")
    p = panel_payload(r)
    assert p["panel"] == "webv:list"
    assert p["title"] == "webv · list"
    assert p["schema"] is None
    assert p["webview"] == {"html": "<html>wv</html>"}
    assert p["data"] == {"rows": [1]}
    # 未声明 surfaces → 默认全档（三态）随 payload 透传（宿主裁决用）
    assert p["surfaces"] == ["inline", "stage", "focus"]
    r2 = ActionResult(success=True, data={"x": 1})  # 无 panel 引用 → None
    assert panel_payload(r2) is None


# ---------- 面板输入模式声明 [[panel]].input(panel-input-modes spec)----------


def test_panel_input_declared_passthrough(data_dir, tmp_path):
    """声明 input = "handoff" → 注册表保留且 panel_payload 载荷透传。"""
    from yibao_brain.plugins import get_panel, panel_payload

    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\ninput = "handoff"',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert get_panel("notes:list")["input"] == "handoff"
    p = panel_payload(ActionResult(success=True, data={"rows": []}, panel="notes:list"))
    assert p["input"] == "handoff"


def test_panel_input_absent_no_key(data_dir, tmp_path):
    """未声明 → 注册表与载荷都无 input 键(缺省 inherit 由前端语义兜,不发键)。"""
    from yibao_brain.plugins import get_panel, panel_payload

    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    assert "input" not in get_panel("notes:list")
    p = panel_payload(ActionResult(success=True, data={"rows": []}, panel="notes:list"))
    assert "input" not in p


def test_panel_input_invalid_warns_and_drops(data_dir, tmp_path, capsys):
    """非法值 → stderr 告警 + 按未声明处理(不回退出键,前端语义 inherit)。"""
    from yibao_brain.plugins import get_panel

    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\ninput = "takeover"',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert "input" not in get_panel("notes:list")
    assert "inherit" in capsys.readouterr().err


# ---------- db insert auto（系统生成字段）----------


def test_db_insert_auto_unixts(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    keep = reg.get("notes.keep")
    before = int(__import__("time").time())
    r = keep.run({"text": "带时间戳"}, keep.plugin_ctx)
    assert r.success
    row = keep.plugin_ctx.db.query("notes")[0]
    assert isinstance(row["created_at"], int) and before <= row["created_at"] <= before + 5
    assert row["tags"] == "[]"  # 列默认值生效


def test_db_insert_auto_unknown_kind_fails(data_dir, tmp_path):
    manifest = NOTES_PANEL_MANIFEST.replace('auto = {created_at = "unixts"}', 'auto = {created_at = "bogus"}')
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = ToolRegistry()
    _load(tmp_path, reg)
    keep = reg.get("notes.keep")
    r = keep.run({"text": "x"}, keep.plugin_ctx)
    assert not r.success and "auto" in r.error


# ---------- ⑦py：api.toml 解析 ----------

API_TOML = """
[[method]]
name = "delete"
handler = "notes.delete"
direct = true
risk = "L2"

[[method]]
name = "notes.list"
handler = "notes.list"
direct = true

[[method]]
name = "ghost"
handler = "notes.ghost"
direct = true

[[method]]
name = "badrisk"
handler = "notes.keep"
direct = true
risk = "L9"

[[method]]
name = "cross"
handler = "other.tool"
direct = true

[[method]]
name = "agent_thing"
handler = "notes.keep"
intent = "整理 {text}"

[[event]]
name = "notes.changed"
"""


def test_api_toml_parsed(data_dir, tmp_path, capsys):
    from yibao_brain.plugins import get_api, get_plugin_events

    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {
        "panel/list.schema.json": LIST_SCHEMA,
        "api.toml": API_TOML,
    })
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    api = get_api("notes.delete")
    assert api.handler == "notes.delete" and api.direct is True
    assert api.risk == RiskLevel.L2_MEDIUM
    assert get_api("notes.list") is not None          # name 已带前缀容错
    assert get_api("notes.ghost") is None             # handler 不存在 → 跳过
    assert get_api("notes.badrisk") is None           # risk 非法 → 跳过
    assert get_api("notes.cross") is None             # handler 跨插件 → 跳过
    agent_api = get_api("notes.agent_thing")
    assert agent_api.direct is False and agent_api.intent == "整理 {text}"
    assert get_plugin_events("notes") == ["notes.changed"]
    err = capsys.readouterr().err
    assert err.count("跳过") >= 3  # ghost/badrisk/cross 各一条


def test_api_toml_panel_field(data_dir, tmp_path, capsys):
    """api.toml [[method]] panel 字段：指向本插件已声明面板才受理；跨插件/未声明 → 跳过。"""
    from yibao_brain.plugins import get_api

    api_toml = """
[[method]]
name = "open_editor"
handler = "notes.list"
direct = true
panel = "notes:list"

[[method]]
name = "cross_panel"
handler = "notes.list"
direct = true
panel = "other:list"

[[method]]
name = "ghost_panel"
handler = "notes.list"
direct = true
panel = "notes:ghost"

[[method]]
name = "no_panel"
handler = "notes.list"
direct = true
"""
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {
        "panel/list.schema.json": LIST_SCHEMA,
        "api.toml": api_toml,
    })
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    assert get_api("notes.open_editor").panel == "notes:list"
    assert get_api("notes.cross_panel") is None   # 指向别的插件面板 → 跳过
    assert get_api("notes.ghost_panel") is None   # 面板未声明 → 跳过
    assert get_api("notes.no_panel").panel is None  # 缺省无覆盖
    err = capsys.readouterr().err
    assert err.count("跳过") >= 2


# ---------- ⑥：仓库里的真实闪念盘插件 ----------

REPO_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


def test_repo_notes_plugin_loads(data_dir, tmp_path):
    """仓库根 plugins/notes 必须能被 load_plugins 无错加载（⑥ 的验收）。"""
    from yibao_brain.durable_execution import DurableExecutionEngine
    from yibao_brain.plugins import get_api, get_panel
    from yibao_brain.work_graph import WorkGraphStore

    reg = ToolRegistry()
    results = _load(REPO_PLUGINS_DIR, reg, durable_engine=DurableExecutionEngine(
        WorkGraphStore(str(tmp_path / "wg.db"))))
    assert results["notes"] == "ok"
    assert results["zimeiti"] == "ok"  # 声明了 durable capability：引擎在就能全量加载
    for tid in ("notes.keep", "notes.list", "notes.delete"):
        reg.get(tid)
    assert isinstance(get_panel("notes:list"), dict)
    assert get_api("notes.delete").direct is True
    assert get_api("notes.list").direct is True

    keep = reg.get("notes.keep")
    r = keep.run({"text": "持久化验证"}, keep.plugin_ctx)
    assert r.success and r.panel == "notes:list"
    lst = reg.get("notes.list")
    rows = lst.run({}, lst.plugin_ctx).data["rows"]
    row = next(x for x in rows if x["text"] == "持久化验证")
    assert isinstance(row["created_at"], int) and row["created_at"] > 0
    assert row["tags"] == "[]"
    assert reg.get("notes.delete").default_risk == RiskLevel.L2_MEDIUM
    # list 的 manifest 默认 order="created_at DESC"（不传参也倒序）
    db = keep.plugin_ctx.db
    db.insert("notes", {"text": "旧", "created_at": 1})
    db.insert("notes", {"text": "新", "created_at": 2})
    texts = [x["text"] for x in lst.run({}, lst.plugin_ctx).data["rows"]]
    assert texts.index("新") < texts.index("旧")


# ---------- [[tool]] refresh：写操作后面板拿刷新数据 ----------

REFRESH_MANIFEST = """
id = "notes"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "text", type = "text"},
]
indexes = []

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
panel = "notes:list"
refresh = "list"
[tool.params]
text = {type = "string", description = "内容"}
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "list"
type = "db"
description = "列出闪念"
risk = "L0"
panel = "notes:list"
[tool.db]
op = "query"
table = "notes"
"""


class _SeqProvider:
    """第一次返回 first，之后都返回 second。"""

    def __init__(self, first, second):
        self._f, self._s, self._n = first, second, 0

    def chat(self, messages, tools=None):
        self._n += 1
        return self._f.chat(messages, tools) if self._n == 1 else self._s.chat(messages, tools)

    async def astream(self, messages, tools=None):
        self._n += 1
        src = self._f if self._n == 1 else self._s
        async for d in src.astream(messages, tools):
            yield d


def test_chat_write_tool_panel_carries_refresh_data(data_dir, tmp_path):
    """对话路径写操作：面板事件拿 refresh 查询数据而非回执 {"id":…}（否则面板显示「暂无数据」）。"""
    from yibao_brain.loop import AgentLoop

    _write_plugin(tmp_path, "notes", REFRESH_MANIFEST)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    provider = _SeqProvider(
        FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="notes_keep", params={"text": "牛奶"})]),
        FakeProvider(text="记下了"),
    )
    loop = AgentLoop(
        provider=provider, skills=reg, classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=FakeMemory(), log=AuditLog(tmp_path / "a.db"),
    )
    events = list(loop.run("记一下"))
    panels = [e for e in events if e.kind == "panel"]
    assert len(panels) == 1, "写操作应只出一个面板事件（refresh 的）"
    rows = panels[0].payload["data"].get("rows")
    assert rows is not None, "面板数据必须是查询结果（rows），不是回执 {id}"
    assert [r["text"] for r in rows] == ["牛奶"]


def test_refresh_cross_plugin_rejected(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", REFRESH_MANIFEST.replace('refresh = "list"', 'refresh = "other.list"'))
    results = _load(tmp_path, ToolRegistry())
    assert results["notes"].startswith("ValueError") and "本插件" in results["notes"]


def test_refresh_unregistered_tool_rejected(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", REFRESH_MANIFEST.replace('refresh = "list"', 'refresh = "ghost"'))
    results = _load(tmp_path, ToolRegistry())
    assert results["notes"].startswith("ValueError") and "未注册" in results["notes"]


SURFACE_MANIFEST = """
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

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
presentation = "inline"
attention = "quiet"
[tool.params]
text = {type = "string", description = "内容"}
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "bad"
type = "db"
description = "非法声明"
risk = "L1"
presentation = "gigantic"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "peeky"
type = "db"
description = "声明 peek 档位"
risk = "L1"
presentation = "peek"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "silent"
type = "db"
description = "不声明"
risk = "L1"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "boom"
type = "composite"
description = "会失败的编排"
risk = "L1"
presentation = "inline"
[tool.composite]
steps = [{tool = "notes.ghost", params = {}}]
"""


def test_declarative_tool_carries_surface_hints(data_dir, tmp_path):
    """声明式 tool 的 presentation/attention 必须带进 ActionResult。

    notes/reminders 这批最该走 Inline 的插件全是声明式的；Phase 1 只给
    ActionResult 加了字段，等于把它们排除在表面模型之外。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = ToolRegistry()
    _load(tmp_path, reg)

    keep = reg.get("notes.keep")
    r = keep.run({"text": "买点牛奶", "created_at": 1}, keep.plugin_ctx)
    assert r.success
    assert r.presentation == "inline"
    assert r.attention == "quiet"


def test_declarative_tool_invalid_presentation_ignored(data_dir, tmp_path):
    """非法值静默过滤（与 [[panel]].surfaces 既有约定一致），不抛错、不阻断加载。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    bad = reg.get("notes.bad")
    r = bad.run({"text": "x", "created_at": 1}, bad.plugin_ctx)
    assert r.success
    assert r.presentation is None


def test_declarative_tool_peek_presentation_filtered(data_dir, tmp_path):
    """presentation = "peek" 同样被过滤：peek 是宿主对 Stage 的瞬态 compact
    placement（架构 §6.5），插件公共枚举只有 inline/stage/focus 三态。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    peeky = reg.get("notes.peeky")
    r = peeky.run({"text": "x", "created_at": 1}, peeky.plugin_ctx)
    assert r.success
    assert r.presentation is None


def test_declarative_tool_silent_defaults(data_dir, tmp_path):
    """不声明 → presentation=None、attention 保持 ActionResult 默认 "suggest"。

    这条锁住向后兼容：旧插件行为完全不变。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = ToolRegistry()
    _load(tmp_path, reg)

    silent = reg.get("notes.silent")
    r = silent.run({"text": "x", "created_at": 1}, silent.plugin_ctx)
    assert r.success
    assert r.presentation is None
    assert r.attention == "suggest"


def test_declarative_tool_failure_carries_no_hints(data_dir, tmp_path):
    """失败结果不带表面建议——失败不该建议展开面板。

    与既有「失败不放 panel 引用」共用同一个 if result.success 判断。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = ToolRegistry()
    _load(tmp_path, reg)

    boom = reg.get("notes.boom")
    r = boom.run({"id": "x"}, boom.plugin_ctx)
    assert not r.success
    assert r.presentation is None



EXPLICIT_MANIFEST = """
id = "notes"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "created_at", type = "integer"},
]

[[tool]]
id = "list"
type = "db"
description = "列出闪念"
risk = "L0"
explicit = true
[tool.db]
op = "query"
table = "notes"

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
[tool.db]
op = "insert"
table = "notes"
"""


def test_declarative_tool_explicit_declaration(data_dir, tmp_path):
    """[[tool]] explicit = true → 成功后 result.explicit（对话点名 → 宿主可越过 AUTO_MAX 弹面板）。

    纯声明式插件（zimeiti/forge/agents/notes）此前拿不到 explicit——只有 fun 代码工具
    能手动置位；本测试锁定声明式补齐。"""
    _write_plugin(tmp_path, "notes", EXPLICIT_MANIFEST)
    reg = ToolRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    list_tool = reg.get("notes.list")
    r = list_tool.run({}, list_tool.plugin_ctx)
    assert r.success and r.explicit is True

    keep = reg.get("notes.keep")
    r2 = keep.run({"created_at": 1}, keep.plugin_ctx)
    assert r2.success and r2.explicit is False  # 未声明保持默认
