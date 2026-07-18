"""数据目录分离：用户数据统一放应用数据目录，与代码仓库解耦（v2 方案 §3.1/§9）。"""
import os

from yibao_brain import config


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "custom"))
    assert config.data_dir() == str(tmp_path / "custom")


def test_data_dir_default_is_app_support(monkeypatch):
    monkeypatch.delenv("YIBAO_DATA_DIR", raising=False)
    d = config.data_dir()
    assert "yibao" in d
    # 不再落在代码仓库里
    assert "sidecar" not in d


def test_user_data_paths_default_under_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    assert config.history_path() == str(tmp_path / "history.json")
    assert config.mem0_vector_path() == str(tmp_path / "mem0_store")
    assert config.audit_db_path() == str(tmp_path / "audit.db")
    assert config.plugin_data_dir("notes") == str(tmp_path / "plugins" / "notes")


def test_env_still_overrides_each_path(monkeypatch, tmp_path):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("YIBAO_HISTORY_PATH", "/tmp/h.json")
    monkeypatch.setenv("YIBAO_MEM0_VECTOR_PATH", "/tmp/m")
    assert config.history_path() == "/tmp/h.json"
    assert config.mem0_vector_path() == "/tmp/m"


def test_migrate_legacy_data_moves_old_repo_files(monkeypatch, tmp_path):
    legacy = tmp_path / "sidecar"
    legacy.mkdir()
    (legacy / "mem0_store").mkdir()
    (legacy / "mem0_store" / "x.bin").write_text("vec")
    (legacy / "audit.db").write_text("db")
    (legacy / "history.json").write_text("[]")
    data = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(data))

    config.migrate_legacy_data(str(legacy))

    assert (data / "mem0_store" / "x.bin").read_text() == "vec"
    assert (data / "audit.db").read_text() == "db"
    assert (data / "history.json").read_text() == "[]"
    assert not (legacy / "mem0_store").exists()
    assert not (legacy / "audit.db").exists()


def test_migrate_legacy_data_never_overwrites(monkeypatch, tmp_path):
    legacy = tmp_path / "sidecar"
    legacy.mkdir()
    (legacy / "audit.db").write_text("old")
    data = tmp_path / "data"
    data.mkdir()
    (data / "audit.db").write_text("new")
    monkeypatch.setenv("YIBAO_DATA_DIR", str(data))

    config.migrate_legacy_data(str(legacy))

    # 新位置已有数据 → 旧文件保留不覆盖，交给用户处理
    assert (data / "audit.db").read_text() == "new"
    assert (legacy / "audit.db").read_text() == "old"
