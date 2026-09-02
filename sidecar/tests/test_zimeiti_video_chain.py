"""视频工作流端到端验收（产品审计核心）：经 invoker 走全链，产出真 MP4。

链：zimeiti.add → mat_save → article_save → storyboard_save → voice_save →
visual_card_save → timeline_save → render_save（durable execution 第一个真实消费者）。

断言（对应审计「Timeline 引用 Shot/Asset，能输出至少一版可播放 MP4；时长、比例、
版本来源均可验收」）：
- 真 MP4 存在：ffprobe 实测 1080×1920，时长 ≈ 各 clip 求和（容差）；
- render 走 durable 引擎：execution completed、deliver 段 checkpoint 逐片段推进；
- Work Graph：run 推进到 completed（current_stage=deliver），各 artifact 与
  contains/derived_from/uses/rendered_from 边落图。
为控制时长用 2 镜短口播；say/afconvert/ffmpeg/ffprobe 缺失时整体跳过。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from yibao_brain.audit import AuditLog
from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.invoker import ToolInvoker
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_events import WorkGraphInvocationSink
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("say", "afconvert", "ffmpeg", "ffprobe")),
    reason="需要本机 say/afconvert/ffmpeg/ffprobe",
)

_SHOTS = [
    {"idx": 1, "narration": "开场", "duration": 2, "visual": "黑底白字：开场钩子"},
    {"idx": 2, "narration": "收尾", "duration": 2, "visual": "结尾卡：点关注"},
]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


def _ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    doc = json.loads(out)
    return int(doc["streams"][0]["width"]), int(doc["streams"][0]["height"]), float(doc["format"]["duration"])


def test_video_chain_end_to_end_produces_playable_mp4(data_dir, tmp_path):
    reg = ToolRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    engine = DurableExecutionEngine(graph)
    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=engine,
    )
    assert results["zimeiti"] == "ok"
    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "video"))
    invoker = ToolInvoker(
        reg, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "video")
    invoker.durable_engine = engine

    def execute(tool_id, params):
        action = invoker.propose(ToolCall(id=f"tc-{tool_id}", tool_id=tool_id, params=params))
        result = invoker.execute(action, params, {"conversation_id": "session-video", "surface": "home"})
        assert result.success, f"{tool_id}: {result.error}"
        return result

    try:
        topic = execute("zimeiti.add", {"title": "Agent 为什么需要你点头"})
        tid = topic.data["id"]
        execute("zimeiti.mat_save", {
            "text": "Agent 系统由模型、工具、记忆与权限治理共同组成。", "defer": True,
        })
        execute("zimeiti.article_save", {
            "id": tid, "content": "Agent 会规划、调用工具，并把结果交还给用户确认。", "note": "初稿",
        })
        execute("zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS, "note": "两镜短片"})
        voice = execute("zimeiti.voice_save", {"topic_id": tid})
        execute("zimeiti.visual_card_save", {"topic_id": tid})
        timeline = execute("zimeiti.timeline_save", {"topic_id": tid})
        render = execute("zimeiti.render_save", {"topic_id": tid})

        # ---- 真 MP4：分辨率/时长/版本来源均可验收 ----
        mp4 = Path(render.data["path"])
        assert mp4.is_file() and mp4.stat().st_size > 0 and data_dir in mp4.parents
        width, height, duration = _ffprobe(mp4)
        assert (width, height) == (1080, 1920)
        expected = timeline.data["duration_sec"]  # 两镜都有音轨：ffprobe 实测时长求和
        assert expected == pytest.approx(
            sum(t["duration_sec"] for t in voice.data["tracks"]), abs=0.01,
        )
        assert duration == pytest.approx(expected, abs=1.0)
        acceptance = render.data["acceptance"]
        assert acceptance == {
            "aspect_ok": True, "resolution": "1080x1920",
            "duration_sec": acceptance["duration_sec"], "timeline_version": 1,
            "duration_expected": expected, "duration_ok": True,
        }
        assert acceptance["duration_sec"] == pytest.approx(expected, abs=1.0)

        # ---- render 是 durable execution 的真实消费者 ----
        assert render.data["durable"] is True and render.data["execution_id"]
        execution = graph.durable_execution_view(render.data["execution_id"])
        assert execution["status"] == "completed"
        assert execution["capability_id"] == "video.render" and execution["stage_id"] == "deliver"
        assert execution["checkpoint"]["segments_done"] == [0, 1]  # 每片段一个 checkpoint

        # ---- Work Graph：run 完成 + artifact/边落图 ----
        view = graph.workspace_view("video")
        run = view["workflow_run"]
        assert run["status"] == "completed" and run["current_stage_id"] == "deliver"
        types = {item["type"] for item in view["objects"]}
        assert types == {
            "zimeiti.topic", "research.evidence", "video.script",
            "video.storyboard", "video.shot", "asset.visual", "voice.track",
            "timeline.composition", "video.render",
        }
        render_artifact = next(item for item in view["objects"] if item["type"] == "video.render")
        assert render_artifact["ref"] == f"{tid}#render"
        revisions = graph.artifact_view(render_artifact["artifact_id"])["revisions"]
        assert revisions[-1]["content_ref"].endswith("v1.mp4")
        # 边落图：rendered_from / derived_from / uses 全齐（id → type 从 objects 反解）
        type_by_id = {item["artifact_id"]: item["type"] for item in view["objects"]}
        timeline_artifact = next(item for item in view["objects"] if item["type"] == "timeline.composition")
        edges = (
            graph.artifact_view(render_artifact["artifact_id"])["edges"]
            + graph.artifact_view(timeline_artifact["artifact_id"])["edges"]
        )
        relations = {
            (e["relation"], type_by_id.get(e["source_artifact_id"]), type_by_id.get(e["target_artifact_id"]))
            for e in edges
        }
        assert ("rendered_from", "video.render", "timeline.composition") in relations
        assert ("derived_from", "timeline.composition", "video.storyboard") in relations
        assert ("uses", "timeline.composition", "asset.visual") in relations
        assert ("uses", "timeline.composition", "voice.track") in relations
    finally:
        engine.shutdown()
        graph.close()
