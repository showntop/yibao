"""zimeiti 分镜（视频 workflow S2）：storyboard_save / storyboard_get + Work Graph 投影。

核心断言（对应产品审计「7 个镜头被压进一条 material.content 文本」）：
- 分镜落盘版本化（镜像 article_save：blob content-ref + DB 行 + 版本递增 + 20 版治理）；
- shot 是一等对象：每个 shot 一个 video.shot artifact（ref=<topic>#s<idx> 跨版本稳定），
  storyboard contains shot ×N、storyboard derived_from video.script（有稿才产）；
- 动态 N 个 shot artifact 走 work_output foreach_from 扇出（见 test_work_events.py）。
"""
import json
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, load_plugins
from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.tools import ToolRegistry
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]

_SHOTS = [
    {"idx": 1, "narration": "你以为 Agent 只是个聊天框？", "duration": 3, "visual": "黑底白字大字报弹出"},
    {"idx": 2, "narration": "它其实在替你跑一整套工作流", "duration": 4.5, "visual": "屏幕录制：工具调用链滚动"},
    {"idx": 3, "narration": "关键节点，它停下来等你点头", "duration": 3, "visual": "闸门卡弹出特写"},
]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir, tmp_path):
    reg = ToolRegistry()
    mem = FakeMemory()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        durable_engine=DurableExecutionEngine(WorkGraphStore(str(tmp_path / "wg.db"))),
        memory=mem, http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg, mem, results


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


def _topic(reg, title="Agent 科普"):
    return _run(reg, "zimeiti.add", {"title": title}).data["id"]


# ---------- storyboard_save：合法保存 ----------


def test_storyboard_save_versions_blob_and_row(env, data_dir):
    reg, _, _ = env
    tid = _topic(reg)

    r1 = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS, "note": "初版分镜"})
    assert r1.success and r1.data["version"] == 1 and r1.data["shot_count"] == 3
    assert r1.panel == "zimeiti:detail" and reg.get("zimeiti.storyboard_save").refresh == "zimeiti.get"
    assert r1.data["content_ref"].startswith("blob://sha256/")
    path = Path(r1.data["path"])
    assert path.is_file() and data_dir in path.parents  # 落用户数据根，不污染仓库
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["topic_id"] == tid and doc["version"] == 1 and doc["note"] == "初版分镜"
    assert [s["idx"] for s in doc["shots"]] == [1, 2, 3]
    assert doc["shots"][1]["duration"] == 4.5

    db = reg.get("zimeiti.storyboard_save").plugin_ctx.db
    rows = db.query("storyboards", where={"topic_id": tid})
    assert len(rows) == 1 and rows[0]["version"] == 1 and rows[0]["shot_count"] == 3
    assert rows[0]["content_path"] == r1.data["content_ref"]

    r2 = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS[:2], "note": "砍到两镜"})
    assert r2.success and r2.data["version"] == 2 and r2.data["shot_count"] == 2
    rows = db.query("storyboards", where={"topic_id": tid}, order="version DESC")
    assert [row["version"] for row in rows] == [2, 1]


def test_storyboard_save_accepts_json_string_shots(env):
    """LLM 常把数组参数给成 JSON 字符串：防御解析，与 add 的 hkrr 同姿势。"""
    reg, _, _ = env
    tid = _topic(reg)
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": json.dumps(_SHOTS, ensure_ascii=False)})
    assert r.success and r.data["shot_count"] == 3


def test_storyboard_save_keeps_design_doc_optional_fields(env):
    """设计文档（2026-08-30 §4）shot 字段原样留存：scene/shot_size/camera_move/narrative_purpose/dialogue_ref。"""
    reg, _, _ = env
    tid = _topic(reg)
    shots = [{
        "idx": 1, "narration": "开场", "duration": 3, "visual": "大字报",
        "scene": "S01", "shot_size": "特写", "camera_move": "推",
        "narrative_purpose": "钩子：制造反差", "dialogue_ref": "L1",
    }]
    r = _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": shots})
    assert r.success
    doc = json.loads(Path(r.data["path"]).read_text(encoding="utf-8"))
    shot = doc["shots"][0]
    assert shot["shot_size"] == "特写" and shot["camera_move"] == "推"
    assert shot["narrative_purpose"] == "钩子：制造反差" and shot["dialogue_ref"] == "L1"


def test_storyboard_save_versions_are_per_topic(env):
    """归属锚点是 topic：两个选题各自独立版本栈（写路径归属由 topic 决定，不需 scope 参数）。"""
    reg, _, _ = env
    ta, tb = _topic(reg, "A"), _topic(reg, "B")
    _run(reg, "zimeiti.storyboard_save", {"topic_id": ta, "shots": _SHOTS})
    _run(reg, "zimeiti.storyboard_save", {"topic_id": ta, "shots": _SHOTS})
    rb = _run(reg, "zimeiti.storyboard_save", {"topic_id": tb, "shots": _SHOTS})
    assert rb.data["version"] == 1
    db = reg.get("zimeiti.storyboard_save").plugin_ctx.db
    assert len(db.query("storyboards", where={"topic_id": ta})) == 2
    assert len(db.query("storyboards", where={"topic_id": tb})) == 1


def test_storyboard_save_prunes_old_versions(env):
    """版本治理同 articles：保留最近 20 版关系行，blob 内容由 Host 延迟 GC。"""
    reg, _, _ = env
    tid = _topic(reg)
    for i in range(25):
        assert _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS[:1]}).success
    db = reg.get("zimeiti.storyboard_save").plugin_ctx.db
    rows = db.query("storyboards", where={"topic_id": tid}, order="version DESC")
    assert len(rows) == 20 and rows[0]["version"] == 25 and rows[-1]["version"] == 6


# ---------- storyboard_save：非法输入（success=False + 人话，不抛栈） ----------


def test_storyboard_save_rejects_bad_input(env):
    reg, _, _ = env
    tid = _topic(reg)
    good = _SHOTS[:1]

    def bad(params):
        r = _run(reg, "zimeiti.storyboard_save", params)
        assert not r.success and r.error
        return r

    bad({"topic_id": "", "shots": good})                       # 缺 topic_id
    bad({"topic_id": "不存在", "shots": good})                 # 选题不存在
    bad({"topic_id": tid, "shots": []})                        # 空分镜
    bad({"topic_id": tid, "shots": "不是 JSON"})               # 坏 JSON 字符串
    bad({"topic_id": tid, "shots": {"idx": 1}})                # 非数组
    bad({"topic_id": tid, "shots": ["旁白"]})                  # 元素非对象
    bad({"topic_id": tid, "shots": [{"narration": "x", "duration": 3, "visual": "v"}]})      # 缺 idx
    bad({"topic_id": tid, "shots": [{"idx": 1, "duration": 3, "visual": "v"}]})              # 缺 narration
    bad({"topic_id": tid, "shots": [{"idx": 1, "narration": "x", "duration": 3}]})           # 缺 visual
    bad({"topic_id": tid, "shots": [{"idx": 1, "narration": "x", "visual": "v"}]})           # 缺 duration
    bad({"topic_id": tid, "shots": [{"idx": 1, "narration": "x", "duration": 3, "visual": "  "}]})  # 空 visual
    for bad_idx in (0, -1, 1.5, "1", True):                    # idx 非正整数
        bad({"topic_id": tid, "shots": [{"idx": bad_idx, "narration": "x", "duration": 3, "visual": "v"}]})
    for bad_dur in (0, -2, "3", True, None):                   # duration 非正数
        bad({"topic_id": tid, "shots": [{"idx": 1, "narration": "x", "duration": bad_dur, "visual": "v"}]})
    dup = [{"idx": 1, "narration": "a", "duration": 3, "visual": "v"},
           {"idx": 1, "narration": "b", "duration": 3, "visual": "v"}]
    r = bad({"topic_id": tid, "shots": dup})                   # idx 重复
    assert "递增" in r.error or "唯一" in r.error
    disordered = [{"idx": 2, "narration": "a", "duration": 3, "visual": "v"},
                  {"idx": 1, "narration": "b", "duration": 3, "visual": "v"}]
    bad({"topic_id": tid, "shots": disordered})                # idx 乱序
    # 非法输入不落库
    db = reg.get("zimeiti.storyboard_save").plugin_ctx.db
    assert db.query("storyboards", where={"topic_id": tid}) == []


# ---------- storyboard_get：读回 ----------


def test_storyboard_get_reads_latest_and_specific_version(env):
    reg, _, _ = env
    tid = _topic(reg)
    r = _run(reg, "zimeiti.storyboard_get", {"topic_id": tid})
    assert not r.success and "分镜" in r.error                 # 还没分镜时人话报错

    _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS, "note": "v1"})
    _run(reg, "zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS[:2], "note": "v2"})

    r = _run(reg, "zimeiti.storyboard_get", {"topic_id": tid})
    assert r.success and r.data["version"] == 2 and r.data["shot_count"] == 2
    assert r.data["note"] == "v2" and r.data["topic_id"] == tid
    assert [s["idx"] for s in r.data["shots"]] == [1, 2]
    assert r.data["shots"][0]["visual"] == "黑底白字大字报弹出"
    assert reg.get("zimeiti.storyboard_get").default_risk.name == "L0_READONLY"

    r1 = _run(reg, "zimeiti.storyboard_get", {"topic_id": tid, "version": 1})
    assert r1.success and r1.data["shot_count"] == 3 and r1.data["note"] == "v1"

    assert not _run(reg, "zimeiti.storyboard_get", {"topic_id": tid, "version": 9}).success
    assert not _run(reg, "zimeiti.storyboard_get", {"topic_id": tid, "version": "abc"}).success
    assert not _run(reg, "zimeiti.storyboard_get", {"topic_id": ""}).success
    assert not _run(reg, "zimeiti.storyboard_get", {"topic_id": "不存在"}).success


# ---------- work_outputs 声明形态 ----------


def test_storyboard_work_outputs_declared_shape(env):
    """preflight 能力索引直接读类属性：video.storyboard + video.shot 都由本工具提供。"""
    reg, _, _ = env
    outputs = reg.get("zimeiti.storyboard_save").work_outputs
    by_kind = {}
    for spec in outputs:
        by_kind.setdefault((spec["kind"], spec.get("artifact_type") or spec.get("relation")), spec)
    storyboard = by_kind[("artifact", "video.storyboard")]
    assert storyboard["ref_from"] == "data.storyboard_ref"
    shot = by_kind[("artifact", "video.shot")]
    assert shot["foreach_from"] == "data.shots" and shot["ref_from"] == "item.shot_ref"
    contains = by_kind[("edge", "contains")]
    assert contains["source_artifact_type"] == "video.storyboard"
    assert contains["target_artifact_type"] == "video.shot"
    derived = by_kind[("edge", "derived_from")]
    assert derived["target_artifact_type"] == "video.script"
    # storyboard 段 acceptance 是 ["storyboard", "shot"]：两个 artifact_type 都命中
    assert "storyboard" in storyboard["artifact_type"] and "shot" in shot["artifact_type"]
    assert reg.get("zimeiti.storyboard_get").work_outputs == ()  # L0 只读不产事件


def test_materialized_events_match_saved_data(env):
    """用真实 run 结果过 materialize（strict，与 invoker 同事务路径）：
    1 分镜 artifact + N shot artifact + N contains 边；有稿才多 1 条 derived_from 边。"""
    from yibao_brain.work_events import materialize_work_events

    reg, _, _ = env
    tid = _topic(reg)
    tool = reg.get("zimeiti.storyboard_save")
    params = {"topic_id": tid, "shots": _SHOTS, "note": "初版"}
    r = tool.run(params, tool.plugin_ctx)
    events = materialize_work_events(tool.work_outputs, params=params, data=r.data,
                                     tool_id=tool.id, strict=True)
    kinds = [e["event_type"] for e in events]
    assert kinds == ["artifact.upsert"] * 4 + ["artifact.edge.upsert"] * 3  # 无稿 → 无 derived_from
    storyboard = events[0]["payload"]
    assert storyboard["artifact_type"] == "video.storyboard" and storyboard["ref"] == tid
    assert storyboard["content_ref"].startswith("blob://sha256/")
    shots = [e["payload"] for e in events[1:4]]
    assert [s["ref"] for s in shots] == [f"{tid}#s1", f"{tid}#s2", f"{tid}#s3"]
    assert shots[0]["metadata"]["narration"] == "你以为 Agent 只是个聊天框？"
    edges = [e["payload"] for e in events[4:]]
    assert all(e["relation"] == "contains" and e["source"]["ref"] == tid for e in edges)
    assert [e["target"]["ref"] for e in edges] == [f"{tid}#s1", f"{tid}#s2", f"{tid}#s3"]

    # 有稿 → 多一条 storyboard derived_from video.script 边（script artifact 的 ref 是 topic_id）
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 稿件"})
    r2 = tool.run(params, tool.plugin_ctx)
    events2 = materialize_work_events(tool.work_outputs, params=params, data=r2.data,
                                      tool_id=tool.id, strict=True)
    last = events2[-1]["payload"]
    assert events2[-1]["event_type"] == "artifact.edge.upsert" and last["relation"] == "derived_from"
    assert last["source"] == {"artifact_type": "video.storyboard", "ref": tid}
    assert last["target"] == {"artifact_type": "video.script", "ref": tid}


# ---------- 端到端：经 invoker 落 Work Graph ----------


def test_storyboard_end_to_end_lands_in_work_graph(env, tmp_path):
    """shot 成为可寻址对象：artifact_view 按 <topic>#s2 拿到独立 artifact（自带 revision 元数据），
    分镜 contains 3 镜 + derived_from 稿件；workflow 推进到 assets 段。"""
    from yibao_brain.audit import AuditLog
    from yibao_brain.invoker import ToolInvoker
    from yibao_brain.llm import ToolCall
    from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
    from yibao_brain.work_events import WorkGraphInvocationSink
    from yibao_brain.work_graph import WorkGraphStore

    reg, _, _ = env
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("video", "Agent 科普视频", str(tmp_path / "video"))
    invoker = ToolInvoker(reg, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")))
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "video")

    def execute(tool_id, params):
        action = invoker.propose(ToolCall(id=f"tc-{tool_id}", tool_id=tool_id, params=params))
        return invoker.execute(action, params, {"conversation_id": "session-video", "surface": "home"})

    try:
        tid = execute("zimeiti.add", {"title": "Agent 为什么需要你点头"}).data["id"]
        assert execute("zimeiti.mat_save", {"text": "Agent 系统由模型、工具、记忆与权限治理组成。", "defer": True}).success
        assert execute("zimeiti.article_save", {"id": tid, "content": "逐字稿……"}).success
        saved = execute("zimeiti.storyboard_save", {"topic_id": tid, "shots": _SHOTS, "note": "初版"})
        assert saved.success

        view = graph.workspace_view("video")
        by_type = {}
        for item in view["objects"]:
            by_type.setdefault(item["type"], []).append(item)
        assert len(by_type["video.storyboard"]) == 1
        shots = sorted(by_type["video.shot"], key=lambda item: item["ref"])
        assert [s["ref"] for s in shots] == [f"{tid}#s1", f"{tid}#s2", f"{tid}#s3"]

        storyboard = graph.artifact_view(by_type["video.storyboard"][0]["artifact_id"])
        relations = {(e["relation"], e["target_artifact_id"]) for e in storyboard["edges"]}
        shot_ids = {s["artifact_id"] for s in shots}
        assert sum(1 for rel, target in relations if rel == "contains" and target in shot_ids) == 3
        script_id = by_type["video.script"][0]["artifact_id"]
        assert any(rel == "derived_from" and target == script_id for rel, target in relations)

        shot2 = graph.artifact_view(shots[1]["artifact_id"])  # 单独寻址第 2 镜
        assert shot2["external_ref"] == f"{tid}#s2"
        assert shot2["revisions"][-1]["metadata"]["narration"] == "它其实在替你跑一整套工作流"
        assert shot2["revisions"][-1]["metadata"]["duration"] == 4.5

        assert view["workflow_run"]["current_stage_id"] == "assets"  # 分镜段验收通过，推进到素材
        outbox = reg.get("zimeiti.add").plugin_ctx.db.work_outbox_events()
        assert {item["status"] for item in outbox} == {"acknowledged"}
    finally:
        graph.close()
