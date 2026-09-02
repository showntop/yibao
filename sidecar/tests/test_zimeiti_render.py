"""zimeiti 渲染（视频 workflow deliver 段）：render_save 把 timeline 渲成真 MP4。

核心断言：
- 读最新 timeline → 逐片段渲染（图片 -loop 成 duration 秒；有音轨 mux m4a，无音轨
  anullsrc 静音轨补齐，保证 concat 后音轨连续）→ concat → 1080×1920 H.264 + AAC
  （yuv420p 保兼容）→ 落盘 renders/<topic_id>/v<N>.mp4 + renders 表；
- 验收即交付物：ffprobe 实测 resolution/duration 写回，data.acceptance =
  {aspect_ok, resolution, duration_sec, timeline_version, ...}；
- 长任务走 DurableExecution（durable capability 第一个真实消费者）：每片段一个
  checkpoint（断点续跑）、片段间是取消安全点；无 workspace 上下文（未立项会话）→
  同步退路照样出 MP4（只是没有恢复语义，结果如实标 durable: False）；
- Work Graph 投影：video.render artifact（ref=<topic>#render）+ rendered_from
  timeline.composition 边；
- ffmpeg/ffprobe 缺失 → success=False 人话；真实渲染用 skipif 保护。
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

needs_ffmpeg = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("ffmpeg", "ffprobe")),
    reason="需要本机 ffmpeg/ffprobe",
)

_SHOTS = [
    {"idx": 1, "narration": "开场。", "duration": 2, "visual": "大字报"},
    {"idx": 2, "narration": "", "duration": 1.5, "visual": "录屏滚动"},  # 静音镜
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
    engine = DurableExecutionEngine(graph)
    load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=engine,
    )
    yield reg, engine, graph
    engine.shutdown()
    graph.close()


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _db(reg):
    return reg.get("zimeiti.render_save").plugin_ctx.db


def _mod(reg):
    """render_save 模块 globals（可测试性接缝与 handler 都挂在这里，同 voice_save 先例）。"""
    return type(reg.get("zimeiti.render_save")).run.__globals__


def _make_png(path, color=(17, 20, 24)):
    from PIL import Image

    Image.new("RGB", (1080, 1920), color).save(path)


def _make_m4a(path, seconds=1.0):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000", "-t", str(seconds), "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )


def _topic_with_timeline(reg, tmp_path, shots=_SHOTS):
    """真 storyboard + 种子音轨/视觉卡 + 真 timeline_save（PNG/m4a 是真文件）。"""
    tid = _run(reg, "zimeiti.add", {"title": "Agent 科普"}).data["id"]
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": shots})
    assert r.success
    db = _db(reg)
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    for shot in shots:
        idx = shot["idx"]
        png = assets / f"s{idx}.png"
        _make_png(png)
        db.insert("visual_cards", {
            "topic_id": tid, "storyboard_version": 1, "shot_idx": idx,
            "path": str(png), "style": "dark", "degraded": 1, "created_at": 1,
        })
        if shot["narration"]:
            m4a = assets / f"s{idx}.m4a"
            _make_m4a(m4a, seconds=1.0)
            db.insert("voice_tracks", {
                "topic_id": tid, "storyboard_version": 1, "shot_idx": idx,
                "path": str(m4a), "duration_sec": 1.0, "voice": "test", "created_at": 1,
            })
    tl = _run(reg, "zimeiti.timeline_save", {"topic_id": tid})
    assert tl.success, tl.error
    return tid, tl.data


def _ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    doc = json.loads(out)
    return {
        "width": int(doc["streams"][0]["width"]),
        "height": int(doc["streams"][0]["height"]),
        "duration": float(doc["format"]["duration"]),
    }


def _request(tmp_path, clips=None):
    assets = tmp_path / "req_assets"
    assets.mkdir(exist_ok=True)
    if clips is None:
        png1, png2, m4a1 = assets / "a.png", assets / "b.png", assets / "a.m4a"
        _make_png(png1)
        _make_png(png2, color=(14, 42, 71))
        _make_m4a(m4a1, seconds=1.0)
        clips = [
            {"idx": 1, "image_path": str(png1), "audio_path": str(m4a1),
             "duration": 1.0, "silent": False},
            {"idx": 2, "image_path": str(png2), "audio_path": None,
             "duration": 1.5, "silent": True},
        ]
    return {
        "topic_id": "t1", "timeline_version": 1, "storyboard_version": 1,
        "out_path": str(tmp_path / "renders" / "v1.mp4"),
        "work_dir": str(tmp_path / "renders" / "v1.work"),
        "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
        "clips": clips,
    }


# ---------- work_outputs 声明形态 ----------


def test_render_work_outputs_declared_shape(env):
    reg, _, _ = env
    outputs = reg.get("zimeiti.render_save").work_outputs
    by_kind = {}
    for spec in outputs:
        by_kind.setdefault((spec["kind"], spec.get("artifact_type") or spec.get("relation")), spec)
    artifact = by_kind[("artifact", "video.render")]
    assert artifact["ref_from"] == "data.render_ref"
    # deliver 段 acceptance 是 ["render", "export", "published"]：artifact_type 命中
    assert "render" in artifact["artifact_type"]
    edge = by_kind[("edge", "rendered_from")]
    assert edge["source_artifact_type"] == "video.render"
    assert edge["target_artifact_type"] == "timeline.composition"
    assert reg.get("zimeiti.render_save").default_risk.name == "L2_MEDIUM"


# ---------- 前置与依赖缺失 ----------


def test_render_save_requires_topic_and_timeline(env):
    reg, _, _ = env
    assert not _run(reg, "zimeiti.render_save", {"topic_id": ""}).success
    r = _run(reg, "zimeiti.render_save", {"topic_id": "不存在"})
    assert not r.success and "选题" in r.error
    tid = _run(reg, "zimeiti.add", {"title": "无时间线"}).data["id"]
    r = _run(reg, "zimeiti.render_save", {"topic_id": tid})
    assert not r.success and "时间线" in r.error


@needs_ffmpeg
def test_render_save_reports_missing_binaries(env, tmp_path, monkeypatch):
    reg, _, _ = env
    tid, _ = _topic_with_timeline(reg, tmp_path)
    mod = _mod(reg)
    real_which = mod["_which"]
    monkeypatch.setitem(mod, "_which", lambda name: None if name == "ffmpeg" else real_which(name))
    r = _run(reg, "zimeiti.render_save", {"topic_id": tid})
    assert not r.success and "ffmpeg" in r.error


# ---------- 真实渲染：同步退路（无 workspace 上下文） ----------


@needs_ffmpeg
def test_render_save_inline_produces_real_mp4(env, tmp_path, data_dir):
    """直接 tool.run（ctx.meta 无 workspace_id）→ 同步退路：真 MP4 + ffprobe 验收 +
    renders 行；结果如实标 durable: False。"""
    reg, _, _ = env
    tid, tl = _topic_with_timeline(reg, tmp_path)
    r = _run(reg, "zimeiti.render_save", {"topic_id": tid})
    assert r.success, r.error
    assert r.data["durable"] is False and r.data["execution_id"] == ""
    assert r.data["render_ref"] == f"{tid}#render"
    assert r.data["version"] == 1 and r.data["timeline_version"] == 1
    path = Path(r.data["path"])
    assert path.is_file() and path.suffix == ".mp4" and path.stat().st_size > 0
    assert data_dir in path.parents
    assert f"renders/{tid}/v1.mp4" in str(path)

    probed = _ffprobe(path)
    assert (probed["width"], probed["height"]) == (1080, 1920)
    expected = tl["duration_sec"]  # 1.0（音轨实测）+ 1.5（静音镜分镜时长）
    assert probed["duration"] == pytest.approx(expected, abs=1.0)
    acceptance = r.data["acceptance"]
    assert acceptance["aspect_ok"] is True and acceptance["resolution"] == "1080x1920"
    assert acceptance["duration_sec"] == pytest.approx(expected, abs=1.0)
    assert acceptance["timeline_version"] == 1 and acceptance["duration_ok"] is True

    rows = _db(reg).query("renders", where={"topic_id": tid})
    assert len(rows) == 1 and rows[0]["version"] == 1
    assert rows[0]["resolution"] == "1080x1920"
    assert rows[0]["duration_sec"] == pytest.approx(expected, abs=1.0)
    assert rows[0]["timeline_version"] == 1 and rows[0]["storyboard_version"] == 1

    r2 = _run(reg, "zimeiti.render_save", {"topic_id": tid})  # 重渲 = 新版本
    assert r2.success and r2.data["version"] == 2 and f"v2.mp4" in r2.data["path"]


@needs_ffmpeg
def test_render_materialized_events_match_rendered_data(env, tmp_path):
    """strict materialize：video.render artifact（ref=<topic>#render）+ rendered_from
    timeline.composition 边。"""
    from yibao_brain.work_events import materialize_work_events

    reg, _, _ = env
    tid, _ = _topic_with_timeline(reg, tmp_path)
    tool = reg.get("zimeiti.render_save")
    params = {"topic_id": tid}
    r = tool.run(params, tool.plugin_ctx)
    assert r.success, r.error
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    assert [e["event_type"] for e in events] == ["artifact.upsert", "artifact.edge.upsert"]
    artifact = events[0]["payload"]
    assert artifact["artifact_type"] == "video.render" and artifact["ref"] == f"{tid}#render"
    assert artifact["content_ref"].endswith("v1.mp4")
    assert artifact["metadata"]["timeline_version"] == 1
    edge = events[1]["payload"]
    assert edge["relation"] == "rendered_from"
    assert edge["source"] == {"artifact_type": "video.render", "ref": f"{tid}#render"}
    assert edge["target"] == {"artifact_type": "timeline.composition", "ref": tid}


# ---------- durable execution：checkpoint 推进 / 取消安全点 / interrupted 续跑 ----------


@needs_ffmpeg
def test_durable_render_checkpoints_each_segment(env, tmp_path):
    """引擎直跑（provider 在插件加载时已注册）：每片段一个 checkpoint，终态 result
    带实测 resolution/duration，StageInstance 同步拿到进度。"""
    reg, engine, graph = env
    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "ws"))
    request = _request(tmp_path)
    execution = engine.start(
        workspace_id="video", stage_id="deliver", capability_id="video.render",
        provider_candidates=["zimeiti.ffmpeg"], request=request,
        idempotency_key="render:t1:test", cancel_mode="checkpoint",
    )
    done = engine.wait(execution["id"], timeout=60)
    assert done["status"] == "completed", done.get("error")
    assert done["checkpoint"]["segments_done"] == [0, 1]
    assert done["progress"] == 1.0
    result = done["result"]
    assert result["resolution"] == "1080x1920" and result["duration_sec"] > 0
    probed = _ffprobe(result["path"])
    assert (probed["width"], probed["height"]) == (1080, 1920)
    stage = next(s for s in graph.workspace_view("video")["workflow_run"]["stages"]
                 if s["id"] == "deliver")
    assert stage["checkpoint"]["segments_done"] == [0, 1]
    assert done["attempt"] == 1


@needs_ffmpeg
def test_durable_render_cancel_at_safe_point(env, tmp_path, monkeypatch):
    """取消是协作式的：片段间 checkpoint 是安全点——取消后状态 cancelled、
    已完成片段的 checkpoint 保留（可 resume）。"""
    reg, engine, graph = env
    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "ws"))
    request = _request(tmp_path)
    mod = _mod(reg)
    real_run_cmd = mod["_run_cmd"]
    entered = threading.Event()
    release = threading.Event()

    def fake_run_cmd(argv, **kwargs):
        # 第一段渲染命令内暂停：让测试在「恰好完成 0 段、第 1 段进行中」时取消
        if "seg_000" in " ".join(str(a) for a in argv):
            entered.set()
            release.wait(timeout=10)
        return real_run_cmd(argv, **kwargs)

    monkeypatch.setitem(mod, "_run_cmd", fake_run_cmd)
    execution = engine.start(
        workspace_id="video", stage_id="deliver", capability_id="video.render",
        provider_candidates=["zimeiti.ffmpeg"], request=request,
        idempotency_key="render:t1:cancel", cancel_mode="checkpoint",
    )
    assert entered.wait(timeout=10)
    assert engine.cancel(execution["id"])
    release.set()
    done = engine.wait(execution["id"], timeout=60)
    assert done["status"] == "cancelled"
    # 取消发生在第 1 段渲染中：checkpoint 为空（第 1 段未落 checkpoint），安全点语义成立
    assert done["checkpoint"].get("segments_done", []) == []
    assert done["progress"] < 1.0


@needs_ffmpeg
def test_durable_render_resume_from_checkpoint_after_interrupt(env, tmp_path, monkeypatch):
    """进程退出 → interrupted：重开库 + 新引擎 recover，从最后 checkpoint 续跑——
    已完成的片段不重渲（数 ffmpeg 调用），最终成片可用。"""
    reg, _, _ = env
    path = tmp_path / "wg_restart.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "ws"))
    request = _request(tmp_path)
    # 模拟「上一进程渲完第 1 段、checkpoint 后死掉」：真渲 seg_000 + 手写 checkpoint
    mod = _mod(reg)
    work_dir = Path(request["work_dir"])
    work_dir.mkdir(parents=True, exist_ok=True)
    mod["_render_segment"](request["ffmpeg"], request["clips"][0], work_dir / "seg_000.mp4")
    execution = graph.create_durable_execution(
        workspace_id="video", stage_id="deliver", capability_id="video.render",
        provider_candidates=["zimeiti.ffmpeg"], request=request,
        idempotency_key="render:t1:resume", cancel_mode="checkpoint",
    )
    claimed = graph.claim_durable_execution(execution["id"], "zimeiti.ffmpeg")
    graph.checkpoint_durable_execution(
        execution["id"], {"segments_done": [0]}, progress=0.5,
        expected_version=claimed["checkpoint_version"],
    )
    graph.close()  # 进程退出：running 执行下次开库时标 interrupted

    reopened = WorkGraphStore(str(path))
    try:
        assert reopened.durable_execution_view(execution["id"])["status"] == "interrupted"
        engine2 = DurableExecutionEngine(reopened)
        calls: list[str] = []
        real_run_cmd = mod["_run_cmd"]

        def counting_run_cmd(argv, **kwargs):
            calls.append(" ".join(str(a) for a in argv))
            return real_run_cmd(argv, **kwargs)

        monkeypatch.setitem(mod, "_run_cmd", counting_run_cmd)
        engine2.register_provider(
            capability_id="video.render", provider_id="zimeiti.ffmpeg",
            handler=mod["_durable_render_handler"],
        )
        assert engine2.recover() == [execution["id"]]
        done = engine2.wait(execution["id"], timeout=60)
        try:
            assert done["status"] == "completed", done.get("error")
            assert done["attempt"] == 2
            assert [a["status"] for a in done["attempts"]] == ["interrupted", "completed"]
            # seg_000 已在 checkpoint 里：不重渲（ffmpeg 渲染命令只跑 seg_001）
            seg_renders = [c for c in calls if "seg_000" in c]
            assert seg_renders == []
            probed = _ffprobe(done["result"]["path"])
            assert (probed["width"], probed["height"]) == (1080, 1920)
        finally:
            engine2.shutdown()
    finally:
        reopened.close()


# ---------- durable 工具路径：经 ctx.durable 走引擎 ----------


@needs_ffmpeg
def test_render_save_via_durable_engine_when_workspace_known(env, tmp_path, monkeypatch):
    """ctx.meta 带 workspace_id（invoker 注入的归属）→ 走引擎：execution 完成、
    结果标 durable: True + execution_id 溯源；workspace 非视频流（无 deliver 段）→
    同步退路照样出片。"""
    reg, engine, graph = env
    from dataclasses import replace

    graph.create_workspace("video", "Agent 概念科普视频", str(tmp_path / "ws"))
    graph.register_workflow({  # 没有 deliver 段的流程：引擎拒收 → 同步退路
        "id": "demo.nodeliver", "version": "1", "domain": "demo", "label": "无交付段",
        "matches": ["无交付段"],
        "stages": [{"id": "only", "label": "唯一段", "depends_on": [],
                    "acceptance": [{"artifact_patterns": ["topic"]}]}],
    }, source_plugin="demo")
    graph.create_workspace("general", "无交付段项目", str(tmp_path / "ws2"))
    tid, _ = _topic_with_timeline(reg, tmp_path)
    tool = reg.get("zimeiti.render_save")
    ctx = replace(tool.plugin_ctx, meta={"workspace_id": "video"})
    r = tool.run({"topic_id": tid}, ctx)
    assert r.success, r.error
    assert r.data["durable"] is True and r.data["execution_id"]
    view = graph.durable_execution_view(r.data["execution_id"])
    assert view["status"] == "completed" and view["capability_id"] == "video.render"
    assert view["stage_id"] == "deliver"
    row = _db(reg).query("renders", where={"topic_id": tid})[0]
    assert row["execution_id"] == r.data["execution_id"]

    ctx2 = replace(tool.plugin_ctx, meta={"workspace_id": "general"})
    r2 = tool.run({"topic_id": tid}, ctx2)
    assert r2.success, r2.error  # 该 workflow 没有 deliver 段 → 引擎拒收 → 同步退路
    assert r2.data["durable"] is False
