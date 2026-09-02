"""Typed artifact registry：未注册类型不得经 attach 进入 Work Graph。

注册类型 = 插件 work_outputs 声明（build_capability_index → set_capability_providers）
+ 内核 CORE_ARTIFACT_TYPES。legacy projects.json 迁移的旧自由类型打标放行（grandfather）。
"""
from __future__ import annotations

import json

import pytest

from yibao_brain.projects import ProjectStore
from yibao_brain.work_graph import WorkGraphStore


def _provide(graph: WorkGraphStore, *artifact_types: str) -> None:
    """测试域类型经 provider 索引注册（与插件 work_outputs 声明同一条入口）。"""
    graph.set_capability_providers({
        artifact_type: [{"plugin_id": "test", "tool_id": "test.provide", "label": artifact_type}]
        for artifact_type in artifact_types
    })


def test_attach_rejects_unregistered_type_with_human_error(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        graph.create_workspace("ws", "注册表", str(tmp_path / "ws"))
        with pytest.raises(ValueError, match="未注册的对象类型：ghost.type"):
            graph.attach_external_artifact("ws", "ghost.type", "x1")
        # 拒绝是整体性的：图谱里没有落下任何对象。
        assert graph.workspace_view("ws")["objects"] == []
    finally:
        graph.close()


def test_attach_rejected_before_registration_allowed_after(tmp_path):
    """注册前 attach 拒绝、插件加载注册后同类型放行（顺序与 server 启动一致）。"""
    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        graph.create_workspace("ws", "注册表", str(tmp_path / "ws"))
        with pytest.raises(ValueError, match="未注册"):
            graph.attach_external_artifact("ws", "video.script", "s1")
        _provide(graph, "video.script")
        attached = graph.attach_external_artifact("ws", "video.script", "s1")
        assert attached["type"] == "video.script"
        assert attached["external_ref"] == "s1"
    finally:
        graph.close()


def test_attach_still_rejects_empty_type_or_ref(tmp_path):
    """旧的非空校验不因注册表上线而松动。"""
    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        graph.create_workspace("ws", "注册表", str(tmp_path / "ws"))
        _provide(graph, "video.script")
        with pytest.raises(ValueError, match="不能为空"):
            graph.attach_external_artifact("ws", "video.script", "  ")
        with pytest.raises(ValueError, match="不能为空"):
            graph.attach_external_artifact("ws", "", "s1")
    finally:
        graph.close()


def test_create_workspace_validates_initial_objects_and_rolls_back(tmp_path):
    """project.create 的初始 objects 同样过注册表；未注册类型 → 整个 Workspace 不落库。"""
    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        with pytest.raises(ValueError, match="未注册的对象类型：ghost.type"):
            graph.create_workspace(
                "ws", "初始对象", str(tmp_path / "ws"),
                objects=[{"type": "ghost.type", "ref": "x"}],
            )
        assert graph.workspace_view("ws") is None
        _provide(graph, "zimeiti.topic")
        view = graph.create_workspace(
            "ws2", "初始对象2", str(tmp_path / "ws2"),
            objects=[{"type": "zimeiti.topic", "ref": "t1"}],
        )
        assert view["objects"][0]["type"] == "zimeiti.topic"
    finally:
        graph.close()


def test_legacy_migration_grandfathers_free_types_but_new_attach_is_checked(tmp_path, monkeypatch):
    """旧 projects.json 的自由类型迁移放行（grandfather）；迁移后的新 attach 照查注册表。"""
    from yibao_brain import config

    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    legacy = {
        "projects": [{
            "id": "proj_legacy",
            "name": "迁移项目",
            "created_at": 10.0,
            "touched_at": 20.0,
            "dir": str(tmp_path / "legacy"),
            "objects": [{"type": "ancient.free", "ref": "a1"}],
        }],
    }
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        store = ProjectStore(str(path), work_graph=graph)
        migrated = store.get("proj_legacy")
        assert migrated["objects"][0]["type"] == "ancient.free"  # 旧数据不丢

        with pytest.raises(ValueError, match="未注册的对象类型：ancient.free"):
            graph.attach_external_artifact("proj_legacy", "ancient.free", "a2")
    finally:
        graph.close()


def test_registered_types_come_from_capability_index(tmp_path):
    """注册表的数据源就是能力索引：注入什么 provider 类型，就放行什么类型。"""
    graph = WorkGraphStore(str(tmp_path / "g.db"))
    try:
        graph.create_workspace("ws", "注册表", str(tmp_path / "ws"))
        _provide(graph, "research.evidence", "video.script")
        assert graph.attach_external_artifact("ws", "research.evidence", "e1") is not None
        with pytest.raises(ValueError, match="未注册"):
            graph.attach_external_artifact("ws", "video.storyboard", "sb1")
        # 索引整体替换（热重载语义）：后一次注入的类型集合生效。
        _provide(graph, "video.storyboard")
        assert graph.attach_external_artifact("ws", "video.storyboard", "sb1") is not None
        with pytest.raises(ValueError, match="未注册"):
            graph.attach_external_artifact("ws", "video.script", "s1")
    finally:
        graph.close()
