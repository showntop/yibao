"""DurableExecution: long-running video/deck work survives failure and restart."""
from __future__ import annotations

import threading

from yibao_brain.durable_execution import (
    DurableExecutionEngine,
    DurableOutcome,
    DurableProviderError,
)
from yibao_brain.work_graph import WorkGraphStore


def _provide(graph: WorkGraphStore, *artifact_types: str) -> None:
    """测试域类型经 provider 索引注册（typed registry：类型来自插件 work_outputs 声明）。"""
    graph.set_capability_providers({
        artifact_type: [{"plugin_id": "test", "tool_id": "test.provide", "label": artifact_type}]
        for artifact_type in artifact_types
    })


def _prepare_video(graph: WorkGraphStore, tmp_path) -> None:
    _provide(graph, "zimeiti.topic", "research.evidence", "video.script", "video.storyboard")
    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "video"))
    for artifact_type, ref in (
        ("zimeiti.topic", "agent"),
        ("research.evidence", "sources"),
        ("video.script", "script"),
        ("video.storyboard", "storyboard"),
    ):
        graph.attach_external_artifact("video", artifact_type, ref)


def _prepare_deck(graph: WorkGraphStore, tmp_path) -> None:
    _provide(graph, "brief.presentation", "deck.claim_set", "deck.storyline")
    graph.create_workspace("deck", "Agent OS 产品架构 PPT", str(tmp_path / "deck"))
    for artifact_type, ref in (
        ("brief.presentation", "brief"),
        ("deck.claim_set", "claims"),
        ("deck.storyline", "storyline"),
    ):
        graph.attach_external_artifact("deck", artifact_type, ref)


def test_video_provider_fallback_resumes_checkpoint_and_projects_output(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    _prepare_video(graph, tmp_path)
    engine = DurableExecutionEngine(graph)
    seen: list[tuple[str, dict]] = []

    def primary(_request, checkpoint, control):
        seen.append(("primary", checkpoint))
        control.checkpoint({"completed_assets": ["hero.png"]}, progress=0.5)
        raise DurableProviderError("primary quota exhausted", retryable=True)

    def fallback(request, checkpoint, control):
        seen.append(("fallback", checkpoint))
        assert checkpoint == {"completed_assets": ["hero.png"]}
        control.checkpoint(
            {"completed_assets": ["hero.png", "diagram.png"]}, progress=0.9,
        )
        return DurableOutcome(
            result={"asset_count": 2, "provider": "fallback"},
            work_events=({
                "event_type": "artifact.upsert",
                "payload": {
                    "artifact_type": "video.asset",
                    "ref": request["asset_set_id"],
                    "content_ref": "blob://video-assets-v1",
                },
            },),
        )

    engine.register_provider(
        capability_id="media.generate", provider_id="primary", handler=primary,
    )
    engine.register_provider(
        capability_id="media.generate", provider_id="fallback", handler=fallback,
    )
    execution = engine.start(
        workspace_id="video",
        stage_id="assets",
        capability_id="media.generate",
        provider_candidates=["primary", "fallback"],
        request={"asset_set_id": "agent-assets"},
        idempotency_key="assets-v1",
    )
    done = engine.wait(execution["id"], timeout=3)
    try:
        assert done["status"] == "completed"
        assert done["provider_id"] == "fallback"
        assert done["result"] == {"asset_count": 2, "provider": "fallback"}
        assert [attempt["status"] for attempt in done["attempts"]] == ["failed", "completed"]
        assert seen == [
            ("primary", {}),
            ("fallback", {"completed_assets": ["hero.png"]}),
        ]
        view = graph.workspace_view("video")
        assets = next(stage for stage in view["workflow_run"]["stages"] if stage["id"] == "assets")
        assert assets["status"] == "completed"
        assert assets["checkpoint"] == {"completed_assets": ["hero.png", "diagram.png"]}
        assert assets["execution"]["status"] == "completed"
        assert any(item["type"] == "video.asset" for item in view["objects"])
    finally:
        engine.shutdown()
        graph.close()


def test_deck_execution_cancel_preserves_checkpoint(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    _prepare_deck(graph, tmp_path)
    engine = DurableExecutionEngine(graph)
    checkpointed = threading.Event()
    release = threading.Event()

    def slides(_request, _checkpoint, control):
        control.checkpoint({"completed_pages": [1, 2, 3]}, progress=0.25)
        checkpointed.set()
        release.wait(timeout=2)
        control.raise_if_cancelled()
        return {"page_count": 12}

    engine.register_provider(
        capability_id="deck.compose", provider_id="native", handler=slides,
    )
    execution = engine.start(
        workspace_id="deck",
        stage_id="slides",
        capability_id="deck.compose",
        provider_candidates=["native"],
        request={"pages": 12},
        idempotency_key="deck-pages-v1",
    )
    assert checkpointed.wait(timeout=2)
    assert engine.cancel(execution["id"])
    release.set()
    done = engine.wait(execution["id"], timeout=3)
    try:
        assert done["status"] == "cancelled"
        assert done["progress"] == 0.25
        assert done["checkpoint"] == {"completed_pages": [1, 2, 3]}
        stage = next(
            item for item in graph.workspace_view("deck")["workflow_run"]["stages"]
            if item["id"] == "slides"
        )
        assert stage["status"] == "ready"
        assert stage["checkpoint"] == {"completed_pages": [1, 2, 3]}
    finally:
        engine.shutdown()
        graph.close()


def test_process_restart_recovers_same_provider_from_checkpoint(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    _prepare_deck(graph, tmp_path)
    execution = graph.create_durable_execution(
        workspace_id="deck",
        stage_id="visual",
        capability_id="deck.visualize",
        provider_candidates=["native"],
        request={"visuals": 4},
        idempotency_key="visuals-v1",
    )
    claimed = graph.claim_durable_execution(execution["id"], "native")
    graph.checkpoint_durable_execution(
        execution["id"], {"completed_visuals": ["cover"]},
        progress=0.25, expected_version=claimed["checkpoint_version"],
    )
    graph.close()  # simulate a process exiting while provider code is still active

    reopened = WorkGraphStore(str(path))
    interrupted = reopened.durable_execution_view(execution["id"])
    assert interrupted["status"] == "interrupted"
    engine = DurableExecutionEngine(reopened)
    seen_checkpoint = {}

    def resume(_request, checkpoint, control):
        seen_checkpoint.update(checkpoint)
        control.checkpoint(
            {"completed_visuals": ["cover", "architecture", "flow", "summary"]},
            progress=1,
        )
        return {"visual_count": 4}

    engine.register_provider(
        capability_id="deck.visualize", provider_id="native", handler=resume,
    )
    assert engine.recover() == [execution["id"]]
    done = engine.wait(execution["id"], timeout=3)
    try:
        assert done["status"] == "completed"
        assert done["attempt"] == 2
        assert seen_checkpoint == {"completed_visuals": ["cover"]}
        assert [item["status"] for item in done["attempts"]] == ["interrupted", "completed"]
    finally:
        engine.shutdown()
        reopened.close()
