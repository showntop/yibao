"""zimeiti 配音（视频 workflow voice 段）：voice_save 合成每镜口播音频。

核心断言：
- 读 topic 最新分镜 → 逐镜 say → afconvert 转 m4a → ffprobe 取真实时长（timeline 用真实时长，
  不靠估算）→ 落盘 voice/<topic_id>/v<分镜版本>/s<idx>.m4a + DB 行；
- 每镜一个 voice.track artifact（ref=<topic>#s<idx>#voice 稳定，重合成同镜叠 revision）
  + derived_from video.shot 边（foreach_from 扇出，同 storyboard_save 先例）；
- 空 narration 的镜跳过；单镜失败不拖垮整批；缺 say/afconvert/ffprobe 人话报错不抛栈；
- 真实二进制端到端用 skipif 保护（无 ffmpeg/say 的 CI 环境跳过）。
"""
import shutil
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

needs_audio_bins = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("say", "afconvert", "ffprobe")),
    reason="需要本机 say/afconvert/ffprobe",
)

_SHOTS = [
    {"idx": 1, "narration": "你好，这是第一镜。", "duration": 3, "visual": "黑底白字大字报弹出"},
    {"idx": 2, "narration": "", "duration": 2, "visual": "屏幕录制：工具链滚动"},  # 空口播 → 跳过
    {"idx": 3, "narration": "第三镜收尾。", "duration": 3, "visual": "结尾卡"},
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

    load_plugins(
        REPO_ROOT / "plugins", reg,
        durable_engine=DurableExecutionEngine(WorkGraphStore(str(tmp_path / "wg.db"))),
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _topic_with_storyboard(reg, shots=_SHOTS):
    tid = _run(reg, "zimeiti.add", {"title": "Agent 科普"}).data["id"]
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": shots})
    assert r.success
    return tid


def _db(reg, tool_id="zimeiti.voice_save"):
    return reg.get(tool_id).plugin_ctx.db


# ---------- work_outputs 声明形态（不需要本机二进制） ----------


def test_voice_work_outputs_declared_shape(env):
    """preflight 能力索引直接读类属性：voice.track artifact + derived_from 边都声明在类上。"""
    reg = env
    outputs = reg.get("zimeiti.voice_save").work_outputs
    by_kind = {}
    for spec in outputs:
        by_kind.setdefault((spec["kind"], spec.get("artifact_type") or spec.get("relation")), spec)
    track = by_kind[("artifact", "voice.track")]
    assert track["foreach_from"] == "data.tracks" and track["ref_from"] == "item.track_ref"
    # voice 段 acceptance 是 ["voice", "audio", "narration"]：artifact_type 命中
    assert "voice" in track["artifact_type"]
    edge = by_kind[("edge", "derived_from")]
    assert edge["source_artifact_type"] == "voice.track"
    assert edge["target_artifact_type"] == "video.shot"
    assert edge["foreach_from"] == "data.tracks"
    assert reg.get("zimeiti.voice_save").default_risk.name == "L2_MEDIUM"


# ---------- 非法输入 / 前置缺失（不需要本机二进制） ----------


def test_voice_save_requires_topic_and_storyboard(env):
    reg = env
    assert not _run(reg, "zimeiti.voice_save", {"topic_id": ""}).success
    r = _run(reg, "zimeiti.voice_save", {"topic_id": "不存在"})
    assert not r.success and "选题" in r.error
    tid = _run(reg, "zimeiti.add", {"title": "无分镜选题"}).data["id"]
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid})
    assert not r.success and "分镜" in r.error  # 缺分镜：人话报错，不抛栈


def test_voice_save_rejects_bad_shot_selection(env):
    reg = env
    tid = _topic_with_storyboard(reg)
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": [9]})
    assert not r.success and "9" in r.error  # 不存在的镜号，报可用范围
    for bad in (["a"], [0], [-1], [1.5], "不是 JSON", {"idx": 1}):
        assert not _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": bad}).success
    # 非法输入不落库
    assert _db(reg).query("voice_tracks", where={"topic_id": tid}) == []


def test_voice_save_reports_missing_binaries(env, monkeypatch):
    """say/afconvert/ffprobe 任一缺失：success=False + 人话（不抛栈）。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    mod_globals = type(reg.get("zimeiti.voice_save")).run.__globals__
    real_which = mod_globals["_which"]
    monkeypatch.setitem(mod_globals, "_which",
                        lambda name: None if name == "afconvert" else real_which(name))
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid})
    assert not r.success and "afconvert" in r.error


# ---------- 真实合成（skipif 保护） ----------


@needs_audio_bins
def test_voice_save_synthesizes_real_audio(env, data_dir):
    """真跑 say→afconvert→ffprobe：m4a 真实存在、真实时长>0、空口播镜跳过。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid})
    assert r.success, r.error
    assert r.data["storyboard_version"] == 1
    tracks = r.data["tracks"]
    assert [t["idx"] for t in tracks] == [1, 3]  # idx2 空口播跳过
    assert r.data["skipped"] == [{"idx": 2, "reason": "narration 为空"}]
    assert r.data["failed"] == []
    assert r.data["voice"]  # 实际使用的语音名（zh_CN 或调用方指定）
    for track in tracks:
        path = Path(track["path"])
        assert path.is_file() and path.suffix == ".m4a" and path.stat().st_size > 0
        assert data_dir in path.parents  # 落用户数据根，不污染仓库
        assert f"voice/{tid}/v1/s{track['idx']}.m4a" in str(path)
        assert track["duration_sec"] > 0  # ffprobe 真实时长，非估算
        assert track["track_ref"] == f"{tid}#s{track['idx']}#voice"
        assert track["shot_ref"] == f"{tid}#s{track['idx']}"
    rows = _db(reg).query("voice_tracks", where={"topic_id": tid}, order="shot_idx")
    assert len(rows) == 2
    assert rows[0]["storyboard_version"] == 1 and rows[0]["duration_sec"] > 0
    assert rows[0]["voice"] == r.data["voice"]


@needs_audio_bins
def test_voice_save_resynthesis_keeps_stable_ref_and_replaces_row(env):
    """重合成同镜 = 同 artifact 新 revision（ref 稳定），DB 行按（选题,分镜版,镜号）替换不堆叠。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    r1 = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": [1]})
    r2 = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": [1]})
    assert r1.success and r2.success
    assert r1.data["tracks"][0]["track_ref"] == r2.data["tracks"][0]["track_ref"]
    rows = _db(reg).query("voice_tracks", where={"topic_id": tid, "shot_idx": 1})
    assert len(rows) == 1


@needs_audio_bins
def test_voice_save_shot_filter_and_json_string(env):
    """shots 只合成指定镜；LLM 常给 JSON 字符串数组（同 storyboard_save 防御姿势）。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": "[3]"})
    assert r.success and [t["idx"] for t in r.data["tracks"]] == [3]
    rows = _db(reg).query("voice_tracks", where={"topic_id": tid})
    assert len(rows) == 1 and rows[0]["shot_idx"] == 3


@needs_audio_bins
def test_voice_save_single_shot_failure_isolated(env, monkeypatch):
    """单镜合成失败不拖垮整批：结果里列 failed shots，其余镜正常落库。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    mod_globals = type(reg.get("zimeiti.voice_save")).run.__globals__
    real_run_cmd = mod_globals["_run_cmd"]

    def fake_run_cmd(argv, **kwargs):
        # 第 3 镜的 say 命令（口播文本在 argv 里）模拟失败
        if argv and argv[0].endswith("say") and any("第三镜" in str(a) for a in argv):
            raise OSError("模拟 say 崩溃")
        return real_run_cmd(argv, **kwargs)

    monkeypatch.setitem(mod_globals, "_run_cmd", fake_run_cmd)
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": [1, 3]})
    assert r.success, r.error  # 整批仍成功
    assert [t["idx"] for t in r.data["tracks"]] == [1]
    assert len(r.data["failed"]) == 1 and r.data["failed"][0]["idx"] == 3
    assert _db(reg).query("voice_tracks", where={"topic_id": tid})[0]["shot_idx"] == 1


@needs_audio_bins
def test_voice_save_all_failed_returns_error(env, monkeypatch):
    """整批零产出（全失败）不算成功：success=False + 人话，不产生空 artifact 事件。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    mod_globals = type(reg.get("zimeiti.voice_save")).run.__globals__

    def fake_run_cmd(argv, **kwargs):
        raise OSError("模拟全部失败")

    monkeypatch.setitem(mod_globals, "_run_cmd", fake_run_cmd)
    r = _run(reg, "zimeiti.voice_save", {"topic_id": tid, "shots": [1]})
    assert not r.success and r.error


@needs_audio_bins
def test_voice_materialized_events_match_synthesized_data(env):
    """用真实 run 结果过 materialize（strict，与 invoker 同事务路径）：
    N 条 voice.track artifact.upsert + N 条 derived_from 边；ref 形态 <topic>#s<idx>#voice。"""
    from yibao_brain.work_events import materialize_work_events

    reg = env
    tid = _topic_with_storyboard(reg)
    tool = reg.get("zimeiti.voice_save")
    params = {"topic_id": tid, "shots": [1, 3]}
    r = tool.run(params, tool.plugin_ctx)
    assert r.success, r.error
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["artifact.upsert"] * 2 + ["artifact.edge.upsert"] * 2
    tracks = [e["payload"] for e in events[:2]]
    assert [t["ref"] for t in tracks] == [f"{tid}#s1#voice", f"{tid}#s3#voice"]
    assert all(t["artifact_type"] == "voice.track" for t in tracks)
    assert tracks[0]["content_ref"].endswith("s1.m4a")
    assert tracks[0]["metadata"]["duration_sec"] > 0
    edges = [e["payload"] for e in events[2:]]
    assert all(e["relation"] == "derived_from" for e in edges)
    assert edges[0]["source"] == {"artifact_type": "voice.track", "ref": f"{tid}#s1#voice"}
    assert edges[0]["target"] == {"artifact_type": "video.shot", "ref": f"{tid}#s1"}
