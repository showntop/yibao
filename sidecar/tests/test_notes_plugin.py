"""notes 插件（闪念盘，全声明式零代码）端到端测试：加载真实 plugins/notes/（数据目录重定向到 tmp）。

声明式 db tool 的执行机制（insert/query/delete 分发、capability 隔离）由底座
test_plugins.py 用合成 manifest 覆盖；本文件只补真实 manifest 的缺口：
keep/list/delete 全链 + auto unixts + 倒序 limit + db 表结构按 manifest 建好
+ api.toml 白名单 + 面板 schema 与白名单的一致性。
"""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, get_api, load_plugins
from yibao_brain.skills import SkillRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = REPO_ROOT / "plugins" / "notes"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
    """加载真实插件目录；返回 (registry, 加载结果)。"""
    reg = SkillRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg, results


def _run(reg, tid, params):
    t = reg.get(tid)
    assert t is not None, f"tool 未注册: {tid}"
    return t.run(params, t.plugin_ctx)


# ---------- 加载与注册 ----------


def test_notes_loads_ok(env):
    _, results = env
    assert results["notes"] == "ok"


def test_tools_registered_with_risks_and_refresh(env):
    reg, _ = env
    from yibao_brain.ipc import RiskLevel

    expected = {
        "notes.keep": RiskLevel.L1_LOW,
        "notes.list": RiskLevel.L0_READONLY,
        "notes.delete": RiskLevel.L2_MEDIUM,
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid
    # manifest 里 refresh = "list" 短名自动补插件前缀
    assert reg.get("notes.keep").refresh == "notes.list"
    assert reg.get("notes.delete").refresh == "notes.list"
    assert reg.get("notes.list").refresh is None


# ---------- db 表结构按 manifest 建好 ----------


def test_table_structure_matches_manifest(env, data_dir):
    reg, _ = env
    _run(reg, "notes.keep", {"text": "触发生成 data.db"})
    db_file = data_dir / "plugins" / "notes" / "data.db"
    assert db_file.is_file()
    conn = sqlite3.connect(db_file)
    try:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(notes)")}
        assert set(cols) == {"id", "text", "tags", "created_at"}
        assert cols["id"][5] == 1  # pk
        assert cols["tags"][4] == "'[]'"  # manifest 声明的默认值（sqlite 存的是带引号的文本字面量）
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(notes)")}
        idx_cols = {
            col_row[2]
            for idx in indexes
            for col_row in conn.execute(f"PRAGMA index_info({idx})")
        }
        assert "created_at" in idx_cols  # manifest 声明的索引
    finally:
        conn.close()


# ---------- keep → list → delete 全链 ----------


def test_keep_list_delete_chain(env):
    reg, _ = env
    before = int(time.time())
    r = _run(reg, "notes.keep", {"text": "第一句闪念"})
    assert r.success and r.data["id"]
    assert r.panel == "notes:list"  # manifest 声明的面板引用
    rid = r.data["id"]

    rows = _run(reg, "notes.list", {}).data["rows"]
    assert [row["id"] for row in rows] == [rid]
    row = rows[0]
    assert row["text"] == "第一句闪念"
    assert row["tags"] == "[]"  # 未传 tags 走 manifest 默认值
    assert before <= row["created_at"] <= int(time.time())  # auto unixts 系统生成

    r = _run(reg, "notes.delete", {"id": rid})
    assert r.success and r.panel == "notes:list"
    assert _run(reg, "notes.list", {}).data["rows"] == []


def test_keep_auto_unixts_overrides_forged_input(env):
    """auto = unixts 覆盖入参：调用方伪造 created_at 不生效（防伪造系统字段）。"""
    reg, _ = env
    r = _run(reg, "notes.keep", {"text": "x", "created_at": 1})
    assert r.success
    row = _run(reg, "notes.list", {}).data["rows"][0]
    assert row["created_at"] > 1_000_000_000


def test_list_order_desc_and_limit_50(env):
    reg, _ = env
    for i in range(55):
        assert _run(reg, "notes.keep", {"text": f"n{i:02d}"}).success
    rows = _run(reg, "notes.list", {}).data["rows"]
    assert len(rows) == 50  # manifest limit = 50
    times = [row["created_at"] for row in rows]
    assert times == sorted(times, reverse=True)  # order = created_at DESC


def test_delete_unknown_id_is_silent_success(env):
    """delete 不校验存在性（声明式语义）：删不存在的 id 也 success，但不误删其他行。"""
    reg, _ = env
    rid = _run(reg, "notes.keep", {"text": "留着"}).data["id"]
    assert _run(reg, "notes.delete", {"id": "不存在"}).success
    assert [row["id"] for row in _run(reg, "notes.list", {}).data["rows"]] == [rid]


# ---------- api.toml 白名单 + 面板 schema 一致性 ----------


def test_api_whitelist(env):
    _ = env
    lst = get_api("notes.list")
    assert lst is not None and lst.direct
    delete = get_api("notes.delete")
    assert delete is not None and delete.direct and delete.refresh == "notes.list"


def test_panel_schemas_reference_whitelisted_methods(env):
    """面板 schema 里引用的 method 必须都在 api.toml 白名单（防手滑）。"""
    _ = env  # 先加载插件，get_api 注册表才有内容
    for schema_file in (NOTES_DIR / "panel").glob("*.schema.json"):
        doc = json.loads(schema_file.read_text(encoding="utf-8"))
        actions = list(doc.get("actions") or [])
        actions += (doc.get("item") or {}).get("actions") or []
        if doc.get("submit"):
            actions.append(doc["submit"])
        for a in actions:
            assert get_api(a["method"]) is not None, f"{schema_file.name}: {a['method']} 不在白名单"
