"""PluginDb：插件专属 SQLite（v2 方案 §3.2 ctx.db）。

每插件一个 data.db，单连接 + 锁序列化；apply_schema 只做 additive 迁移。
"""
import pytest

from yibao_brain.plugindb import PluginDb


def _notes_table(extra_cols=None, indexes=None):
    cols = [
        {"name": "id", "type": "text", "pk": True},
        {"name": "text", "type": "text"},
        {"name": "created_at", "type": "integer"},
    ]
    cols.extend(extra_cols or [])
    t = {"name": "notes", "columns": cols}
    if indexes is not None:
        t["indexes"] = indexes
    return t


@pytest.fixture
def db(tmp_path):
    d = PluginDb("notes", db_path=str(tmp_path / "notes" / "data.db"))
    yield d
    d.close()


def test_apply_schema_creates_table_and_parent_dir(db):
    db.apply_schema([_notes_table(indexes=["created_at"])])
    rows = db.query("notes")
    assert rows == []


def test_insert_auto_generates_id_and_query_returns_row(db):
    db.apply_schema([_notes_table()])
    row_id = db.insert("notes", {"text": " hello ", "created_at": 1})
    assert isinstance(row_id, str) and len(row_id) == 32  # uuid hex
    assert db.query("notes") == [{"id": row_id, "text": " hello ", "created_at": 1}]


def test_insert_with_explicit_id_returns_it(db):
    db.apply_schema([_notes_table()])
    assert db.insert("notes", {"id": "x1", "text": "a", "created_at": 2}) == "x1"


def test_query_where_order_limit(db):
    db.apply_schema([_notes_table()])
    db.insert("notes", {"text": "a", "created_at": 1})
    db.insert("notes", {"text": "b", "created_at": 2})
    db.insert("notes", {"text": "b", "created_at": 3})
    rows = db.query("notes", where={"text": "b"}, order="created_at DESC", limit=1)
    assert [r["created_at"] for r in rows] == [3]
    rows = db.query("notes", order="created_at")
    assert [r["created_at"] for r in rows] == [1, 2, 3]


def test_update_and_delete(db):
    db.apply_schema([_notes_table()])
    rid = db.insert("notes", {"text": "old", "created_at": 1})
    db.update("notes", rid, {"text": "new"})
    assert db.query("notes", where={"id": rid})[0]["text"] == "new"
    db.delete("notes", rid)
    assert db.query("notes") == []


def test_values_are_parameter_bound_not_concatenated(db):
    db.apply_schema([_notes_table()])
    evil = "x'); DROP TABLE notes;--"
    db.insert("notes", {"text": evil, "created_at": 1})
    assert db.query("notes")[0]["text"] == evil  # 表还在，值原样落库


@pytest.mark.parametrize("bad", ["1abc", "a b", "a-b", "a;b", "", "a.b"])
def test_invalid_table_name_rejected(db, bad):
    with pytest.raises(ValueError):
        db.query(bad)


@pytest.mark.parametrize("bad", ["1x", "x y", "x;y"])
def test_invalid_column_name_rejected(db, bad):
    db.apply_schema([_notes_table()])
    with pytest.raises(ValueError):
        db.insert("notes", {bad: "v"})


def test_invalid_order_rejected(db):
    db.apply_schema([_notes_table()])
    with pytest.raises(ValueError):
        db.query("notes", order="created_at; DROP TABLE notes")


def test_unsupported_column_type_rejected(tmp_path):
    d = PluginDb("p", db_path=str(tmp_path / "p" / "data.db"))
    with pytest.raises(ValueError):
        d.apply_schema([{"name": "t", "columns": [{"name": "c", "type": "blob"}]}])
    d.close()


def test_reopen_is_additive_adds_missing_column_with_default(tmp_path):
    path = str(tmp_path / "notes" / "data.db")
    d1 = PluginDb("notes", db_path=path)
    d1.apply_schema([_notes_table()])
    rid = d1.insert("notes", {"text": "keep", "created_at": 1})
    d1.close()

    # 重开：新 schema 加了 mood 列（带默认值），旧列一个不少
    d2 = PluginDb("notes", db_path=path)
    d2.apply_schema([_notes_table(extra_cols=[{"name": "mood", "type": "text", "default": ""}])])
    rows = d2.query("notes")
    assert rows == [{"id": rid, "text": "keep", "created_at": 1, "mood": ""}]
    # 重复 apply 幂等（索引 IF NOT EXISTS）
    d2.apply_schema([_notes_table(extra_cols=[{"name": "mood", "type": "text", "default": ""}], indexes=["created_at"])])
    d2.close()


def test_default_db_path_uses_config_plugin_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    d = PluginDb("notes")
    d.apply_schema([_notes_table()])
    d.insert("notes", {"text": "x", "created_at": 1})
    d.close()
    assert (tmp_path / "data" / "plugins" / "notes" / "data.db").is_file()
