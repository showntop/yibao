"""zimeiti 降级视觉卡（视频 workflow assets 段）：visual_card_save 生成占位字幕卡 PNG。

定位：明确标注的降级 provider——Pillow 排版文字 + ffmpeg 合成真实 1080×1920 PNG
（本机 ffmpeg 无 drawtext，见工具 docstring），不是 AI 生成图像；
degraded/provider 元数据诚实标注，可后装真图片 provider 重生成。

核心断言：
- 每镜一个 asset.visual artifact（ref=<topic>#s<idx>#visual 稳定，重生成同镜叠 revision）
  + derived_from video.shot 边；
- 真实 ffmpeg 端到端用 skipif 保护；产物用 ffprobe 验证 1080×1920 真实可读。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

needs_ffmpeg = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("ffmpeg", "ffprobe")),
    reason="需要本机 ffmpeg/ffprobe",
)

_SHOTS = [
    {"idx": 1, "narration": "你好，这是第一镜。", "duration": 3, "visual": "黑底白字：Agent 不是聊天框"},
    {"idx": 2, "narration": "它替你跑工作流", "duration": 2, "visual": "屏幕录制：工具调用链滚动，关键节点暂停"},
    {"idx": 3, "narration": "第三镜收尾。", "duration": 3, "visual": "结尾卡：关注不迷路"},
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


def _db(reg):
    return reg.get("zimeiti.visual_card_save").plugin_ctx.db


def _png_size(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    w, h = out.split(",")
    return int(w), int(h)


# ---------- work_outputs 声明形态与降级标注（不需要本机二进制） ----------


def test_visual_card_work_outputs_declared_shape(env):
    reg = env
    tool = reg.get("zimeiti.visual_card_save")
    outputs = tool.work_outputs
    by_kind = {}
    for spec in outputs:
        by_kind.setdefault((spec["kind"], spec.get("artifact_type") or spec.get("relation")), spec)
    card = by_kind[("artifact", "asset.visual")]
    assert card["foreach_from"] == "data.cards" and card["ref_from"] == "item.card_ref"
    # assets 段 acceptance 是 ["asset", "image", "visual"]：artifact_type 命中
    assert "asset" in card["artifact_type"] and "visual" in card["artifact_type"]
    # 降级标注必须进 artifact metadata（下游可据此换真 provider 重生成）
    assert "item.degraded" in card["metadata_fields"]
    assert "item.provider" in card["metadata_fields"]
    edge = by_kind[("edge", "derived_from")]
    assert edge["source_artifact_type"] == "asset.visual"
    assert edge["target_artifact_type"] == "video.shot"
    assert tool.default_risk.name == "L2_MEDIUM"
    # 描述诚实标注：降级占位、非 AI 生成图像、可重生成
    assert "降级" in tool.description and "占位" in tool.description


def test_visual_card_requires_topic_and_storyboard(env):
    reg = env
    assert not _run(reg, "zimeiti.visual_card_save", {"topic_id": ""}).success
    assert not _run(reg, "zimeiti.visual_card_save", {"topic_id": "不存在"}).success
    tid = _run(reg, "zimeiti.add", {"title": "无分镜选题"}).data["id"]
    r = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid})
    assert not r.success and "分镜" in r.error


def test_visual_card_rejects_bad_input(env):
    reg = env
    tid = _topic_with_storyboard(reg)
    assert not _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "shots": [9]}).success
    for bad in (["a"], [0], "不是 JSON"):
        assert not _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "shots": bad}).success
    r = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "style": "neon"})
    assert not r.success and "style" in r.error  # 未注册的底色主题：人话报错
    assert _db(reg).query("visual_cards", where={"topic_id": tid}) == []


def test_visual_card_reports_missing_ffmpeg(env, monkeypatch):
    reg = env
    tid = _topic_with_storyboard(reg)
    mod_globals = type(reg.get("zimeiti.visual_card_save")).run.__globals__
    real_which = mod_globals["_which"]
    monkeypatch.setitem(mod_globals, "_which",
                        lambda name: None if name == "ffmpeg" else real_which(name))
    r = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid})
    assert not r.success and "ffmpeg" in r.error


# ---------- 真实生成（skipif 保护） ----------


@needs_ffmpeg
def test_visual_card_generates_real_png(env, data_dir):
    """真跑 ffmpeg：1080×1920 PNG 真实可读（ffprobe 验证分辨率）、降级元数据齐全。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    r = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid})
    assert r.success, r.error
    assert r.data["storyboard_version"] == 1 and r.data["style"] == "dark"
    assert r.data["font"]  # 报告实测探测到的 CJK 字体路径
    cards = r.data["cards"]
    assert [c["idx"] for c in cards] == [1, 2, 3] and r.data["failed"] == []
    for card in cards:
        path = Path(card["path"])
        assert path.is_file() and path.suffix == ".png" and path.stat().st_size > 1000
        assert data_dir in path.parents
        assert f"visuals/{tid}/v1/s{card['idx']}.png" in str(path)
        assert _png_size(path) == (1080, 1920)  # ffprobe 实测分辨率
        assert card["card_ref"] == f"{tid}#s{card['idx']}#visual"
        assert card["shot_ref"] == f"{tid}#s{card['idx']}"
        assert card["degraded"] is True and card["provider"] == "pillow_ffmpeg.textcard"
    rows = _db(reg).query("visual_cards", where={"topic_id": tid}, order="shot_idx")
    assert len(rows) == 3
    assert rows[0]["degraded"] == 1 and rows[0]["style"] == "dark"
    assert rows[0]["storyboard_version"] == 1


@needs_ffmpeg
def test_visual_card_regeneration_keeps_stable_ref_and_replaces_row(env):
    """重生成同镜 = 同 artifact 新 revision（ref 不带分镜版本号，跨版本稳定），DB 行替换。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    r1 = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "shots": [1]})
    r2 = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "shots": [1], "style": "light"})
    assert r1.success and r2.success, r2.error
    assert r1.data["cards"][0]["card_ref"] == r2.data["cards"][0]["card_ref"]
    rows = _db(reg).query("visual_cards", where={"topic_id": tid, "shot_idx": 1})
    assert len(rows) == 1 and rows[0]["style"] == "light"  # 换主题重生成：行更新不堆叠


@needs_ffmpeg
def test_visual_card_single_shot_failure_isolated(env, monkeypatch):
    reg = env
    tid = _topic_with_storyboard(reg)
    mod_globals = type(reg.get("zimeiti.visual_card_save")).run.__globals__
    real_run_cmd = mod_globals["_run_cmd"]

    def fake_run_cmd(argv, **kwargs):
        # 第 2 镜的 ffmpeg 输出路径含 s2.png：模拟编码失败
        if argv and "ffmpeg" in Path(argv[0]).name and any("s2.png" in str(a) for a in argv):
            raise OSError("模拟 ffmpeg 崩溃")
        return real_run_cmd(argv, **kwargs)

    monkeypatch.setitem(mod_globals, "_run_cmd", fake_run_cmd)
    r = _run(reg, "zimeiti.visual_card_save", {"topic_id": tid, "shots": [1, 2]})
    assert r.success, r.error
    assert [c["idx"] for c in r.data["cards"]] == [1]
    assert len(r.data["failed"]) == 1 and r.data["failed"][0]["idx"] == 2


@needs_ffmpeg
def test_visual_card_materialized_events(env):
    """strict materialize：N 条 asset.visual artifact（带 degraded/provider 元数据）+ N 条 derived_from 边。"""
    from yibao_brain.work_events import materialize_work_events

    reg = env
    tid = _topic_with_storyboard(reg)
    tool = reg.get("zimeiti.visual_card_save")
    params = {"topic_id": tid, "shots": [1, 3]}
    r = tool.run(params, tool.plugin_ctx)
    assert r.success, r.error
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["artifact.upsert"] * 2 + ["artifact.edge.upsert"] * 2
    cards = [e["payload"] for e in events[:2]]
    assert [c["ref"] for c in cards] == [f"{tid}#s1#visual", f"{tid}#s3#visual"]
    assert cards[0]["metadata"]["degraded"] is True
    assert cards[0]["metadata"]["provider"] == "pillow_ffmpeg.textcard"
    assert cards[0]["content_ref"].endswith("s1.png")
    edges = [e["payload"] for e in events[2:]]
    assert edges[0]["source"] == {"artifact_type": "asset.visual", "ref": f"{tid}#s1#visual"}
    assert edges[0]["target"] == {"artifact_type": "video.shot", "ref": f"{tid}#s1"}
