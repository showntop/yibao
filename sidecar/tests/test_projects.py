"""项目实体（V1a）：ProjectStore + project.* tool 的行为测试。"""
import json
import os

import pytest

from yibao_brain.projects import ProjectStore, SKELETON_DIRS
from yibao_brain.project_tools import make_project_tools


@pytest.fixture
def store(tmp_path, monkeypatch):
    # 数据目录指到 tmp：目录骨架不污染真实 ~/Library/Application Support
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    # settings.json 也在 tmp：current_project_id 持久化走它
    from yibao_brain import config
    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    return ProjectStore(str(tmp_path / "projects.json"))


def test_create_creates_skeleton_and_switches(store, tmp_path):
    p = store.create("测试项目")
    assert p["id"].startswith("proj_")
    assert p["name"] == "测试项目"
    for sub in SKELETON_DIRS:
        assert os.path.isdir(os.path.join(p["dir"], sub))
    assert store.current_id() == p["id"]
    # 第二次创建同名 → ValueError
    with pytest.raises(ValueError):
        store.create("测试项目")


def test_slug_friendly_and_collision(store):
    p1 = store.create("a/b 项目")
    p2 = store.create("a\\b 项目")  # 不同名但 slug 相同 → 目录名加 id 段避让
    assert p1["dir"] != p2["dir"]
    assert os.path.isdir(p1["dir"]) and os.path.isdir(p2["dir"])


def test_add_object_dedup_and_touch(store):
    p = store.create("obj 项目")
    assert store.add_object(p["id"], "zimeiti.topic", "t1")
    assert store.add_object(p["id"], "zimeiti.topic", "t1")  # 重复挂载去重
    proj = store.get(p["id"])
    assert proj["objects"] == [{"type": "zimeiti.topic", "ref": "t1"}]
    assert store.remove_object(p["id"], "zimeiti.topic", "t1")
    assert store.get(p["id"])["objects"] == []


def test_current_persists_in_settings(store, tmp_path):
    p = store.create("持久化项目")
    # 重读 settings.json 验证 current_project_id 落盘
    with open(tmp_path / "settings.json", encoding="utf-8") as f:
        s = json.load(f)
    assert s.get("current_project_id") == p["id"]


def test_corrupt_json_backs_up_and_starts_empty(store, tmp_path):
    path = tmp_path / "projects.json"
    path.write_text("not json", encoding="utf-8")
    s2 = ProjectStore(str(path))
    assert s2.list() == []
    assert (tmp_path / "projects.json.bak").exists()


# ---------- project.* tool 层 ----------

@pytest.fixture
def tools(store):
    fired = []
    ts = {t.id: t for t in make_project_tools(store, on_change=lambda: fired.append(1))}
    return ts, fired


def test_create_tool_is_confirm_level(tools):
    ts, _ = tools
    from yibao_brain.ipc import RiskLevel
    assert ts["project.create"].default_risk == RiskLevel.L3_HIGH  # 设计 L2 按印 → 代码 L3


def test_create_tool_success_and_notify(tools):
    ts, fired = tools
    r = ts["project.create"].run({"name": "立项项目"}, None)
    assert r.success and r.data["project"]["name"] == "立项项目"
    assert fired  # on_change 广播已触发


def test_open_and_current(tools):
    ts, _ = tools
    ts["project.create"].run({"name": "目标项目"}, None)
    ts["project.create"].run({"name": "别的项目"}, None)  # 当前已切走
    r = ts["project.open"].run({"name": "目标项目"}, None)
    assert r.success
    r2 = ts["project.current"].run({}, None)
    assert r2.data["project"]["name"] == "目标项目"


def test_attach_default_current(tools):
    ts, _ = tools
    ts["project.create"].run({"name": "挂载项目"}, None)
    r = ts["project.attach"].run({"type": "video.script", "ref": "s1"}, None)
    assert r.success
    proj = ts["project.current"].run({}, None).data["project"]
    assert {"type": "video.script", "ref": "s1"} in proj["objects"]
