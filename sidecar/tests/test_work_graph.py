"""Agent OS Work Graph：跨领域对象、版本、关系、流程与 Project 迁移。"""
from __future__ import annotations

import json

import pytest

from yibao_brain.audit import AuditLog
from yibao_brain.blob_store import BlobStore
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.llm import ToolCall
from yibao_brain.plugindb import PluginDb
from yibao_brain.projects import ProjectStore
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.tools import Tool, ToolContext, ToolRegistry
from yibao_brain.work_events import WorkGraphInvocationSink
from yibao_brain.work_graph import BUILTIN_WORKFLOWS, WorkGraphStore


def test_video_and_deck_share_schema_but_keep_their_workflow(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.create_workspace(
            "ws_video", "Agent 概念科普视频", str(tmp_path / "video"),
            objects=[{"type": "zimeiti.topic", "ref": "t1"}],
        )
        video = graph.workspace_view("ws_video")
        assert video["workflow_run"]["definition_id"] == "video.explainer"
        assert video["workflow_run"]["current_stage_id"] == "evidence"

        graph.attach_external_artifact("ws_video", "research.evidence", "e1")
        graph.attach_external_artifact("ws_video", "video.script", "s1")
        video = graph.workspace_view("ws_video")
        assert video["workflow_run"]["current_stage_id"] == "storyboard"
        assert [s["label"] for s in video["workflow_run"]["stages"]][:4] == ["选题", "证据", "脚本", "分镜"]

        graph.create_workspace(
            "ws_deck", "Agent 策略复盘 PPT", str(tmp_path / "deck"),
            objects=[{"type": "brief.presentation", "ref": "b1"}],
            mission_title="周五前完成 12 页策略汇报",
        )
        graph.attach_external_artifact("ws_deck", "deck.storyline", "story1")
        graph.attach_external_artifact("ws_deck", "deck.slide", "slide1")
        deck = graph.workspace_view("ws_deck")
        assert deck["workflow_run"]["definition_id"] == "deck.presentation"
        assert deck["workflow_run"]["current_stage_id"] == "claims"
        assert next(s for s in deck["workflow_run"]["stages"] if s["id"] == "slides")["status"] == "blocked"
        assert deck["mission"]["title"] == "周五前完成 12 页策略汇报"
        assert [s["label"] for s in deck["workflow_run"]["stages"]][:4] == ["需求", "主张", "故事线", "页面"]

        # 两个领域共用完全相同的 Artifact/Revision 读模型，不为 PPT 增加 core 表。
        assert set(video["objects"][0]) == set(deck["objects"][0])
    finally:
        graph.close()


def test_revision_is_immutable_and_edges_are_workspace_scoped(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.create_workspace("ws", "证据驱动视频", str(tmp_path / "ws"))
        evidence = graph.attach_external_artifact("ws", "research.evidence", "e1")
        script = graph.attach_external_artifact("ws", "video.script", "s1")

        first_revision = script["head_revision_id"]
        second = graph.create_revision(
            script["id"], "blob://sha256/script-v2",
            metadata={"summary": "人工修改"}, created_by="user",
        )
        latest = graph.artifact_view(script["id"])
        assert latest["head_revision_id"] == second["id"]
        assert [r["id"] for r in latest["revisions"]] == [first_revision, second["id"]]
        assert second["parent_revision_ids"] == [first_revision]
        assert latest["revisions"][0]["content_ref"].startswith("external://")

        edge = graph.add_edge(evidence["id"], script["id"], "supports", label="核心主张")
        assert edge["relation"] == "supports"
        assert graph.artifact_view(script["id"])["edges"][0]["source_artifact_id"] == evidence["id"]

        graph.create_workspace("other", "另一工作区", str(tmp_path / "other"))
        foreign = graph.attach_external_artifact("other", "research.evidence", "e2")
        with pytest.raises(ValueError, match="跨 Workspace"):
            graph.add_edge(foreign["id"], script["id"], "supports")
    finally:
        graph.close()


def test_project_json_migrates_once_then_work_graph_is_authoritative(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain import config

    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    legacy = {
        "projects": [{
            "id": "proj_legacy",
            "name": "迁移视频",
            "created_at": 10.0,
            "touched_at": 20.0,
            "dir": str(tmp_path / "legacy"),
            "objects": [{"type": "zimeiti.topic", "ref": "t1"}],
        }],
    }
    path = tmp_path / "projects.json"
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    store = ProjectStore(str(path), work_graph=graph)
    assert store.get("proj_legacy")["objects"][0]["artifact_id"].startswith("artifact_")

    store.add_object("proj_legacy", "video.script", "s1")
    # 新关系只写 Work Graph；legacy JSON 不发生 objects 双写。
    assert json.loads(path.read_text(encoding="utf-8"))["projects"][0]["objects"] == [
        {"type": "zimeiti.topic", "ref": "t1"},
    ]
    assert {o["ref"] for o in store.get("proj_legacy")["objects"]} == {"t1", "s1"}

    store.remove_object("proj_legacy", "zimeiti.topic", "t1")
    graph.close()

    # 重启会再次看到旧 JSON，但 migration ledger 阻止已移除对象被重新挂回。
    reloaded_graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        reloaded_store = ProjectStore(str(path), work_graph=reloaded_graph)
        view = reloaded_store.get("proj_legacy")
        assert [o["ref"] for o in view["objects"]] == ["s1"]
        assert view["mission"]["id"].startswith("mission_")
        assert view["workflow_run"]["current_stage_id"] == "topic"
        assert next(s for s in view["workflow_run"]["stages"] if s["id"] == "script")["status"] == "blocked"
    finally:
        reloaded_graph.close()


def test_workflow_run_restores_after_database_reopen(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace("deck", "董事会 PPT", str(tmp_path / "deck"))
    graph.attach_external_artifact("deck", "deck.export.pptx", "final.pptx")
    assert graph.workspace_view("deck")["workflow_run"]["status"] != "completed"
    for artifact_type, ref in (
        ("brief.presentation", "brief"),
        ("research.claim", "claims"),
        ("deck.storyline", "storyline"),
        ("deck.slide", "slide-1"),
        ("visual.image", "hero"),
        ("quality.validation", "qa"),
    ):
        graph.attach_external_artifact("deck", artifact_type, ref)
    before = graph.workspace_view("deck")["workflow_run"]
    assert before["status"] == "completed"
    graph.close()

    reopened = WorkGraphStore(str(path))
    try:
        after = reopened.workspace_view("deck")["workflow_run"]
        assert after["id"] == before["id"]
        assert after["status"] == "completed"
        assert after["current_stage_id"] == "export"
    finally:
        reopened.close()


def test_startup_reprojects_locked_run_after_same_version_core_rewrite(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace(
        "video", "Agent 视频", str(tmp_path / "video"),
        objects=[
            {"type": "zimeiti.topic", "ref": "topic"},
            {"type": "research.evidence", "ref": "evidence"},
        ],
    )
    run_id = graph.workspace_view("video")["workflow_run"]["id"]
    # 模拟上一代线性投影留下的状态；定义同版本原位重写后必须重算。
    graph._conn.execute(
        "UPDATE stage_instances SET status='pending',input_artifact_ids='[]' WHERE workflow_run_id=?",
        (run_id,),
    )
    graph._conn.execute(
        "UPDATE workflow_runs SET status='running',current_stage_id='evidence' WHERE id=?", (run_id,),
    )
    graph._conn.commit()
    graph.close()

    reopened = WorkGraphStore(str(path))
    try:
        run = reopened.workspace_view("video")["workflow_run"]
        assert run["current_stage_id"] == "script"
        assert [stage["status"] for stage in run["stages"][:3]] == [
            "completed", "completed", "ready",
        ]
        script = next(stage for stage in run["stages"] if stage["id"] == "script")
        evidence = next(stage for stage in run["stages"] if stage["id"] == "evidence")
        assert script["input_artifact_ids"] == evidence["output_artifact_ids"]
    finally:
        reopened.close()


def test_workspace_creation_rolls_back_as_one_unit(tmp_path, monkeypatch):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        def fail_attach(*_args, **_kwargs):
            raise RuntimeError("simulated artifact failure")

        monkeypatch.setattr(graph, "_attach_external_locked", fail_attach)
        with pytest.raises(RuntimeError, match="simulated artifact failure"):
            graph.create_workspace(
                "atomic", "原子创建", str(tmp_path / "atomic"),
                objects=[{"type": "brief", "ref": "b1"}],
            )
        assert graph.workspace_view("atomic") is None
    finally:
        graph.close()


class _ArtifactTool(Tool):
    id = "demo.save"
    default_risk = RiskLevel.L1_LOW
    work_outputs = ({
        "kind": "artifact", "artifact_type": "deck.document",
        "ref_from": "data.id", "content_ref_from": "data.path",
        "metadata_fields": ["data.version"],
    },)

    def run(self, params, ctx):
        return ActionResult(success=True, data={
            "id": params["id"], "path": params["path"], "version": params["version"],
        })


class _ReadonlyProjectionTool(Tool):
    id = "demo.list"
    default_risk = RiskLevel.L0_READONLY

    def run(self, params, ctx):
        return ActionResult(success=True, data={"rows": [{"id": "one"}]})


class _GraphOutputTool(Tool):
    id = "demo.graph_output"
    default_risk = RiskLevel.L1_LOW
    # 故意把 edge 声明在 artifact 前；materializer 必须按引用完整性排序。
    work_outputs = (
        {
            "kind": "edge", "relation": "supports", "label": "核心主张",
            "source_artifact_type": "research.evidence", "source_ref_from": "data.evidence_id",
            "target_artifact_type": "video.script", "target_ref_from": "data.script_id",
        },
        {
            "kind": "artifact", "artifact_type": "research.evidence",
            "ref_from": "data.evidence_id", "content_ref_from": "data.evidence_ref",
        },
        {
            "kind": "artifact", "artifact_type": "video.script",
            "ref_from": "data.script_id", "content_ref_from": "data.script_ref",
        },
        {
            "kind": "checkpoint", "stage_id": "storyboard",
            "checkpoint_from": "data.checkpoint",
        },
    )

    def run(self, params, ctx):
        return ActionResult(success=True, data={
            "evidence_id": "evidence-main", "evidence_ref": "blob://evidence",
            "script_id": "script-main", "script_ref": "blob://script",
            "checkpoint": {"cursor": 12, "completed_shots": ["shot-01"]},
        })


def test_tool_invoker_immediately_projects_safe_result_to_work_graph(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("deck", "策略 PPT", str(tmp_path / "deck"))
    registry = ToolRegistry()
    registry.register(_ArtifactTool(), plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "deck")
    try:
        for version in (1, 2):
            params = {"id": "deck-main", "path": f"blob://deck-v{version}", "version": version}
            action = invoker.propose(ToolCall(id=f"tc{version}", tool_id="demo.save", params=params))
            result = invoker.execute(action, params, {"conversation_id": "session-a", "surface": "home"})
            assert result.success

        view = graph.workspace_view("deck")
        assert view["workflow_run"]["current_stage_id"] == "brief"
        assert next(s for s in view["workflow_run"]["stages"] if s["id"] == "slides")["status"] == "blocked"
        artifact = graph.artifact_view(view["objects"][0]["artifact_id"])
        assert [revision["content_ref"] for revision in artifact["revisions"]] == [
            "blob://deck-v1", "blob://deck-v2",
        ]
        invocations = graph.invocation_views(workspace_id="deck")
        assert [item["status"] for item in invocations] == ["succeeded", "succeeded"]
        assert invocations[0]["params_hash"] and "params" not in invocations[0]
        assert graph.outbox_views(invocations[0]["id"])[0]["status"] == "applied"
    finally:
        graph.close()


def test_declared_artifacts_edge_and_checkpoint_apply_as_one_ordered_contract(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace(
        "video", "Agent 概念科普视频", str(tmp_path / "video"),
        objects=[{"type": "zimeiti.topic", "ref": "agent-topic"}],
    )
    registry = ToolRegistry()
    registry.register(_GraphOutputTool(), plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "video")
    try:
        action = invoker.propose(ToolCall(id="graph", tool_id="demo.graph_output", params={}))
        assert invoker.execute(action, {}, {"conversation_id": "s", "surface": "stage"}).success

        invocation = graph.invocation_views(workspace_id="video")[0]
        events = graph.outbox_views(invocation["id"])
        assert [event["event_type"] for event in events] == [
            "artifact.upsert", "artifact.upsert", "artifact.edge.upsert", "stage.checkpoint",
        ]
        assert {event["status"] for event in events} == {"applied"}
        view = graph.workspace_view("video")
        by_type = {item["type"]: item for item in view["objects"]}
        script = graph.artifact_view(by_type["video.script"]["artifact_id"])
        assert script["edges"][0]["relation"] == "supports"
        assert script["edges"][0]["source_artifact_id"] == by_type["research.evidence"]["artifact_id"]
        storyboard = next(stage for stage in view["workflow_run"]["stages"] if stage["id"] == "storyboard")
        assert storyboard["status"] == "running"
        assert storyboard["checkpoint"] == {"cursor": 12, "completed_shots": ["shot-01"]}
        assert storyboard["checkpoint_version"] == 1
    finally:
        graph.close()


def test_dag_parallel_gates_and_checkpoint_survive_reopen(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace("deck", "Agent 策略 PPT", str(tmp_path / "deck"))
    try:
        # 绕过依赖投入后续产物，只能 blocked，不能伪造前置完成。
        graph.attach_external_artifact("deck", "deck.export.pptx", "premature.pptx")
        jumped = graph.workspace_view("deck")["workflow_run"]
        assert jumped["current_stage_id"] == "brief"
        assert next(stage for stage in jumped["stages"] if stage["id"] == "export")["status"] == "blocked"

        for artifact_type, ref in (
            ("brief.presentation", "brief"),
            ("research.claim", "claims"),
            ("deck.storyline", "storyline"),
        ):
            graph.attach_external_artifact("deck", artifact_type, ref)
        parallel = graph.workspace_view("deck")["workflow_run"]
        assert parallel["active_stage_ids"] == ["slides", "visual"]
        assert next(stage for stage in parallel["stages"] if stage["id"] == "validate")["status"] == "pending"

        first = graph.save_stage_checkpoint(
            parallel["id"], "slides", {"page": 4, "draft_ids": ["s1", "s2"]},
            expected_version=0,
        )
        assert first["status"] == "running" and first["checkpoint_version"] == 1
        with pytest.raises(ValueError, match="版本冲突"):
            graph.save_stage_checkpoint(parallel["id"], "slides", {"page": 2}, expected_version=0)
    finally:
        graph.close()

    reopened = WorkGraphStore(str(path))
    try:
        run = reopened.workspace_view("deck")["workflow_run"]
        slides = next(stage for stage in run["stages"] if stage["id"] == "slides")
        assert slides["status"] == "running"
        assert slides["checkpoint"] == {"page": 4, "draft_ids": ["s1", "s2"]}
        assert slides["checkpoint_version"] == 1
    finally:
        reopened.close()


def test_workflow_definition_rejects_cycles_and_supports_count_acceptance(tmp_path):
    with pytest.raises(ValueError, match="循环依赖"):
        WorkGraphStore.normalize_workflow_definition({
            "id": "bad.cycle", "version": "1", "domain": "test", "label": "cycle",
            "matches": [],
            "stages": [
                {"id": "a", "label": "A", "depends_on": ["b"], "acceptance": [{"artifact_patterns": ["a"]}]},
                {"id": "b", "label": "B", "depends_on": ["a"], "acceptance": [{"artifact_patterns": ["b"]}]},
            ],
        })

    graph = WorkGraphStore(str(tmp_path / "count.db"))
    try:
        graph.register_workflow({
            "id": "research.two_sources", "version": "1", "domain": "research",
            "label": "双源验收", "matches": ["双源"],
            "stages": [{
                "id": "sources", "label": "来源", "depends_on": [],
                "acceptance": [{"artifact_patterns": ["research\\.evidence"], "min_count": 2}],
            }],
        }, source_plugin="research")
        graph.create_workspace("research", "双源调研", str(tmp_path / "research"))
        graph.attach_external_artifact("research", "research.evidence", "one")
        assert graph.workspace_view("research")["workflow_run"]["status"] != "completed"
        graph.attach_external_artifact("research", "research.evidence", "two")
        assert graph.workspace_view("research")["workflow_run"]["status"] == "completed"
    finally:
        graph.close()


def test_transient_ui_projection_skips_work_graph_invocation_but_still_executes(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("deck", "策略 PPT", str(tmp_path / "deck"))
    registry = ToolRegistry()
    registry.register(_ReadonlyProjectionTool(), plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "deck")
    try:
        params = {}
        action = invoker.propose(ToolCall(id="widget", tool_id="demo.list", params=params))
        result = invoker.execute(action, params, {
            "surface": "widget", "invocation_persistence": "transient",
        })
        assert result.success and result.data == {"rows": [{"id": "one"}]}
        assert graph.invocation_views(workspace_id="deck") == []
    finally:
        graph.close()


def test_evidence_event_and_unbound_session_are_explicit(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("video", "证据视频", str(tmp_path / "video"))
    try:
        invocation = graph.begin_invocation(
            action_id="a1", workspace_id="video", conversation_id="s1",
            surface="home", tool_id="research.capture", params={"url": "https://example.com"},
        )
        graph.complete_invocation(invocation, success=True, safe_result={"ok": True}, work_events=[{
            "event_type": "evidence.capture",
            "payload": {
                "artifact_type": "research.evidence", "ref": "source-1",
                "claim": "Agent 会调用工具完成动作", "source_uri": "https://example.com/a",
                "source_title": "Agent Systems", "confidence": 0.8,
            },
        }])
        assert graph.outbox_views(invocation)[0]["status"] == "applied"
        evidence = graph.evidence_views("video")
        assert evidence[0]["claim"] == "Agent 会调用工具完成动作"
        assert graph.workspace_view("video")["workflow_run"]["current_stage_id"] == "topic"
        assert next(
            stage for stage in graph.workspace_view("video")["workflow_run"]["stages"]
            if stage["id"] == "evidence"
        )["status"] == "blocked"

        unbound = graph.begin_invocation(
            action_id="a2", workspace_id=None, conversation_id="s2",
            surface="home", tool_id="research.capture", params={},
        )
        graph.complete_invocation(unbound, success=True, safe_result={"ok": True}, work_events=[{
            "event_type": "artifact.upsert",
            "payload": {"artifact_type": "video.script", "ref": "must-not-leak"},
        }])
        blocked = graph.outbox_views(unbound)[0]
        assert blocked["status"] == "blocked"
        assert "未绑定 Workspace" in blocked["last_error"]
        assert all(obj["ref"] != "must-not-leak" for obj in graph.workspace_view("video")["objects"])
    finally:
        graph.close()


def test_running_invocation_recovers_as_interrupted(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace("ws", "恢复测试", str(tmp_path / "ws"))
    invocation = graph.begin_invocation(
        action_id="running", workspace_id="ws", conversation_id="s",
        surface="home", tool_id="demo.long", params={},
    )
    graph.close()

    reopened = WorkGraphStore(str(path))
    try:
        assert reopened.invocation_view(invocation)["status"] == "interrupted"
    finally:
        reopened.close()


def test_plugin_outbox_recovers_cross_database_crash_exactly_once(tmp_path):
    graph_path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(graph_path))
    graph.create_workspace("ws", "崩溃恢复视频", str(tmp_path / "ws"))
    invocation = graph.begin_invocation(
        action_id="save", workspace_id="ws", conversation_id="session",
        surface="home", tool_id="demo.save", params={"id": "script-main"},
    )
    plugin_db = PluginDb("demo", str(tmp_path / "plugin.db"))
    plugin_db.apply_schema([{
        "name": "documents",
        "columns": [
            {"name": "id", "type": "text", "pk": True},
            {"name": "content", "type": "text"},
        ],
    }])
    with plugin_db.work_transaction():
        plugin_db.insert("documents", {"id": "script-main", "content": "已提交正文"})
        plugin_db.enqueue_work_events(invocation, [{
            "event_type": "artifact.upsert",
            "payload": {
                "artifact_type": "video.script", "ref": "script-main",
                "content_ref": "plugin://demo/documents/script-main",
            },
        }])
    # 模拟 plugin commit 后、Host complete/ingest 前进程退出。
    graph.close()

    reopened = WorkGraphStore(str(graph_path))
    try:
        assert reopened.invocation_view(invocation)["status"] == "interrupted"
        sink = WorkGraphInvocationSink(reopened, lambda _cid: "ws")
        assert sink.reconcile_plugin_db(plugin_db) == 1
        assert reopened.invocation_view(invocation)["status"] == "succeeded"
        artifact_id = reopened.workspace_view("ws")["objects"][0]["artifact_id"]
        assert len(reopened.artifact_view(artifact_id)["revisions"]) == 1
        assert reopened.outbox_views(invocation)[0]["status"] == "applied"
        assert plugin_db.work_outbox_events()[0]["status"] == "acknowledged"

        # 重启恢复与主动重试都可以重复执行，不增加 Revision。
        assert sink.reconcile_plugin_db(plugin_db) == 0
        assert len(reopened.artifact_view(artifact_id)["revisions"]) == 1
    finally:
        plugin_db.close()
        reopened.close()


def test_blob_live_set_includes_revisions_and_blocked_outbox(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("ws", "Blob refs", str(tmp_path / "ws"))
    try:
        artifact = graph.attach_external_artifact("ws", "deck.document", "deck")
        graph.create_revision(artifact["id"], "blob://sha256/" + "a" * 64)
        invocation = graph.begin_invocation(
            action_id="unbound", workspace_id=None, conversation_id="s",
            surface="home", tool_id="deck.export", params={},
        )
        graph.complete_invocation(invocation, success=True, safe_result={}, work_events=[{
            "event_type": "artifact.upsert",
            "payload": {
                "artifact_type": "deck.export.pptx", "ref": "deck.pptx",
                "content_ref": "blob://sha256/" + "b" * 64,
            },
        }])
        assert graph.blob_refs() == {
            "blob://sha256/" + "a" * 64,
            "blob://sha256/" + "b" * 64,
        }
    finally:
        graph.close()


class _BrokenOutputContractTool(Tool):
    id = "demo.broken_save"
    default_risk = RiskLevel.L1_LOW
    work_outputs = ({
        "kind": "artifact", "artifact_type": "video.script", "ref_from": "data.id",
    },)

    def run(self, params, ctx):
        ctx.db.insert("documents", {"id": "should-rollback", "content": "正文"})
        return ActionResult(success=True, data={})


class _BrokenBlobContractTool(Tool):
    id = "demo.broken_blob"
    default_risk = RiskLevel.L1_LOW
    work_outputs = ({
        "kind": "artifact", "artifact_type": "deck.export.pptx", "ref_from": "data.id",
        "content_ref_from": "data.content_ref",
    },)

    def run(self, params, ctx):
        staged = ctx.blobs.stage_bytes(b"fake-pptx")
        content_ref = staged.finalize()
        ctx.db.insert("documents", {"id": "orphan", "content": content_ref})
        return ActionResult(success=True, data={"content_ref": content_ref})  # 故意缺 id


def test_invalid_runtime_work_output_rolls_back_plugin_business_write(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("ws", "契约失败", str(tmp_path / "ws"))
    plugin_db = PluginDb("demo", str(tmp_path / "plugin.db"))
    plugin_db.apply_schema([{
        "name": "documents",
        "columns": [
            {"name": "id", "type": "text", "pk": True},
            {"name": "content", "type": "text"},
        ],
    }])
    skill = _BrokenOutputContractTool()
    skill.plugin_ctx = ToolContext(db=plugin_db)
    registry = ToolRegistry()
    registry.register(skill, plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "ws")
    try:
        action = invoker.propose(ToolCall(id="broken", tool_id=skill.id, params={}))
        result = invoker.execute(action, {}, {"conversation_id": "session", "surface": "home"})
        assert not result.success
        assert "业务写入已回滚" in result.error
        assert plugin_db.query("documents") == []
        assert plugin_db.work_outbox_events() == []
        assert graph.invocation_views(workspace_id="ws")[0]["status"] == "failed"
    finally:
        plugin_db.close()
        graph.close()


def test_blob_promoted_before_failed_plugin_commit_becomes_collectable_orphan(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("ws", "Blob rollback", str(tmp_path / "ws"))
    plugin_db = PluginDb("demo", str(tmp_path / "plugin.db"))
    plugin_db.apply_schema([{
        "name": "documents",
        "columns": [
            {"name": "id", "type": "text", "pk": True},
            {"name": "content", "type": "text"},
        ],
    }])
    blobs = BlobStore(tmp_path / "blobs")
    skill = _BrokenBlobContractTool()
    skill.plugin_ctx = ToolContext(db=plugin_db, blobs=blobs)
    registry = ToolRegistry()
    registry.register(skill, plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "ws")
    try:
        action = invoker.propose(ToolCall(id="broken-blob", tool_id=skill.id, params={}))
        result = invoker.execute(action, {}, {"conversation_id": "session", "surface": "home"})
        assert not result.success and "业务写入已回滚" in result.error
        assert plugin_db.query("documents") == []
        objects = [path for path in blobs.objects_dir.glob("*/*") if path.is_file()]
        assert len(objects) == 1  # promote 已完成，但没有数据库/Work Graph 引用
        assert graph.blob_refs() == set()
        assert blobs.gc_orphans(graph.blob_refs(), grace_seconds=0)["objects"] == 1
        assert not objects[0].exists()
    finally:
        plugin_db.close()
        graph.close()


def test_plugin_workflow_pack_is_data_driven_and_cannot_overwrite_core(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.register_workflow({
            "id": "research.synthesis", "version": "1.0.0", "domain": "research",
            "label": "研究综合", "matches": ["研究"],
            "stages": [
                {"id": "collect", "label": "收集", "artifact_patterns": ["evidence"]},
                {"id": "report", "label": "报告", "artifact_patterns": ["report"]},
            ],
        }, source_plugin="research")
        graph.create_workspace("research", "Agent 研究报告", str(tmp_path / "research"))
        assert graph.workspace_view("research")["workflow_run"]["definition_id"] == "research.synthesis"

        with pytest.raises(ValueError, match="不能覆盖 core"):
            graph.register_workflow({**BUILTIN_WORKFLOWS[-1]}, source_plugin="untrusted")
    finally:
        graph.close()
