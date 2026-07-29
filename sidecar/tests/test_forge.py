"""forge 插件（需求磨刀）端到端测试：加载真实 plugins/forge/（数据目录重定向到 tmp）。

覆盖：声明式 CRUD 全链 + 代码 tool（guide/doc_save/verdict/verdict_form/proto_gen）
+ api.toml 白名单 + 面板 schema 与 api 方法的一致性。
"""
import json
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, get_api, load_plugins
from yibao_brain.skills import SkillRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
FORGE_DIR = REPO_ROOT / "plugins" / "forge"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
    """加载真实 forge 插件；返回 (registry, FakeMemory, 加载结果)。"""
    reg = SkillRegistry()
    mem = FakeMemory()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=mem, http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg, mem, results


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


# ---------- 加载 ----------


def test_forge_loads_ok(env):
    _, _, results = env
    assert results["forge"] == "ok"


def test_all_tools_registered_with_risks(env):
    reg, _, _ = env
    from yibao_brain.ipc import RiskLevel

    expected = {
        "forge.add": RiskLevel.L1_LOW,
        "forge.list": RiskLevel.L0_READONLY,
        "forge.get": RiskLevel.L0_READONLY,
        "forge.triage": RiskLevel.L1_LOW,
        "forge.delete": RiskLevel.L2_MEDIUM,
        "forge.guide": RiskLevel.L0_READONLY,
        "forge.doc_save": RiskLevel.L2_MEDIUM,
        "forge.verdict": RiskLevel.L2_MEDIUM,
        "forge.verdict_form": RiskLevel.L0_READONLY,
        "forge.proto_gen": RiskLevel.L2_MEDIUM,
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid


# ---------- 声明式全链：add → list → get → triage → delete ----------


def test_declarative_chain(env):
    reg, _, _ = env
    r = _run(reg, "forge.add", {"title": "桌面宠物", "pain": "一个人写代码太孤独", "who": "独立开发者"})
    assert r.success and r.data["id"]
    rid = r.data["id"]

    rows = _run(reg, "forge.list", {}).data["rows"]
    assert [row["id"] for row in rows] == [rid]
    assert rows[0]["status"] == "灵感" and rows[0]["created_at"] > 0

    # 面板 action 扁平传 {id: …} 的快捷映射（query id shorthand）
    got = _run(reg, "forge.get", {"id": rid}).data["rows"]
    assert len(got) == 1 and got[0]["title"] == "桌面宠物"

    # triage 存快筛结论 + 推进状态；update auto(unixts) 自动刷 updated_at
    before = got[0]["updated_at"]
    r2 = _run(reg, "forge.triage", {"id": rid, "triage": "快筛卡：真痛点", "status": "快筛过"})
    assert r2.success
    after = _run(reg, "forge.get", {"id": rid}).data["rows"][0]
    assert after["status"] == "快筛过" and after["triage"] == "快筛卡：真痛点"
    assert after["updated_at"] >= before

    assert _run(reg, "forge.delete", {"id": rid}).success
    assert _run(reg, "forge.list", {}).data["rows"] == []


# ---------- guide ----------


def test_guide_loads_methodology(env):
    reg, _, _ = env
    r = _run(reg, "forge.guide", {"name": "triage"})
    assert r.success and "快筛" in r.data["text"]
    for name in ("challenge", "scan", "prd"):
        assert _run(reg, "forge.guide", {"name": name}).success, name


def test_guide_rejects_unknown_and_traversal(env):
    reg, _, _ = env
    for bad in ("nope", "../manifest", "../../etc/passwd"):
        assert not _run(reg, "forge.guide", {"name": bad}).success, bad


# ---------- doc_save ----------


def test_doc_save_challenge_writes_file_and_advances_status(env, data_dir):
    reg, _, _ = env
    rid = _run(reg, "forge.add", {"title": "T", "pain": "P"}).data["id"]
    r = _run(reg, "forge.doc_save", {"id": rid, "kind": "challenge", "content": "# 挑战记录\n问答…"})
    assert r.success
    path = Path(r.data["path"])
    assert path.is_file() and path.read_text(encoding="utf-8").startswith("# 挑战记录")
    assert data_dir in path.parents  # 落在插件数据目录，不污染仓库
    row = _run(reg, "forge.get", {"id": rid}).data["rows"][0]
    assert row["status"] == "挑战中" and row["challenge_path"] == str(path)
    assert r.panel == "forge:detail" and reg.get("forge.doc_save").refresh == "forge.get"


def test_doc_save_rejects_bad_kind_and_missing_id(env):
    reg, _, _ = env
    rid = _run(reg, "forge.add", {"title": "T", "pain": "P"}).data["id"]
    assert not _run(reg, "forge.doc_save", {"id": rid, "kind": "evil", "content": "x"}).success
    assert not _run(reg, "forge.doc_save", {"id": "missing", "kind": "prd", "content": "x"}).success


# ---------- verdict（裁决档案） ----------


def test_verdict_updates_row(env):
    reg, _mem, _ = env
    rid = _run(reg, "forge.add", {"title": "日程助手", "pain": "老忘事"}).data["id"]
    assert not _run(reg, "forge.verdict", {"id": rid, "verdict": "随便", "reason": "r"}).success

    r = _run(reg, "forge.verdict", {"id": rid, "verdict": "已否决", "reason": "巨头标配，没差异点"})
    assert r.success and r.panel == "forge:board"
    row = _run(reg, "forge.get", {"id": rid}).data["rows"][0]
    assert row["status"] == "已否决" and row["verdict_reason"] == "巨头标配，没差异点"
    assert row["decided_at"] > 0


def test_verdict_form_returns_row(env):
    reg, _, _ = env
    rid = _run(reg, "forge.add", {"title": "T", "pain": "P"}).data["id"]
    r = _run(reg, "forge.verdict_form", {"id": rid})
    assert r.success and r.data["id"] == rid and r.panel == "forge:verdict_form"
    assert not _run(reg, "forge.verdict_form", {"id": "missing"}).success


# ---------- proto_gen ----------


def test_proto_gen_writes_html_and_opens(env, data_dir, monkeypatch):
    reg, _, _ = env
    rid = _run(reg, "forge.add", {"title": "T", "pain": "P"}).data["id"]
    opened = []
    monkeypatch.setattr(reg.get("forge.proto_gen"), "_opener", opened.append)
    r = _run(reg, "forge.proto_gen", {"id": rid, "html": "<html>demo</html>"})
    assert r.success
    path = Path(r.data["path"])
    assert path.is_file() and path.read_text(encoding="utf-8") == "<html>demo</html>"
    assert opened == [str(path)]  # 浏览器预览被拉起
    row = _run(reg, "forge.get", {"id": rid}).data["rows"][0]
    assert row["proto_path"] == str(path)


def test_proto_gen_rejects_missing_id(env):
    reg, _, _ = env
    assert not _run(reg, "forge.proto_gen", {"id": "missing", "html": "<html/>"}).success


# ---------- api.toml 白名单 + 面板 schema 一致性 ----------


def test_api_whitelist(env):
    _ = env
    for name in ("forge.list", "forge.get", "forge.verdict_form", "forge.delete", "forge.verdict"):
        api = get_api(name)
        assert api is not None and api.direct, name
    for name in ("forge.challenge", "forge.scan", "forge.prd", "forge.proto"):
        api = get_api(name)
        assert api is not None and not api.direct and api.intent, name
    assert get_api("forge.verdict").refresh == "forge.list"


def test_panel_schemas_reference_whitelisted_methods(env):
    """面板 schema 里引用的 method 必须都在 api.toml 白名单（防手滑）。"""
    _ = env  # 先加载插件，get_api 注册表才有内容
    for schema_file in (FORGE_DIR / "panel").glob("*.schema.json"):
        doc = json.loads(schema_file.read_text(encoding="utf-8"))
        actions = []
        if doc.get("type") == "board":
            actions += (doc.get("card") or {}).get("actions") or []
        actions += doc.get("actions") or []
        if doc.get("submit"):
            actions.append(doc["submit"])
        for extra in (doc.get("drag"), doc.get("quick_add"), doc.get("back")):  # 拖拽/快捷新增/返回导航同样走白名单
            if extra:
                actions.append(extra)
        assert actions, f"{schema_file.name} 没有 action"
        for a in actions:
            assert get_api(a["method"]) is not None, f"{schema_file.name}: {a['method']} 不在白名单"


# ---------- doc_read（面板内读挑战/PRD 文档） ----------


def _add_req(reg):
    return _run(reg, "forge.add", {"title": "T", "pain": "P"}).data["id"]


def test_doc_read_happy_after_doc_save(env):
    reg, _, _ = env
    rid = _add_req(reg)
    _run(reg, "forge.doc_save", {"id": rid, "kind": "challenge", "content": "# 挑战记录\n问答正文"})
    r = _run(reg, "forge.doc_read", {"id": rid, "kind": "challenge"})
    assert r.success and r.panel == "forge:doc"
    assert r.data["id"] == rid and "挑战文档" in r.data["title"]
    assert "问答正文" in r.data["text"]


def test_doc_read_missing_doc_errors(env):
    reg, _, _ = env
    rid = _add_req(reg)
    r = _run(reg, "forge.doc_read", {"id": rid, "kind": "prd"})
    assert not r.success and "还没有" in r.error


def test_doc_read_rejects_bad_input(env):
    reg, _, _ = env
    rid = _add_req(reg)
    assert not _run(reg, "forge.doc_read", {"id": rid, "kind": "火星"}).success
    assert not _run(reg, "forge.doc_read", {"id": "不存在", "kind": "prd"}).success


def test_doc_read_proto_points_to_browser(env, data_dir):
    reg, _, _ = env
    rid = _add_req(reg)
    proto = data_dir / "plugins" / "forge" / "docs" / f"{rid}-proto.html"
    proto.parent.mkdir(parents=True, exist_ok=True)
    proto.write_text("<html></html>", encoding="utf-8")
    t = reg.get("forge.doc_read")
    t.plugin_ctx.db.update("requirements", rid, {"proto_path": str(proto)})
    r = t.run({"id": rid, "kind": "proto"}, t.plugin_ctx)
    assert not r.success and str(proto) in r.error  # HTML 不渲染源码，指路浏览器


def test_doc_read_whitelisted(env):
    env
    api = get_api("forge.doc_read")
    assert api is not None and api.direct
