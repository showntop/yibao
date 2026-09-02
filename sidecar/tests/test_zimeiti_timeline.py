"""zimeiti 时间线（视频 workflow compose 段）：timeline_save 组装 timeline JSON。

核心断言：
- 读最新分镜 + 同分镜版本的 voice_tracks/visual_cards → 每镜一个 clip：
  duration 取音轨 ffprobe 实测时长；无音轨（空 narration 跳过镜）回退分镜 duration 并标
  silent: true；缺 visual 的镜 = 阻塞错误（人话，列出缺哪些镜）；
- 落盘 timelines/<topic_id>/v<N>.json（版本递增）+ timelines 表；总时长真实求和；
  aspect "9:16"、resolution 1080×1920、storyboard_version 溯源；
- Work Graph 投影：timeline.composition artifact（ref=topic_id 版本叠 revision）
  + derived_from video.storyboard + uses asset.visual ×N / voice.track ×N（静音镜不出边，
  空数组零事件先例）；uses 是设计文档 §4.4 的合法关系。
"""
import json
import shutil
from pathlib import Path

import pytest

from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

needs_media_bins = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("say", "afconvert", "ffmpeg", "ffprobe")),
    reason="需要本机 say/afconvert/ffmpeg/ffprobe",
)

_SHOTS = [
    {"idx": 1, "narration": "开场钩子。", "duration": 3, "visual": "黑底白字大字报"},
    {"idx": 2, "narration": "", "duration": 2, "visual": "屏幕录制滚动"},  # 空口播 → 无音轨
    {"idx": 3, "narration": "收尾。", "duration": 4, "visual": "结尾卡"},
]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir, tmp_path):
    reg = ToolRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    graph = WorkGraphStore(str(tmp_path / "wg.db"))
    load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=DurableExecutionEngine(graph),
    )
    return reg


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _db(reg):
    return reg.get("zimeiti.timeline_save").plugin_ctx.db


def _topic_with_storyboard(reg, shots=_SHOTS):
    tid = _run(reg, "zimeiti.add", {"title": "Agent 科普"}).data["id"]
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": shots})
    assert r.success
    return tid


def _seed_assets(reg, tid, version=1, voice_idxs=(1, 3), visual_idxs=(1, 2, 3), tmp=None):
    """不烧本机二进制的上游产物种子：直接写 voice_tracks/visual_cards 行 + 空文件。

    timeline 只消费「行 + 路径存在 + duration_sec」，不验文件内容——真实合成由
    voice/visual 自己的测试与端到端链覆盖。
    """
    db = _db(reg)
    for idx in voice_idxs:
        path = Path(tmp) / f"s{idx}.m4a"
        path.write_bytes(b"x")
        db.insert("voice_tracks", {
            "topic_id": tid, "storyboard_version": version, "shot_idx": idx,
            "path": str(path), "duration_sec": 1.25 * idx, "voice": "Tingting",
            "created_at": 1,
        })
    for idx in visual_idxs:
        path = Path(tmp) / f"s{idx}.png"
        path.write_bytes(b"x")
        db.insert("visual_cards", {
            "topic_id": tid, "storyboard_version": version, "shot_idx": idx,
            "path": str(path), "style": "dark", "degraded": 1, "created_at": 1,
        })


# ---------- work_outputs 声明形态 ----------


def test_timeline_work_outputs_declared_shape(env):
    reg = env
    outputs = reg.get("zimeiti.timeline_save").work_outputs
    by_kind = {}
    for spec in outputs:
        by_kind.setdefault((spec["kind"], spec.get("artifact_type") or spec.get("relation")), spec)
    artifact = by_kind[("artifact", "timeline.composition")]
    assert artifact["ref_from"] == "data.timeline_ref"
    # compose 段 acceptance 是 ["timeline", "composition"]：artifact_type 命中
    assert "timeline" in artifact["artifact_type"] and "composition" in artifact["artifact_type"]
    derived = by_kind[("edge", "derived_from")]
    assert derived["source_artifact_type"] == "timeline.composition"
    assert derived["target_artifact_type"] == "video.storyboard"
    uses_visual = by_kind[("edge", "uses")]
    assert uses_visual["target_artifact_type"] == "asset.visual"
    assert uses_visual["foreach_from"] == "data.clips"
    assert reg.get("zimeiti.timeline_save").default_risk.name == "L2_MEDIUM"


# ---------- 前置与阻塞错误 ----------


def test_timeline_save_requires_topic_and_storyboard(env):
    reg = env
    assert not _run(reg, "zimeiti.timeline_save", {"topic_id": ""}).success
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": "不存在"})
    assert not r.success and "选题" in r.error
    tid = _run(reg, "zimeiti.add", {"title": "无分镜"}).data["id"]
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert not r.success and "分镜" in r.error


def test_timeline_save_blocks_on_missing_visuals(env, tmp_path):
    """缺 visual 的镜 = 阻塞错误：success=False + 人话列出缺哪些镜；voice 缺失不阻塞。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    _seed_assets(reg, tid, visual_idxs=(1, 3), tmp=tmp_path)  # idx2 缺视觉卡
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert not r.success and "视觉" in r.error and "2" in r.error
    assert _db(reg).query("timelines", where={"topic_id": tid}) == []  # 阻塞不落库


def test_timeline_save_blocks_when_visual_file_deleted(env, tmp_path):
    """visual_cards 行在但文件被删：视同缺 visual，阻塞（不产出指向死路径的 timeline）。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    _seed_assets(reg, tid, tmp=tmp_path)
    row = _db(reg).query("visual_cards", where={"topic_id": tid, "shot_idx": 2})[0]
    Path(row["path"]).unlink()
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert not r.success and "2" in r.error


# ---------- 组装与落盘 ----------


def test_timeline_save_assembles_clips_and_versions(env, tmp_path, data_dir):
    reg = env
    tid = _topic_with_storyboard(reg)
    _seed_assets(reg, tid, tmp=tmp_path)  # voice 只有 1/3 镜（idx2 空口播无音轨）

    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert r.success, r.error
    assert r.data["version"] == 1 and r.data["storyboard_version"] == 1
    clips = r.data["clips"]
    assert [c["idx"] for c in clips] == [1, 2, 3]
    # 有音轨：duration 取音轨实测（1.25/3.75）；无音轨：回退分镜 duration 且 silent
    assert clips[0]["duration"] == 1.25 and clips[0]["silent"] is False
    assert clips[0]["audio_ref"] == f"{tid}#s1#voice" and clips[0]["audio_path"]
    assert clips[1]["duration"] == 2 and clips[1]["silent"] is True
    assert clips[1]["audio_ref"] is None and clips[1]["audio_path"] is None
    assert clips[2]["duration"] == 3.75
    for clip in clips:  # 每镜都有 visual 引用（缺了会阻塞，走不到这）
        assert clip["image_ref"] == f"{tid}#s{clip['idx']}#visual"
        assert Path(clip["image_path"]).is_file()
        assert clip["narration"] == _SHOTS[clip["idx"] - 1]["narration"]
    assert r.data["duration_sec"] == pytest.approx(1.25 + 2 + 3.75)  # 总时长真实求和
    assert r.data["missing_voice"] == [2]  # 静音镜如实列出

    # 落盘 JSON：tracks 两轨 clip refs + 画幅/分辨率/分镜版本溯源
    path = Path(r.data["path"])
    assert path.is_file() and data_dir in path.parents
    assert f"timelines/{tid}/v1.json" in str(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["topic_id"] == tid and doc["version"] == 1 and doc["storyboard_version"] == 1
    assert doc["aspect"] == "9:16" and doc["resolution"] == {"width": 1080, "height": 1920}
    assert doc["duration_sec"] == pytest.approx(7.0)
    assert [c["idx"] for c in doc["tracks"]["video"]] == [1, 2, 3]
    audio_track = doc["tracks"]["audio"]
    assert audio_track[0]["audio_ref"] == f"{tid}#s1#voice"
    assert audio_track[1]["audio_ref"] is None and audio_track[1]["silent"] is True

    rows = _db(reg).query("timelines", where={"topic_id": tid})
    assert len(rows) == 1 and rows[0]["version"] == 1
    assert rows[0]["duration_sec"] == pytest.approx(7.0) and rows[0]["clip_count"] == 3

    r2 = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})  # 重组 = 新版本
    assert r2.success and r2.data["version"] == 2
    assert f"timelines/{tid}/v2.json" in r2.data["path"]
    assert len(_db(reg).query("timelines", where={"topic_id": tid})) == 2


def test_timeline_save_voice_row_without_file_falls_back_silent(env, tmp_path):
    """voice_tracks 行在但 m4a 被删：不阻塞，按静音镜处理（回退分镜时长）。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    _seed_assets(reg, tid, tmp=tmp_path)
    row = _db(reg).query("voice_tracks", where={"topic_id": tid, "shot_idx": 1})[0]
    Path(row["path"]).unlink()
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert r.success, r.error
    assert r.data["clips"][0]["silent"] is True and r.data["clips"][0]["duration"] == 3
    assert sorted(r.data["missing_voice"]) == [1, 2]


def test_timeline_materialized_events_match_assembled_data(env, tmp_path):
    """strict materialize（与 invoker 同事务路径）：timeline.composition artifact
    + derived_from video.storyboard + uses asset.visual ×3 + uses voice.track ×2（静音镜不出边）。"""
    from yibao_brain.work_events import materialize_work_events

    reg = env
    tid = _topic_with_storyboard(reg)
    _seed_assets(reg, tid, tmp=tmp_path)
    tool = reg.get("zimeiti.timeline_save")
    params = {"topic_id": tid}
    r = tool.run(params, tool.plugin_ctx)
    assert r.success, r.error
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["artifact.upsert"] + ["artifact.edge.upsert"] * 6
    artifact = events[0]["payload"]
    assert artifact["artifact_type"] == "timeline.composition" and artifact["ref"] == tid
    assert artifact["content_ref"].endswith("v1.json")
    assert artifact["metadata"]["storyboard_version"] == 1
    edges = [e["payload"] for e in events[1:]]
    derived = next(e for e in edges if e["relation"] == "derived_from")
    assert derived["source"] == {"artifact_type": "timeline.composition", "ref": tid}
    assert derived["target"] == {"artifact_type": "video.storyboard", "ref": tid}
    uses = [e for e in edges if e["relation"] == "uses"]
    visual_targets = {e["target"]["ref"] for e in uses if e["target"]["artifact_type"] == "asset.visual"}
    voice_targets = {e["target"]["ref"] for e in uses if e["target"]["artifact_type"] == "voice.track"}
    assert visual_targets == {f"{tid}#s{i}#visual" for i in (1, 2, 3)}
    assert voice_targets == {f"{tid}#s{i}#voice" for i in (1, 3)}  # idx2 静音不出边
    assert all(e["source"] == {"artifact_type": "timeline.composition", "ref": tid} for e in uses)


# ---------- 真实上游链（skipif 保护） ----------


@needs_media_bins
def test_timeline_save_consumes_real_voice_and_visual(env):
    """真跑 voice_save + visual_card_save 后组 timeline：音轨时长为 ffprobe 实测值。"""
    reg = env
    shots = [
        {"idx": 1, "narration": "开场", "duration": 2, "visual": "大字报"},
        {"idx": 2, "narration": "收尾", "duration": 2, "visual": "结尾卡"},
    ]
    tid = _topic_with_storyboard(reg, shots)
    rv = _run(reg, "zimeiti.voice_save", {"topic_id": tid})
    assert rv.success, rv.error
    rc = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid})
    assert rc.success, rc.error
    r = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert r.success, r.error
    tracks = {t["idx"]: t for t in rv.data["tracks"]}
    for clip in r.data["clips"]:
        assert clip["duration"] == tracks[clip["idx"]]["duration_sec"]  # 真实音轨时长
        assert clip["duration"] > 0 and clip["silent"] is False
