"""zimeiti 插件（自媒体：选题+写作）端到端测试：加载真实 plugins/zimeiti/（数据目录重定向到 tmp）。

覆盖：声明式 CRUD/状态流转全链 + 代码 tool（guide/article_save/article_read 版本管理）
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
ZIMEITI_DIR = REPO_ROOT / "plugins" / "zimeiti"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
    """加载真实插件目录；返回 (registry, FakeMemory, 加载结果)。"""
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


def test_zimeiti_loads_ok(env):
    _, _, results = env
    assert results["zimeiti"] == "ok"


def test_all_tools_registered_with_risks(env):
    reg, _, _ = env
    from yibao_brain.ipc import RiskLevel

    expected = {
        "zimeiti.add": RiskLevel.L1_LOW,
        "zimeiti.list": RiskLevel.L0_READONLY,
        "zimeiti.get": RiskLevel.L0_READONLY,
        "zimeiti.move": RiskLevel.L1_LOW,
        "zimeiti.delete": RiskLevel.L2_MEDIUM,
        "zimeiti.guide": RiskLevel.L0_READONLY,
        "zimeiti.article_save": RiskLevel.L2_MEDIUM,
        "zimeiti.article_read": RiskLevel.L0_READONLY,
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid


# ---------- 声明式全链：add → list → get → move → delete ----------


def test_declarative_chain(env):
    reg, _, _ = env
    r = _run(reg, "zimeiti.add", {"title": "AI 桌宠的一天", "angle": "vlog 式记录", "platform": "小红书"})
    assert r.success and r.data["id"]
    tid = r.data["id"]

    rows = _run(reg, "zimeiti.list", {}).data["rows"]
    assert [row["id"] for row in rows] == [tid]
    assert rows[0]["status"] == "候选" and rows[0]["created_at"] > 0

    got = _run(reg, "zimeiti.get", {"id": tid}).data["rows"]
    assert len(got) == 1 and got[0]["title"] == "AI 桌宠的一天"

    before = got[0]["updated_at"]
    assert _run(reg, "zimeiti.move", {"id": tid, "status": "待发布"}).success
    after = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert after["status"] == "待发布" and after["updated_at"] >= before

    assert _run(reg, "zimeiti.delete", {"id": tid}).success
    assert _run(reg, "zimeiti.list", {}).data["rows"] == []


# ---------- guide ----------


def test_guide_loads_methodology(env):
    reg, _, _ = env
    r = _run(reg, "zimeiti.guide", {"name": "write"})
    assert r.success and "钩子" in r.data["text"]


def test_guide_rejects_unknown_and_traversal(env):
    reg, _, _ = env
    for bad in ("nope", "../manifest", "../../etc/passwd"):
        assert not _run(reg, "zimeiti.guide", {"name": bad}).success, bad


# ---------- article_save / article_read（版本管理） ----------


def test_article_save_versions_and_status_flow(env, data_dir):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]

    r1 = _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 初稿", "note": "初稿"})
    assert r1.success and r1.data["version"] == 1 and r1.panel == "zimeiti:detail"
    path1 = Path(r1.data["path"])
    assert path1.is_file() and path1.name == "v1.md"
    assert data_dir in path1.parents  # 落在插件数据目录，不污染仓库
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "写作中"  # 有稿即进入写作中

    r2 = _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 二稿", "note": "改了开头"})
    assert r2.success and r2.data["version"] == 2
    assert Path(r2.data["path"]).read_text(encoding="utf-8") == "# 二稿"

    # 已流转的状态不被 save 回退
    _run(reg, "zimeiti.move", {"id": tid, "status": "待发布"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 三稿"})
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "待发布"


def test_article_save_rejects_missing_topic(env):
    reg, _, _ = env
    assert not _run(reg, "zimeiti.article_save", {"id": "missing", "content": "x"}).success
    assert not _run(reg, "zimeiti.article_save", {"id": "", "content": "x"}).success


def test_article_read_latest_and_specific_version(env):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    assert not _run(reg, "zimeiti.article_read", {"id": tid}).success  # 无稿时报错

    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v1"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v2", "note": "二稿"})

    r = _run(reg, "zimeiti.article_read", {"id": tid})
    assert r.success and r.data["version"] == 2 and r.data["content"] == "# v2"
    assert r.data["note"] == "二稿"

    r1 = _run(reg, "zimeiti.article_read", {"id": tid, "version": 1})
    assert r1.success and r1.data["content"] == "# v1"

    assert not _run(reg, "zimeiti.article_read", {"id": tid, "version": 9}).success
    assert not _run(reg, "zimeiti.article_read", {"id": tid, "version": "abc"}).success


# ---------- api.toml 白名单 + 面板 schema 一致性 ----------


def test_api_whitelist(env):
    _ = env
    for name in ("zimeiti.list", "zimeiti.get", "zimeiti.move", "zimeiti.delete"):
        api = get_api(name)
        assert api is not None and api.direct, name
    for name in ("zimeiti.draft", "zimeiti.revise", "zimeiti.read"):
        api = get_api(name)
        assert api is not None and not api.direct and api.intent, name
    assert get_api("zimeiti.move").refresh == "zimeiti.list"
    assert get_api("zimeiti.delete").refresh == "zimeiti.list"


def test_panel_schemas_reference_whitelisted_methods(env):
    """面板 schema 里引用的 method 必须都在 api.toml 白名单（防手滑）。"""
    _ = env  # 先加载插件，get_api 注册表才有内容
    for schema_file in (ZIMEITI_DIR / "panel").glob("*.schema.json"):
        doc = json.loads(schema_file.read_text(encoding="utf-8"))
        actions = []
        if doc.get("type") == "board":
            actions += (doc.get("card") or {}).get("actions") or []
        actions += doc.get("actions") or []
        if doc.get("submit"):
            actions.append(doc["submit"])
        assert actions, f"{schema_file.name} 没有 action"
        for a in actions:
            assert get_api(a["method"]) is not None, f"{schema_file.name}: {a['method']} 不在白名单"
