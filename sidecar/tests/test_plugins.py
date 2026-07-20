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
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert get_panel("notes:list") == {"type": "list", "bind": {"items": "$data.rows"}}
    keep = reg.get("notes.keep")
    r = keep.run({"text": "x"}, keep.plugin_ctx)
    assert r.success and r.panel == "notes:list"  # 成功才带 panel 引用


def test_panel_ref_not_set_on_failure(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
    _load(tmp_path, reg)
    combo = reg.get("notes.combo_fail")
    r = combo.run({}, combo.plugin_ctx)
    assert not r.success and r.panel is None  # 失败不放 panel


def test_tool_required_params_in_schema(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
    _load(tmp_path, reg)
    schema = reg.get("notes.keep").openai_schema()
    assert schema["parameters"]["required"] == ["text"]


def test_tool_with_panel_advertises_panel_opening(data_dir, tmp_path):
    """声明 panel 的 tool，LLM 可见描述尾部带「会打开面板」提示——模型本不知道面板存在，
    不告诉它，「打开看板」这类请求它只会用文字列数据、不调工具。"""
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
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

    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"webv": "ok"}
    assert get_panel("webv:list") == {"type": "webview", "html": "<html><body>hi</body></html>"}


def test_unknown_panel_type_skipped(data_dir, tmp_path, capsys):
    # 独立插件 id（holo），避免与模块级 _PANELS 里其他测试注册的 webv:list 互相污染
    manifest = NOTES_PANEL_MANIFEST.replace('type = "schema"', 'type = "hologram"').replace('id = "notes"', 'id = "holo"')
    manifest = manifest.replace("notes:", "holo:").replace('"notes.', '"holo.').replace('table = "notes"', 'table = "holo"')
    _write_plugin(tmp_path, "holo", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    from yibao_brain.plugins import get_panel

    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"holo": "ok"}  # panel 跳过不拖垮插件
    assert get_panel("holo:list") is None
    assert "跳过" in capsys.readouterr().err


def test_panel_missing_src_skipped(data_dir, tmp_path, capsys):
    manifest = NOTES_PANEL_MANIFEST.replace('id = "notes"', 'id = "nosrc"').replace("notes:", "nosrc:")
    _write_plugin(tmp_path, "nosrc", manifest)  # 不写 list.schema.json
    from yibao_brain.plugins import get_panel

    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"nosrc": "ok"}
    assert get_panel("nosrc:list") is None
    assert "跳过" in capsys.readouterr().err


def test_panel_payload_webview_shape(data_dir, tmp_path):
    """webview 面板事件 payload：{panel, schema: None, webview: {html}, data}；schema 面板形状不变。"""
    from yibao_brain.plugins import panel_payload

    manifest = NOTES_PANEL_MANIFEST.replace('type = "schema"', 'type = "webview"').replace('id = "notes"', 'id = "webv"')
    manifest = manifest.replace("notes:", "webv:").replace('"notes.', '"webv.').replace('table = "notes"', 'table = "webv"')
    _write_plugin(tmp_path, "webv", manifest, {"panel/list.schema.json": "<html>wv</html>"})
    reg = SkillRegistry()
    _load(tmp_path, reg)

    r = ActionResult(success=True, data={"rows": [1]}, panel="webv:list")
    assert panel_payload(r) == {
        "panel": "webv:list",
        "title": "webv · list",
        "schema": None,
        "webview": {"html": "<html>wv</html>"},
        "data": {"rows": [1]},
    }
    r2 = ActionResult(success=True, data={"x": 1})  # 无 panel 引用 → None
    assert panel_payload(r2) is None


# ---------- db insert auto（系统生成字段）----------


def test_db_insert_auto_unixts(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
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
    reg = SkillRegistry()
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
    reg = SkillRegistry()
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
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    assert get_api("notes.open_editor").panel == "notes:list"
    assert get_api("notes.cross_panel") is None   # 指向别的插件面板 → 跳过
    assert get_api("notes.ghost_panel") is None   # 面板未声明 → 跳过
    assert get_api("notes.no_panel").panel is None  # 缺省无覆盖
    err = capsys.readouterr().err
    assert err.count("跳过") >= 2


# ---------- ⑥：仓库里的真实闪念盘插件 ----------

REPO_PLUGINS_DIR = Path(__file__).resolve().parents[2] / "plugins"


def test_repo_notes_plugin_loads(data_dir):
    """仓库根 plugins/notes 必须能被 load_plugins 无错加载（⑥ 的验收）。"""
    from yibao_brain.plugins import get_api, get_panel

    reg = SkillRegistry()
    results = _load(REPO_PLUGINS_DIR, reg)
    assert results["notes"] == "ok"
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


def test_chat_write_tool_panel_carries_refresh_data(data_dir, tmp_path):
    """对话路径写操作：面板事件拿 refresh 查询数据而非回执 {"id":…}（否则面板显示「暂无数据」）。"""
    from yibao_brain.loop import AgentLoop

    _write_plugin(tmp_path, "notes", REFRESH_MANIFEST)
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    provider = _SeqProvider(
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="notes_keep", params={"text": "牛奶"})]),
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
    results = _load(tmp_path, SkillRegistry())
    assert results["notes"].startswith("ValueError") and "本插件" in results["notes"]


def test_refresh_unregistered_tool_rejected(data_dir, tmp_path):
    _write_plugin(tmp_path, "notes", REFRESH_MANIFEST.replace('refresh = "list"', 'refresh = "ghost"'))
    results = _load(tmp_path, SkillRegistry())
    assert results["notes"].startswith("ValueError") and "未注册" in results["notes"]
