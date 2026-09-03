"""zimeiti 真图 provider（visual_generate）：视频 workflow assets 段的 AI 生图能力。

核心断言：
- 与占位卡同一 artifact 合同：asset.visual、ref 稳定、derived_from video.shot 边；
- 真图非降级（degraded=False）：assets 段预检从「降级」翻「满血」；
- 无图像 API key 时 tool 不注册（假能力不得骗过预检）；单镜失败不拖垮整批；
- 产物经 Pillow 居中裁 9:16 规整 1080×1920 PNG。
"""
import base64
import io
import json
from pathlib import Path

import pytest

from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

_SHOTS = [
    {"idx": 1, "narration": "开场钩子。", "duration": 3, "visual": "发光大脑与三个工牌图标"},
    {"idx": 2, "narration": "中段。", "duration": 2, "visual": "机械手流水线"},
]


def _tiny_png_b64() -> str:
    """造一张 64×36 的真 PNG（Pillow），供假 API 返回 b64_json。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (20, 40, 80)).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir, tmp_path, monkeypatch):
    """加载真实插件目录；配假 key 让 visual_generate 注册（无 key 时它不注册——见末例）。"""
    monkeypatch.setenv("YIBAO_IMAGE_API_KEY", "test-image-key")
    reg = ToolRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=DurableExecutionEngine(WorkGraphStore(str(tmp_path / "wg.db"))),
    )
    return reg


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _tool_mod(reg):
    """插件模块不挂 sys.modules：经 run.__globals__ 拿模块命名空间打网络补丁。"""
    return type(reg.get("zimeiti.visual_generate")).run.__globals__


def _topic_with_storyboard(reg, shots=_SHOTS):
    tid = _run(reg, "zimeiti.add", {"title": "Agent 科普"}).data["id"]
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": shots})
    assert r.success
    return tid


def _fake_api(monkeypatch, reg, fail_idxs=()):
    """把 visual_generate 的网络接缝换成假实现：b64 形态回一张真 PNG；fail_idxs 的镜抛错。"""
    g = _tool_mod(reg)

    def fake_post(url, payload, api_key):
        if any(s in payload.get("prompt", "") for s in fail_idxs):
            raise RuntimeError("模拟生成失败")
        return {"data": [{"b64_json": _tiny_png_b64()}]}

    monkeypatch.setitem(g, "_post_json", fake_post)


def test_visual_generate_real_images(env, data_dir, monkeypatch):
    """真图链路：假 API 回 PNG → 居中裁 9:16 → 1080×1920 落盘；degraded=0、provider=模型名。"""
    reg = env
    _fake_api(monkeypatch, reg)
    tid = _topic_with_storyboard(reg)
    r = _run(reg, "zimeiti.visual_generate", {"topic_id": tid})
    assert r.success, r.error
    cards = r.data["cards"]
    assert [c["idx"] for c in cards] == [1, 2]
    assert r.data["degraded"] is False and r.data["provider"]
    for card in cards:
        assert card["degraded"] is False
        path = Path(card["path"])
        assert path.is_file() and data_dir in path.parents
        from PIL import Image

        with Image.open(path) as img:
            assert img.size == (1080, 1920)  # 规整到竖屏画布
    # 落库：degraded=0（与占位卡的 1 区分）
    db = reg.get("zimeiti.visual_generate").plugin_ctx.db
    rows = db.query("visual_cards", where={"topic_id": tid})
    assert len(rows) == 2 and all(int(row["degraded"]) == 0 for row in rows)


def test_visual_generate_single_shot_failure_isolated(env, monkeypatch):
    """单镜 API 失败不拖垮整批：failed 列出，其余镜照常落库。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    _fake_api(monkeypatch, reg, fail_idxs=("机械手",))  # idx2 的画面描述含「机械手」
    r = _run(reg, "zimeiti.visual_generate", {"topic_id": tid})
    assert r.success, r.error
    assert [c["idx"] for c in r.data["cards"]] == [1]
    assert len(r.data["failed"]) == 1 and r.data["failed"][0]["idx"] == 2


def test_visual_generate_materialized_events(env, monkeypatch):
    """strict materialize：asset.visual artifact ×2 + derived_from video.shot 边 ×2。"""
    from yibao_brain.work_events import materialize_work_events

    reg = env
    _fake_api(monkeypatch, reg)
    tid = _topic_with_storyboard(reg)
    tool = reg.get("zimeiti.visual_generate")
    params = {"topic_id": tid}
    r = tool.run(params, tool.plugin_ctx)
    assert r.success, r.error
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["artifact.upsert"] * 2 + ["artifact.edge.upsert"] * 2
    assert all(e["payload"]["artifact_type"] == "asset.visual" for e in events[:2])
    assert events[0]["payload"]["metadata"]["degraded"] is False


def test_visual_generate_prefers_b64_over_url(env, monkeypatch):
    """返回形态兼容：b64_json 优先；只有 url 时走下载通道。"""
    reg = env
    tid = _topic_with_storyboard(reg)
    g = _tool_mod(reg)
    png = _tiny_png_b64()
    monkeypatch.setitem(g, "_post_json", lambda *a, **kw: {"data": [{"url": "https://x.test/a.png"}]})
    monkeypatch.setitem(g, "_fetch_bytes", lambda url: base64.b64decode(png))
    r = _run(reg, "zimeiti.visual_generate", {"topic_id": tid, "shots": [1]})
    assert r.success, r.error
    assert Path(r.data["cards"][0]["path"]).is_file()


def test_visual_generate_not_registered_without_key(tmp_path, monkeypatch):
    """无图像 key（含回退链全空）→ tool 不注册：预检的 assets 段保持「降级」真相。"""
    for name in ("YIBAO_IMAGE_API_KEY", "YIBAO_VISION_API_KEY", "YIBAO_GLM_API_KEY",
                 "YIBAO_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path / "data"))
    reg = ToolRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=DurableExecutionEngine(WorkGraphStore(str(tmp_path / "wg.db"))),
    )
    ids = [t.id for t in reg.list()]
    assert "zimeiti.visual_generate" not in ids
    assert "zimeiti.visual_card_save" in ids  # 降级占位卡仍在（兜底能力）
