"""能力预检（capability preflight）：立项即算全链路能力可满足性，缺口前置暴露。

设计契约：docs/design/2026-09-01-agent-os-generalized-architecture.md §4.2
（draft → preflighting → ready → running，blocked 分支）与 §5.1（Capability 合同）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from yibao_brain.project_tools import make_project_tools
from yibao_brain.projects import ProjectStore
from yibao_brain.work_graph import WorkGraphStore, build_capability_index

REPO_ROOT = Path(__file__).resolve().parents[2]


def _zimeiti_like_index() -> dict:
    """zimeiti 能力声明的历史快照（只到 storyboard 段）：状态机测试用的「有缺口」场景。

    真实插件现已覆盖全链（见 test_real_zimeiti_plugin_covers_full_video_chain）；
    这里刻意保持部分覆盖，blocked/blocked_reason/plan 的分支才有东西可断言。
    """
    return {
        "zimeiti.topic": [
            {"plugin_id": "zimeiti", "tool_id": "zimeiti.add", "label": "加入选题"},
        ],
        "video.script": [
            {"plugin_id": "zimeiti", "tool_id": "zimeiti.article_save", "label": "保存稿件"},
        ],
        "research.evidence": [
            {"plugin_id": "zimeiti", "tool_id": "zimeiti.mat_save", "label": "保存素材"},
        ],
        "video.storyboard": [
            {"plugin_id": "zimeiti", "tool_id": "zimeiti.storyboard_save", "label": "保存分镜"},
        ],
        "video.shot": [
            {"plugin_id": "zimeiti", "tool_id": "zimeiti.storyboard_save", "label": "保存分镜"},
        ],
    }


def _full_video_index() -> dict:
    """zimeiti 之外补齐 video.explainer 后五段，使全链路可满足。"""
    index = _zimeiti_like_index()
    index.update({
        "video.storyboard": [{"plugin_id": "media", "tool_id": "media.storyboard", "label": "分镜"}],
        "image.asset": [{"plugin_id": "media", "tool_id": "media.image", "label": "出图"}],
        "audio.voice": [{"plugin_id": "media", "tool_id": "media.tts", "label": "配音"}],
        "video.timeline": [{"plugin_id": "media", "tool_id": "media.compose", "label": "合成"}],
        "video.render": [{"plugin_id": "media", "tool_id": "media.render", "label": "渲染导出"}],
    })
    return index


def _run(graph: WorkGraphStore, workspace_id: str) -> dict:
    return graph.workspace_view(workspace_id)["workflow_run"]


# ---------- 能力索引构建 ----------


def test_build_capability_index_from_tool_work_outputs():
    class _CodeTool:  # 代码工具形态：类属性 work_outputs（如 zimeiti article_save）
        id = "demo.save"
        label = "保存稿件"
        work_outputs = (
            {"kind": "artifact", "artifact_type": "video.script", "ref_from": "data.id"},
        )

    class _MixedTool:  # 声明式形态：evidence 同样产出 artifact（计入）；checkpoint 不计
        id = "demo.mix"
        label = ""
        work_outputs = (
            {"kind": "artifact", "artifact_type": "research.evidence", "ref_from": "data.id"},
            {"kind": "evidence", "artifact_type": "research.claim",
             "ref_from": "data.id", "claim_from": "data.claim"},
            {"kind": "checkpoint", "stage_id_from": "data.stage", "checkpoint_from": "data.cp"},
        )

    index = build_capability_index([_CodeTool(), _MixedTool()])
    assert set(index) == {"video.script", "research.evidence", "research.claim"}
    assert index["video.script"] == [{
        "plugin_id": "demo", "tool_id": "demo.save", "label": "保存稿件",
        "artifact_type": "video.script",
    }]
    # label 缺省回退 tool_id
    assert index["research.evidence"][0]["label"] == "demo.mix"


# ---------- plan 计算与立项落库 ----------


def test_video_run_created_blocked_with_stage_level_plan(tmp_path):
    """验收锚点：video.explainer + zimeiti 能力 → 前四段 available，后四段 missing。"""
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(_zimeiti_like_index())
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        run = _run(graph, "ws")
        assert run["definition_id"] == "video.explainer"
        assert run["status"] == "blocked"
        assert run["blocked_reason"] == "素材、配音、合成、交付 缺能力 provider"
        plan = run["capability_plan"]
        assert plan["ready"] is False
        assert plan["missing"] == ["assets", "voice", "compose", "deliver"]
        by_id = {stage["id"]: stage for stage in plan["stages"]}
        assert [by_id[key]["status"] for key in ("topic", "evidence", "script", "storyboard")] == ["available"] * 4
        assert by_id["topic"]["providers"][0]["tool_id"] == "zimeiti.add"
        assert by_id["evidence"]["providers"][0]["tool_id"] == "zimeiti.mat_save"
        assert by_id["script"]["providers"][0]["tool_id"] == "zimeiti.article_save"
        # storyboard 段 acceptance 是 storyboard/shot 双 pattern：同一 tool 两种产出都算
        assert by_id["storyboard"]["providers"][0]["tool_id"] == "zimeiti.storyboard_save"
        # deliver 判 missing：zimeiti 的 publish/wewrite 未声明 work_outputs（代码事实）
        assert by_id["deliver"]["providers"] == []
        assert plan["computed_at"] > 0
    finally:
        graph.close()


def test_run_created_ready_when_all_stages_covered(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(_full_video_index())
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        run = _run(graph, "ws")
        assert run["status"] == "ready"
        assert run["blocked_reason"] == ""
        assert run["capability_plan"]["ready"] is True
        assert run["capability_plan"]["missing"] == []
    finally:
        graph.close()


def test_stage_with_multiple_rules_needs_every_rule_covered(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.register_workflow({
            "id": "demo.two_rules", "version": "1", "domain": "demo", "label": "双规则",
            "matches": ["双规则"],
            "stages": [{
                "id": "both", "label": "双产物", "depends_on": [],
                "acceptance": [
                    {"artifact_patterns": ["script"]},
                    # 单 rule 多 pattern：任一命中即可（podcast.voice 命中 voice）
                    {"artifact_patterns": ["audio", "voice"]},
                ],
            }],
        }, source_plugin="demo")
        graph.set_capability_providers(_zimeiti_like_index())  # 只有 script，无 voice/audio
        graph.create_workspace("ws", "双规则项目", str(tmp_path / "ws"))
        plan = _run(graph, "ws")["capability_plan"]
        assert plan["stages"][0]["status"] == "missing"  # 第二条 rule 无 provider
        assert _run(graph, "ws")["status"] == "blocked"

        graph.set_capability_providers({
            **_zimeiti_like_index(),
            "podcast.voice": [{"plugin_id": "podcast", "tool_id": "podcast.tts", "label": "配音"}],
        })
        run = _run(graph, "ws")
        assert run["status"] == "ready"
        assert run["capability_plan"]["stages"][0]["status"] == "available"
    finally:
        graph.close()


# ---------- 与 _sync_workflow_locked 的整合 ----------


def test_sync_preserves_capability_block_across_detach_and_reopen(tmp_path):
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    try:
        graph.set_capability_providers(_zimeiti_like_index())
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        assert _run(graph, "ws")["status"] == "blocked"

        # 产物进场 → 正常往 running 走，blocked_reason 让位（缺口留在 plan 里作信息）
        graph.attach_external_artifact("ws", "zimeiti.topic", "t1")
        run = _run(graph, "ws")
        assert run["status"] == "running"
        assert run["blocked_reason"] == ""
        assert run["capability_plan"]["ready"] is False  # plan 不因开工被冲掉

        # 产物撤走 → 回到 capability-blocked，而不是 draft/ready 伪装可推进
        graph.detach_external_artifact("ws", "zimeiti.topic", "t1")
        run = _run(graph, "ws")
        assert run["status"] == "blocked"
        assert "素材" in run["blocked_reason"]
    finally:
        graph.close()

    # 重启（__init__ 全量 sync）也不冲掉 capability-blocked
    reopened = WorkGraphStore(str(path))
    try:
        run = _run(reopened, "ws")
        assert run["status"] == "blocked"
        assert run["blocked_reason"] == "素材、配音、合成、交付 缺能力 provider"
    finally:
        reopened.close()


def test_set_capability_providers_flips_blocked_to_ready_but_skips_started_runs(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(_zimeiti_like_index())
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        assert _run(graph, "ws")["status"] == "blocked"

        # 已开工的 run（running 及以后）不被重算：plan 保持注入前的结论
        graph.create_workspace("started", "Agent 实践视频", str(tmp_path / "started"))
        graph.attach_external_artifact("started", "zimeiti.topic", "t1")
        assert _run(graph, "started")["status"] == "running"

        # 插件后装补齐能力 → 未开工的 blocked run 翻 ready
        graph.set_capability_providers(_full_video_index())
        run = _run(graph, "ws")
        assert run["status"] == "ready"
        assert run["blocked_reason"] == ""
        assert run["capability_plan"]["ready"] is True

        started = _run(graph, "started")
        assert started["status"] == "running"
        assert started["capability_plan"]["ready"] is False  # 终态 plan 不动
    finally:
        graph.close()


def test_register_workflow_refreshes_plan_for_open_runs(tmp_path):
    """同 id+version 定义原位重写（acceptance 变了）→ 非终态 run 的 plan 跟着刷新。"""
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        definition = {
            "id": "demo.evolving", "version": "1", "domain": "demo", "label": "演进",
            "matches": ["演进"],
            "stages": [{"id": "out", "label": "产出", "depends_on": [],
                        "acceptance": [{"artifact_patterns": ["hologram"]}]}],
        }
        graph.register_workflow(definition, source_plugin="demo")
        graph.set_capability_providers(_zimeiti_like_index())
        graph.create_workspace("ws", "演进项目", str(tmp_path / "ws"))
        assert _run(graph, "ws")["status"] == "blocked"

        definition["stages"][0]["acceptance"] = [{"artifact_patterns": ["script"]}]
        graph.register_workflow(definition, source_plugin="demo")
        run = _run(graph, "ws")
        assert run["status"] == "ready"
        assert run["capability_plan"]["stages"][0]["status"] == "available"
    finally:
        graph.close()


# ---------- 不误伤通用项目 ----------


def test_mission_general_computes_plan_but_never_blocks(tmp_path):
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        # 一个 provider 都没有：通用项目的 acceptance 面向通用对象，缺 provider 是常态
        graph.create_workspace("ws", "随便整理一下", str(tmp_path / "ws"))
        run = _run(graph, "ws")
        assert run["definition_id"] == "mission.general"
        assert run["status"] == "draft"
        assert run["blocked_reason"] == ""
        plan = run["capability_plan"]
        assert plan is not None and plan["ready"] is False  # plan 仍算，只作信息
        assert plan["missing"] == ["understand", "advance", "verify", "deliver"]
    finally:
        graph.close()


# ---------- 表面化 ----------


def test_project_create_tool_surfaces_capability_summary(tmp_path, monkeypatch):
    from yibao_brain import config

    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(_zimeiti_like_index())
        store = ProjectStore(str(tmp_path / "projects.json"), work_graph=graph)
        tools = {tool.id: tool for tool in make_project_tools(store)}

        result = tools["project.create"].run({"name": "Agent 概念科普视频"}, None)
        assert result.success
        capability = result.data["capability"]
        assert capability["ready"] is False
        assert capability["enforced"] is True
        assert capability["available_stages"] == ["选题", "证据", "脚本", "分镜"]
        assert capability["missing_stages"] == ["素材", "配音", "合成", "交付"]
        assert capability["blocked_reason"] == "素材、配音、合成、交付 缺能力 provider"
        assert capability["degradation"] == "可做到分镜；素材起缺能力，安装对应 provider 后可继续"

        # 通用项目：摘要有，但不给降级建议（没被阻断，谈不上「只能做到哪」）
        general = tools["project.create"].run({"name": "随便整理一下"}, None)
        capability = general.data["capability"]
        assert capability["enforced"] is False
        assert capability["degradation"] == ""
    finally:
        graph.close()


def test_capability_summary_ready_project_has_no_degradation(tmp_path, monkeypatch):
    from yibao_brain import config

    monkeypatch.setattr(config, "settings_path", lambda: str(tmp_path / "settings.json"))
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(_full_video_index())
        store = ProjectStore(str(tmp_path / "projects.json"), work_graph=graph)
        tools = {tool.id: tool for tool in make_project_tools(store)}
        result = tools["project.create"].run({"name": "Agent 概念科普视频"}, None)
        capability = result.data["capability"]
        assert capability["ready"] is True
        assert capability["missing_stages"] == []
        assert capability["degradation"] == ""
    finally:
        graph.close()


# ---------- 验收锚点：真实 zimeiti 插件声明 ----------


def test_real_zimeiti_plugin_covers_full_video_chain(tmp_path):
    """加载真实 plugins/ 目录建索引：zimeiti 九种产出覆盖 video.explainer 全链。

    storyboard_save 声明 video.storyboard + video.shot；voice_save 声明 voice.track、
    visual_card_save（降级视觉卡）声明 asset.visual；timeline_save 声明
    timeline.composition（compose 段）、render_save 声明 video.render（deliver 段）→
    八段全 available，missing 为空，run 判 ready（preflight 全链就绪的验收锚点）。
    """
    from yibao_brain.durable_execution import DurableExecutionEngine
    from yibao_brain.llm import FakeProvider
    from yibao_brain.memory import FakeMemory
    from yibao_brain.plugins import LlmChat, load_plugins
    from yibao_brain.tools import ToolRegistry

    reg = ToolRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    engine = DurableExecutionEngine(WorkGraphStore(str(tmp_path / "loader_wg.db")))
    load_plugins(REPO_ROOT / "plugins", reg, memory=FakeMemory(), http=_Http(),
                 llm=LlmChat(FakeProvider()), durable_engine=engine)
    index = build_capability_index(reg.list())
    assert set(index) == {
        "zimeiti.topic", "video.script", "research.evidence", "video.storyboard", "video.shot",
        "voice.track", "asset.visual", "timeline.composition", "video.render",
    }
    assert index["video.storyboard"][0]["tool_id"] == "zimeiti.storyboard_save"
    assert index["video.shot"][0]["tool_id"] == "zimeiti.storyboard_save"
    assert index["voice.track"][0]["tool_id"] == "zimeiti.voice_save"
    assert index["asset.visual"][0]["tool_id"] == "zimeiti.visual_card_save"
    assert index["timeline.composition"][0]["tool_id"] == "zimeiti.timeline_save"
    assert index["video.render"][0]["tool_id"] == "zimeiti.render_save"

    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.set_capability_providers(index)
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        plan = _run(graph, "ws")["capability_plan"]
        by_id = {stage["id"]: stage for stage in plan["stages"]}
        assert by_id["storyboard"]["status"] == "available"
        assert by_id["assets"]["status"] == "available"
        assert by_id["voice"]["status"] == "available"
        assert by_id["compose"]["status"] == "available"
        assert by_id["deliver"]["status"] == "available"
        assert plan["missing"] == []
        assert plan["ready"] is True
        assert _run(graph, "ws")["status"] == "ready"
    finally:
        graph.close()


# ---------- server 接线：插件加载完成即注入能力索引 ----------

_CAPY_MANIFEST = """
id = "capy"
name = "能力预检插件A"
capabilities = ["db"]

[[table]]
name = "items"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "title", type = "text"},
]

[[tool]]
id = "make"
label = "造脚本"
type = "db"
description = "造选题/证据/脚本产物"
risk = "L1"
[tool.params]
title = {type = "string", description = "标题"}
[tool.db]
op = "insert"
table = "items"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "zimeiti.topic"
ref_from = "data.id"
[[tool.work_outputs]]
kind = "evidence"
artifact_type = "research.evidence"
ref_from = "data.id"
claim_from = "data.title"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "video.script"
ref_from = "data.id"
"""

_CAPY2_MANIFEST = """
id = "capy2"
name = "能力预检插件B"
capabilities = ["db"]

[[table]]
name = "items2"
columns = [{name = "id", type = "text", pk = true}]

[[tool]]
id = "make_rest"
label = "造剩余产物"
type = "db"
description = "造分镜/素材/配音/合成/渲染产物"
risk = "L1"
[tool.db]
op = "insert"
table = "items2"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "video.storyboard"
ref_from = "data.id"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "image.asset"
ref_from = "data.id"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "audio.voice"
ref_from = "data.id"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "video.timeline"
ref_from = "data.id"
[[tool.work_outputs]]
kind = "artifact"
artifact_type = "video.render"
ref_from = "data.id"
"""


def _write_plugin(root: Path, name: str, manifest: str) -> None:
    directory = root / name  # 约定目录名 == manifest id（增量重载按目录名跳过）
    directory.mkdir(parents=True)
    (directory / "manifest.toml").write_text(manifest, encoding="utf-8")


def test_server_injects_capability_index_on_load_and_hot_reload(tmp_path, monkeypatch):
    """build_loop 接线：插件加载完成注入索引；capability_refresh 热加载后重建索引。"""
    from yibao_brain.llm import FakeProvider
    from yibao_brain.server import build_loop

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "capy", _CAPY_MANIFEST)
    monkeypatch.setenv("YIBAO_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setenv("YIBAO_A11Y", "0")  # 测试不起真实 a11y 基座

    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    try:
        graph.create_workspace("ws", "Agent 概念科普视频", str(tmp_path / "ws"))
        assert _run(graph, "ws")["status"] == "blocked"  # 尚无 provider，全链路缺能力

        loop = build_loop(
            lambda: {}, True, str(tmp_path / "audit.db"),
            provider=FakeProvider(),
            workflow_registrar=graph.register_workflow,
            capability_sink=graph.set_capability_providers,
        )
        # 初次加载完成即注入：脚本段补上，缺口收窄但后五段仍 blocked
        run = _run(graph, "ws")
        assert run["status"] == "blocked"
        assert "脚本" not in run["blocked_reason"]
        assert "分镜" in run["blocked_reason"]

        # 热加载第二个插件补齐全部能力 → capability-blocked 翻 ready
        _write_plugin(plugins_dir, "capy2", _CAPY2_MANIFEST)
        result = loop.skills.get("capability_refresh").run({}, None)
        assert result.success, result.error
        assert result.data["added"] == ["capy2"]
        assert _run(graph, "ws")["status"] == "ready"
    finally:
        graph.close()
