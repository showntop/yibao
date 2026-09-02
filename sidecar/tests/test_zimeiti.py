"""zimeiti 插件（自媒体：选题+写作）端到端测试：加载真实 plugins/zimeiti/（数据目录重定向到 tmp）。

覆盖：声明式 CRUD/状态流转全链 + 代码 tool（article_save/article_read 版本管理）
+ bundled skill（skills/write/SKILL.md 成文框架，use_skill 展开）
+ api.toml 白名单 + 面板 schema 与 api 方法的一致性 + webview 编辑器面板。
"""
import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.llm import ToolCall
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, get_api, load_plugins
from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.session_contexts import SessionContextStore
from yibao_brain.tools import ToolRegistry
from yibao_brain.durable_execution import DurableExecutionEngine
from yibao_brain.work_events import WorkGraphInvocationSink
from yibao_brain.work_graph import WorkGraphStore

REPO_ROOT = Path(__file__).resolve().parents[2]
ZIMEITI_DIR = REPO_ROOT / "plugins" / "zimeiti"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir, tmp_path):
    """加载真实插件目录；返回 (registry, FakeMemory, 加载结果)。"""
    reg = ToolRegistry()
    mem = FakeMemory()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=mem, http=_Http(), llm=LlmChat(FakeProvider()),
        durable_engine=DurableExecutionEngine(WorkGraphStore(str(tmp_path / "wg.db"))),
    )
    return reg, mem, results


def _run(reg, tid, params):
    t = reg.get(tid)
    return t.run(params, t.plugin_ctx)


# ---------- 加载 ----------


def test_zimeiti_loads_ok(env):
    _, _, results = env
    assert results["zimeiti"] == "ok"


def test_all_tools_registered_with_risks(env):
    reg, _, _ = env
    from yibao_brain.ipc import RiskLevel

    expected = {
        "zimeiti.add": RiskLevel.L1_LOW,
        "zimeiti.list": RiskLevel.L0_READONLY,
        "zimeiti.get": RiskLevel.L0_READONLY,
        "zimeiti.open_editor": RiskLevel.L0_READONLY,
        "zimeiti.move": RiskLevel.L1_LOW,
        "zimeiti.delete": RiskLevel.L2_MEDIUM,
        "zimeiti.article_save": RiskLevel.L2_MEDIUM,
        "zimeiti.article_read": RiskLevel.L0_READONLY,
        "zimeiti.ai_edit": RiskLevel.L1_LOW,
        "zimeiti.set_status": RiskLevel.L1_LOW,
        "zimeiti.versions": RiskLevel.L0_READONLY,
        "zimeiti.publish": RiskLevel.L2_MEDIUM,
        "zimeiti.hot_topics": RiskLevel.L1_LOW,
        "zimeiti.stat_add": RiskLevel.L1_LOW,
        "zimeiti.mat_search": RiskLevel.L0_READONLY,
        "zimeiti.night_brief": RiskLevel.L1_LOW,  # 守夜人可调度上限 L1（夜间无人值守不弹确认）
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid


def test_declarative_tools_carry_human_labels(env):
    """过程展示用 manifest label（缺省回退 tool id）：不再暴露 zimeiti.add 这类内部名。"""
    reg, _, _ = env
    expected = {
        "zimeiti.add": "记选题",
        "zimeiti.list": "选题看板",
        "zimeiti.open_editor": "打开编辑器",
        "zimeiti.update": "改选题",
        "zimeiti.mat_list": "素材库",
        "zimeiti.mat_get": "素材正文",
        "zimeiti.mat_delete": "删素材",
        "zimeiti.mat_link": "关联素材",
        "zimeiti.stat_list": "发布数据",
    }
    for tid, label in expected.items():
        assert reg.get(tid).label == label, tid


# ---------- 声明式全链：add → list → get → move → delete ----------


def test_declarative_chain(env):
    reg, _, _ = env
    r = _run(reg, "zimeiti.add", {"title": "AI 桌宠的一天", "angle": "vlog 式记录", "platform": "小红书"})
    assert r.success and r.data["id"]
    tid = r.data["id"]

    rows = _run(reg, "zimeiti.list", {}).data["rows"]
    assert [row["id"] for row in rows] == [tid]
    assert rows[0]["status"] == "候选" and rows[0]["created_at"] > 0

    got = _run(reg, "zimeiti.get", {"id": tid}).data["rows"]
    assert len(got) == 1 and got[0]["title"] == "AI 桌宠的一天"

    before = got[0]["updated_at"]
    assert _run(reg, "zimeiti.move", {"id": tid, "status": "待发布"}).success
    after = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert after["status"] == "待发布" and after["updated_at"] >= before

    assert _run(reg, "zimeiti.delete", {"id": tid}).success
    assert _run(reg, "zimeiti.list", {}).data["rows"] == []


def test_delete_cascades_articles_materials_stats(env):
    """delete 清业务关系；content-addressed 文件由 BlobStore 延迟 GC，避免误删共享内容。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    p1 = Path(_run(reg, "zimeiti.article_save", {"id": tid, "content": "# v1"}).data["path"])
    p2 = Path(_run(reg, "zimeiti.article_save", {"id": tid, "content": "# v2"}).data["path"])
    mid = _mat_skill(reg).run({"text": "素材x"}, reg.get("zimeiti.mat_save").plugin_ctx).data["id"]
    assert _run(reg, "zimeiti.mat_link", {"id": mid, "topic_id": tid}).success
    assert _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "小红书", "views": 100}).success

    r = _run(reg, "zimeiti.delete", {"id": tid})
    assert r.success and r.panel == "zimeiti:board" and reg.get("zimeiti.delete").refresh == "zimeiti.list"

    db = reg.get("zimeiti.delete").plugin_ctx.db
    assert db.query("articles", where={"topic_id": tid}) == []  # 稿件库行清掉
    assert p1.exists() and p2.exists()  # 共享 blob 不在领域删除中立即 unlink
    assert _run(reg, "zimeiti.mat_get", {"id": mid}).data["rows"][0]["topic_id"] == ""  # 素材本体保留、摘关联
    assert _run(reg, "zimeiti.stat_list", {}).data["rows"] == []  # 发布数据清掉
    assert _run(reg, "zimeiti.list", {}).data["rows"] == []


def test_delete_rejects_missing_topic(env):
    reg, _, _ = env
    assert not _run(reg, "zimeiti.delete", {"id": "missing"}).success
    assert not _run(reg, "zimeiti.delete", {"id": ""}).success


def test_get_enriches_single_topic(env):
    """v2 #14：按 id 查时带聚合字段（稿件版本+字数/素材数）；无稿无素材给占位文案。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["draft"] == "还没写稿" and row["materials"] == "—"

    _run(reg, "zimeiti.article_save", {"id": tid, "content": "一二三四五"})
    mid = _mat_skill(reg).run({"text": "素材x"}, reg.get("zimeiti.mat_save").plugin_ctx).data["id"]
    _run(reg, "zimeiti.mat_link", {"id": mid, "topic_id": tid})
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["draft"] == "v1 · 5 字" and row["materials"] == "1 条"
    # 全量查询不拼聚合（列表路径不逐条读稿）
    assert "draft" not in _run(reg, "zimeiti.get", {}).data["rows"][0]


# ---------- bundled skill（guides → skills/write/SKILL.md，2026-08-24 转化） ----------


def test_open_editor_tool(env):
    """对话直达编辑器（#2b）：模型调 zimeiti.open_editor{id} → explicit + editor 面板 +
    rows 带选题（编辑器 onInit 凭 rows[0].id 自动加载最新稿）。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r = _run(reg, "zimeiti.open_editor", {"id": tid})
    assert r.success and r.panel == "zimeiti:editor" and r.explicit is True
    assert r.data["rows"][0]["id"] == tid


def test_bundled_write_skill_resolvable(env):
    """插件包内技能随加载注册：owner 前缀与短名都可解析，正文完整（原 zimeiti.guide 替代面）。"""
    from yibao_brain.tools.skills_index import index, resolve

    env
    assert "zimeiti:write" in index()
    key, entry = resolve("zimeiti:write")
    assert key == "zimeiti:write" and "钩子" in entry["text"]
    assert resolve("write")[0] == "zimeiti:write"  # owner 省略：短名唯一命中


def test_bundled_topics_skill_resolvable(env):
    """选题推荐技能（skills/topics/SKILL.md）：「今天写什么」类问题的处理流程，随插件加载注册。"""
    from yibao_brain.tools.skills_index import index, resolve

    env
    assert "zimeiti:topics" in index()
    key, entry = resolve("zimeiti:topics")
    assert key == "zimeiti:topics" and "为什么现在写" in entry["text"]
    assert "今天写什么" in entry["description"]  # frontmatter 描述带触发语，供模型选技能


# ---------- quiet 保留参数（带面板的声明式 tool：只要数据、不弹面板） ----------


def test_list_quiet_suppresses_panel(env):
    """quiet=true：只要数据不弹面板（选题推荐等内部查询场景）；默认行为不变（仍弹看板）。"""
    reg, _, _ = env
    _run(reg, "zimeiti.add", {"title": "T1"})
    r = _run(reg, "zimeiti.list", {"quiet": True})
    assert r.success and r.panel is None and r.explicit is False
    assert r.data["rows"][0]["title"] == "T1"
    r2 = _run(reg, "zimeiti.list", {})
    assert r2.success and r2.panel == "zimeiti:board" and r2.explicit is True


def test_add_quiet_stripped_before_insert(env):
    """quiet 是保留参数：写操作带 quiet 不当作列插库（未知列会报错），也不弹面板。"""
    reg, _, _ = env
    r = _run(reg, "zimeiti.add", {"title": "T2", "quiet": True})
    assert r.success and r.panel is None
    row = _run(reg, "zimeiti.list", {"quiet": True}).data["rows"][0]
    assert row["title"] == "T2" and "quiet" not in row


# ---------- 项目作用域（P0：绑定项目的会话，list/mat_list 默认只出本项目） ----------


def _bind_project(data_dir, cid, pid):
    """会话绑定项目：与底座 SessionContextStore 同一份存储。"""
    SessionContextStore(str(data_dir / "session_contexts.json")).bind(cid, pid)


def _run_in_conv(reg, tid, params, cid):
    """模拟 loop/panel 直调的 ctx 装配：conversation_id 经 ctx.meta 注入。"""
    t = reg.get(tid)
    return t.run(params, replace(t.plugin_ctx, meta={"conversation_id": cid}))


def _seed_scoped_topics(reg):
    ta = _run(reg, "zimeiti.add", {"title": "A 项目选题"}).data["id"]
    tb = _run(reg, "zimeiti.add", {"title": "B 项目选题"}).data["id"]
    tc = _run(reg, "zimeiti.add", {"title": "未立项选题"}).data["id"]
    _run(reg, "zimeiti.update", {"id": ta, "project_id": "proj_a"})
    _run(reg, "zimeiti.update", {"id": tb, "project_id": "proj_b"})
    return ta, tb, tc


def _seed_material(reg, title, topic_id=""):
    db = reg.get("zimeiti.mat_list").plugin_ctx.db
    return db.insert("materials", {"title": title, "topic_id": topic_id,
                                   "created_at": 1, "updated_at": 1})


def test_list_scoped_to_bound_project(env, data_dir):
    """会话绑定 proj_a：看板只列 proj_a 选题；他项目与未立项选题不进项目作用域。"""
    reg, _, _ = env
    ta, _, _ = _seed_scoped_topics(reg)
    _bind_project(data_dir, "conv1", "proj_a")
    rows = _run_in_conv(reg, "zimeiti.list", {}, "conv1").data["rows"]
    assert [r["id"] for r in rows] == [ta]


def test_list_unbound_session_sees_all(env, data_dir):
    """未绑定项目的会话维持现状（全库）：无 meta / 有 conversation_id 但无绑定都算未绑定。"""
    reg, _, _ = env
    _seed_scoped_topics(reg)
    assert len(_run(reg, "zimeiti.list", {}).data["rows"]) == 3
    assert len(_run_in_conv(reg, "zimeiti.list", {}, "conv_free").data["rows"]) == 3


def test_list_scope_global_overrides_binding(env, data_dir):
    """显式放宽：scope=global 返回全库；project_id 显式指定优先于会话绑定。"""
    reg, _, _ = env
    ta, tb, _ = _seed_scoped_topics(reg)
    _bind_project(data_dir, "conv1", "proj_a")
    rows = _run_in_conv(reg, "zimeiti.list", {"scope": "global"}, "conv1").data["rows"]
    assert len(rows) == 3
    rows = _run_in_conv(reg, "zimeiti.list", {"project_id": "proj_b"}, "conv1").data["rows"]
    assert [r["id"] for r in rows] == [tb]
    # 未绑定会话也可显式指定项目
    rows = _run(reg, "zimeiti.list", {"project_id": "proj_a"}).data["rows"]
    assert [r["id"] for r in rows] == [ta]


def test_mat_list_scoped_via_topic(env, data_dir):
    """material 无 project_id 列：经 topic_id 反解选题的 project_id 归属；
    孤儿素材（无 topic）只在全球作用域可见。"""
    reg, _, _ = env
    ta, tb, _ = _seed_scoped_topics(reg)
    m1 = _seed_material(reg, "A 项目素材", ta)
    _seed_material(reg, "B 项目素材", tb)
    _seed_material(reg, "孤儿素材")
    _bind_project(data_dir, "conv1", "proj_a")
    rows = _run_in_conv(reg, "zimeiti.mat_list", {}, "conv1").data["rows"]
    assert [r["id"] for r in rows] == [m1]


def test_mat_list_scope_global_shows_orphans(env, data_dir):
    reg, _, _ = env
    ta, tb, _ = _seed_scoped_topics(reg)
    _seed_material(reg, "A 项目素材", ta)
    _seed_material(reg, "B 项目素材", tb)
    _seed_material(reg, "孤儿素材")
    _bind_project(data_dir, "conv1", "proj_a")
    rows = _run_in_conv(reg, "zimeiti.mat_list", {"scope": "global"}, "conv1").data["rows"]
    assert len(rows) == 3


def test_mat_list_scoped_where_topic(env, data_dir):
    """编辑器素材抽屉的查法（where 按 topic_id 过滤）在项目作用域内仍可用；
    where 指向他项目选题时项目边界优先，返回空。"""
    reg, _, _ = env
    ta, tb, _ = _seed_scoped_topics(reg)
    m1 = _seed_material(reg, "A 素材 1", ta)
    _seed_material(reg, "B 素材", tb)
    _bind_project(data_dir, "conv1", "proj_a")
    rows = _run_in_conv(reg, "zimeiti.mat_list", {"where": {"topic_id": ta}}, "conv1").data["rows"]
    assert [r["id"] for r in rows] == [m1]
    rows = _run_in_conv(reg, "zimeiti.mat_list", {"where": {"topic_id": tb}}, "conv1").data["rows"]
    assert rows == []


def test_panel_refresh_carries_conversation_scope(env, data_dir, tmp_path):
    """写操作后的看板刷新（panel._emit_refresh_panel）继承会话项目作用域，
    否则「记个选题」后跟单刷新会把看板冲成全库。"""
    from types import SimpleNamespace

    from yibao_brain.panel import _emit_refresh_panel

    reg, _, _ = env
    ta, _, _ = _seed_scoped_topics(reg)
    _bind_project(data_dir, "conv1", "proj_a")
    invoker = ToolInvoker(reg, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "a.db")))
    agent = SimpleNamespace(invoker=invoker)
    events = []
    asyncio.run(_emit_refresh_panel(agent, events.append, "zimeiti.list", "conv1"))
    panels = [e for e in events if e.kind == "panel"]
    assert panels and [r["id"] for r in panels[0].payload["data"]["rows"]] == [ta]


# ---------- article_save / article_read（版本管理） ----------


def test_article_save_versions_and_status_flow(env, data_dir):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]

    r1 = _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 初稿", "note": "初稿"})
    assert r1.success and r1.data["version"] == 1 and r1.panel == "zimeiti:detail"
    path1 = Path(r1.data["path"])
    assert path1.is_file() and len(path1.name) == 64
    assert r1.data["content_ref"].startswith("blob://sha256/")
    assert data_dir in path1.parents  # 落在用户数据根，不污染仓库
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "写作中"  # 有稿即进入写作中

    r2 = _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 二稿", "note": "改了开头"})
    assert r2.success and r2.data["version"] == 2
    assert Path(r2.data["path"]).read_text(encoding="utf-8") == "# 二稿"

    # 已流转的状态不被 save 回退
    _run(reg, "zimeiti.move", {"id": tid, "status": "待发布"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 三稿"})
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "待发布"


def test_article_save_rejects_missing_topic(env):
    reg, _, _ = env
    assert not _run(reg, "zimeiti.article_save", {"id": "missing", "content": "x"}).success
    assert not _run(reg, "zimeiti.article_save", {"id": "", "content": "x"}).success


def test_article_read_latest_and_specific_version(env):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    assert not _run(reg, "zimeiti.article_read", {"id": tid}).success  # 无稿时报错

    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v1"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v2", "note": "二稿"})

    r = _run(reg, "zimeiti.article_read", {"id": tid})
    assert r.success and r.data["version"] == 2 and r.data["content"] == "# v2"
    assert r.data["note"] == "二稿"

    r1 = _run(reg, "zimeiti.article_read", {"id": tid, "version": 1})
    assert r1.success and r1.data["content"] == "# v1"

    assert not _run(reg, "zimeiti.article_read", {"id": tid, "version": 9}).success
    assert not _run(reg, "zimeiti.article_read", {"id": tid, "version": "abc"}).success


# ---------- api.toml 白名单 + 面板 schema 一致性 ----------


def test_api_whitelist(env):
    _ = env
    for name in ("zimeiti.list", "zimeiti.add", "zimeiti.get", "zimeiti.move", "zimeiti.delete"):
        api = get_api(name)
        assert api is not None and api.direct, name
    for name in ("zimeiti.draft", "zimeiti.revise", "zimeiti.read"):
        api = get_api(name)
        assert api is not None and not api.direct and api.intent, name
    assert get_api("zimeiti.move").refresh == "zimeiti.list"
    assert get_api("zimeiti.delete").refresh == "zimeiti.list"
    # 录数据表单 + 素材查看（2026-08-25 v2 #9）
    for name in ("zimeiti.record", "zimeiti.stat_add", "zimeiti.mat_get"):
        api = get_api(name)
        assert api is not None and api.direct, name
    assert get_api("zimeiti.record").panel == "zimeiti:record"
    assert get_api("zimeiti.mat_get").panel == "zimeiti:matdoc"


def test_panel_schemas_reference_whitelisted_methods(env):
    """面板 schema 里引用的 method 必须都在 api.toml 白名单（防手滑）。"""
    _ = env  # 先加载插件，get_api 注册表才有内容
    for schema_file in (ZIMEITI_DIR / "panel").glob("*.schema.json"):
        doc = json.loads(schema_file.read_text(encoding="utf-8"))
        actions = []
        if doc.get("type") == "board":
            actions += (doc.get("card") or {}).get("actions") or []
        actions += doc.get("actions") or []
        actions += (doc.get("item") or {}).get("actions") or []  # list 面板的条目级 action
        if doc.get("submit"):
            actions.append(doc["submit"])
        for extra in (doc.get("drag"), doc.get("quick_add"), doc.get("back")):  # 拖拽/快捷新增/返回导航同样走白名单
            if extra:
                actions.append(extra)
        assert actions, f"{schema_file.name} 没有 action"
        for a in actions:
            assert get_api(a["method"]) is not None, f"{schema_file.name}: {a['method']} 不在白名单"


# ---------- webview 写作编辑器面板 ----------


def test_editor_webview_panel_loaded(env):
    """manifest [[panel]] type="webview"：HTML 文本进 _PANELS；panel_payload 带 webview 形状。"""
    _ = env
    from yibao_brain.ipc import ActionResult
    from yibao_brain.plugins import get_panel, panel_payload

    p = get_panel("zimeiti:editor")
    assert p is not None and p["type"] == "webview"
    html = p["html"]
    assert "<textarea" in html and "window.yibao =" not in html  # 桥 JS 由父侧注入，插件不自带
    # 面板事件 size 可控：编辑器承载 AI diff（选段 + 全文三模式）/ 版本历史 / 发布格式化 / 素材抽屉
    # / 标题候选与平台选择弹层 / 主题通道 / AI 撤销与丢稿保护，上限放到 48KB 防失控增长
    assert len(html.encode("utf-8")) < 48 * 1024

    payload = panel_payload(ActionResult(success=True, data={"rows": []}, panel="zimeiti:editor"))
    assert payload["schema"] is None
    assert payload["webview"] == {"html": html}
    assert payload["data"] == {"rows": []}


def test_editor_api_methods_whitelisted(env):
    """编辑器三方法在白名单：open_editor 带 panel 覆盖；读/存直调（存的 L2 由 tool 自身承担）。"""
    _ = env
    oe = get_api("zimeiti.open_editor")
    assert oe is not None and oe.direct and oe.panel == "zimeiti:editor"
    ra = get_api("zimeiti.read_article")
    assert ra is not None and ra.direct and ra.handler == "zimeiti.article_read"
    sa = get_api("zimeiti.save_article")
    assert sa is not None and sa.direct and sa.handler == "zimeiti.article_save"
    assert sa.panel == "zimeiti:editor"  # 保存后仍停在编辑器（回执 data 不改变编辑器状态）


def _make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def test_open_editor_panel_action_end_to_end(env, tmp_path):
    """open_editor 通路：panel_action → zimeiti.get 直调 → api.panel 覆盖 detail → editor webview 事件。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "编辑器通路"}).data["id"]

    from yibao_brain.server import serve_async

    out = []
    asyncio.run(
        serve_async(
            _make_reader([
                {"id": 1, "type": "panel_action", "method": "zimeiti.open_editor", "params": {"id": tid}},
            ]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
            skills_factory=lambda: reg,
        )
    )
    evs = [m["event"] for m in out if m["type"] == "event"]
    pe = next(e for e in evs if e["kind"] == "panel")
    assert pe["payload"]["panel"] == "zimeiti:editor"
    assert pe["payload"]["schema"] is None
    assert "<textarea" in pe["payload"]["webview"]["html"]
    rows = pe["payload"]["data"]["rows"]
    assert rows[0]["id"] == tid and rows[0]["title"] == "编辑器通路"
    assert out[-1] == {"type": "run_done", "id": 1}


# ---------- ai_edit（编辑器选段 AI 协作：直调返回 replacement，不落盘不发面板） ----------


def _ai_skill(reg, text="改写后的表达"):
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = LlmChat(FakeProvider(text=text))
    return t


def test_ai_edit_api_registered_direct_no_panel(env):
    env  # 触发加载
    api = get_api("zimeiti.ai_edit")
    assert api is not None and api.direct and api.panel is None and api.refresh is None


def test_ai_edit_happy_rewrite(env):
    reg, _, _ = env
    t = _ai_skill(reg, "  改写后的表达  ")
    r = t.run({"selection": "原文片段", "mode": "rewrite"}, t.plugin_ctx)
    assert r.success and r.data["replacement"] == "改写后的表达" and r.data["mode"] == "rewrite"
    assert r.panel is None  # 不发面板事件：结果经桥回包给编辑器 iframe 做 diff


def test_ai_edit_unwraps_code_fence(env):
    reg, _, _ = env
    t = _ai_skill(reg, "```markdown\n更通顺的句子\n```")
    r = t.run({"selection": "原文"}, t.plugin_ctx)
    assert r.success and r.data["replacement"] == "更通顺的句子"


def test_ai_edit_prompt_carries_mode_and_context(env):
    reg, _, _ = env
    prov = FakeProvider(text="x")
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = LlmChat(prov)
    t.run({"selection": "片段", "mode": "expand", "context": "全文内容"}, t.plugin_ctx)
    prompt = prov.calls[0]["messages"][0]["content"]
    assert "扩写" in prompt and "全文内容" in prompt and "片段" in prompt


def test_ai_edit_default_mode_is_rewrite(env):
    reg, _, _ = env
    prov = FakeProvider(text="x")
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = LlmChat(prov)
    t.run({"selection": "片段"}, t.plugin_ctx)
    assert "改写" in prov.calls[0]["messages"][0]["content"]


def test_ai_edit_rejects_bad_input(env):
    reg, _, _ = env
    t = _ai_skill(reg)
    assert not t.run({"selection": "  "}, t.plugin_ctx).success  # 空选段
    assert not t.run({"selection": "x", "mode": "wat"}, t.plugin_ctx).success  # 未知模式
    assert not t.run({"selection": "x", "mode": "custom"}, t.plugin_ctx).success  # 自定义缺指令
    assert not t.run({"selection": "x" * 4001}, t.plugin_ctx).success  # 片段过长


def test_ai_edit_empty_llm_output_errors(env):
    reg, _, _ = env
    t = _ai_skill(reg, "   ")
    r = t.run({"selection": "原文"}, t.plugin_ctx)
    assert not r.success and "空" in r.error


def test_ai_edit_without_llm_capability_fails_gracefully(env):
    reg, _, _ = env
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = None
    r = t.run({"selection": "原文"}, t.plugin_ctx)
    assert not r.success and "LLM" in r.error


def test_ai_edit_llm_exception_becomes_error(env):
    reg, _, _ = env

    class _Boom:
        def chat(self, prompt):
            raise RuntimeError("网络炸了")

    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = _Boom()
    r = t.run({"selection": "原文"}, t.plugin_ctx)
    assert not r.success and "AI 处理失败" in r.error


# ---------- set_status（编辑器内「标为已发布」：静默流转，不发面板事件） ----------


def test_set_status_flows_without_panel(env):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r = _run(reg, "zimeiti.set_status", {"id": tid, "status": "已发布"})
    assert r.success and r.data["status"] == "已发布"
    assert r.panel is None  # 编辑器内调用不许把面板跳走
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "已发布"


def test_set_status_api_registered_direct_no_panel(env):
    env  # 触发加载
    api = get_api("zimeiti.set_status")
    assert api is not None and api.direct and api.panel is None and api.refresh is None


def test_set_status_rejects_bad_input(env):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    assert not _run(reg, "zimeiti.set_status", {"id": tid, "status": "火星"}).success  # 未知状态
    assert not _run(reg, "zimeiti.set_status", {"id": "不存在", "status": "已发布"}).success
    assert not _run(reg, "zimeiti.set_status", {"status": "已发布"}).success  # 缺 id


def test_set_status_marks_published_at(env):
    """进「已发布」记发布时间；其它状态不记。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    assert _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]["published_at"] == 0
    _run(reg, "zimeiti.set_status", {"id": tid, "status": "写作中"})
    assert _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]["published_at"] == 0
    _run(reg, "zimeiti.set_status", {"id": tid, "status": "已发布"})
    assert _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]["published_at"] > 0


# ---------- publish（发布最新稿：复制剪贴板 + 标已发布记时间） ----------


def test_publish_copies_and_marks(env, monkeypatch):
    import subprocess

    copied = {}

    def _fake_run(cmd, input=None, check=False):
        assert cmd == ["pbcopy"] and check
        copied["text"] = input.decode("utf-8")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "K3 是垃圾", "platform": "公众号"}).data["id"]

    # 无稿时报错，引导先写稿
    assert not _run(reg, "zimeiti.publish", {"id": tid}).success

    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 正文"})
    r = _run(reg, "zimeiti.publish", {"id": tid})
    assert r.success and r.data["opened_url"] == ""
    # 剪贴板给纯文本（2026-08-25 起剥 markdown，与编辑器小红书纯文本同语义）
    assert copied["text"] == "K3 是垃圾\n\n正文"
    assert r.data["chars"] == len("K3 是垃圾\n\n正文")
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["status"] == "已发布" and row["published_at"] > 0


def test_publish_open_platform(env, monkeypatch):
    import subprocess
    import webbrowser

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T", "platform": "小红书"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "x"})
    r = _run(reg, "zimeiti.publish", {"id": tid, "open_platform": True})
    assert r.success and r.data["opened_url"] == "https://creator.xiaohongshu.com/"
    assert opened == ["https://creator.xiaohongshu.com/"]


def test_publish_rejects_bad_input(env, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    reg, _, _ = env
    assert not _run(reg, "zimeiti.publish", {}).success  # 缺 id
    assert not _run(reg, "zimeiti.publish", {"id": "不存在"}).success


def test_publish_api_registered_direct(env):
    env  # 触发加载
    api = get_api("zimeiti.publish")
    assert api is not None and api.direct and api.refresh == "zimeiti.get"


# ---------- versions（编辑器版本历史） ----------


def test_versions_lists_newest_first(env):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    assert _run(reg, "zimeiti.versions", {"id": tid}).data["rows"] == []  # 无稿时空列表
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "一", "note": "初稿"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "二"})
    rows = _run(reg, "zimeiti.versions", {"id": tid}).data["rows"]
    assert [r["version"] for r in rows] == [2, 1]
    assert rows[1]["note"] == "初稿" and rows[0]["created_at"] > 0
    assert "content" not in rows[0]  # 列表不带正文（正文走 article_read?version=N）


def test_versions_api_registered_direct_no_panel(env):
    env  # 触发加载
    api = get_api("zimeiti.versions")
    assert api is not None and api.direct and api.panel is None and api.refresh is None


def test_versions_rejects_missing_id(env):
    reg, _, _ = env
    assert not _run(reg, "zimeiti.versions", {}).success


# ---------- 素材库（mat_save 存素材 + mat_list/mat_get/mat_delete） ----------


def _mat_skill(reg, text='{"title": "K3 评测", "summary": "三点硬伤。", "tags": ["AI", "硬件"]}'):
    t = reg.get("zimeiti.mat_save")
    t.plugin_ctx.llm = LlmChat(FakeProvider(text=text))
    return t


def test_mat_save_from_text_happy(env):
    reg, _, _ = env
    t = _mat_skill(reg)
    r = t.run({"text": "一段关于 K3 的评测原文……"}, t.plugin_ctx)
    assert r.success and r.data["title"] == "K3 评测" and r.data["tags"] == ["AI", "硬件"]
    assert r.panel == "zimeiti:materials"
    rows = _run(reg, "zimeiti.mat_list", {}).data["rows"]
    assert len(rows) == 1 and rows[0]["summary"] == "三点硬伤。" and rows[0]["kind"] == "note"
    got = _run(reg, "zimeiti.mat_get", {"id": r.data["id"]}).data["rows"][0]
    assert got["content"].startswith("一段关于 K3")


def test_mat_save_strips_code_fence(env):
    reg, _, _ = env
    t = _mat_skill(reg, '```json\n{"title": "T", "summary": "S", "tags": []}\n```')
    r = t.run({"text": "内容"}, t.plugin_ctx)
    assert r.success and r.data["title"] == "T"


def test_mat_save_bad_json_fallback(env):
    reg, _, _ = env
    t = _mat_skill(reg, "这不是 JSON")
    r = t.run({"text": "首句当标题用。后面是正文"}, t.plugin_ctx)
    assert r.success and r.data["title"].startswith("首句") and r.data["summary"]


def test_mat_save_rejects_empty_and_bad_url(env):
    reg, _, _ = env
    t = _mat_skill(reg)
    assert not t.run({}, t.plugin_ctx).success
    assert not t.run({"url": "ftp://x"}, t.plugin_ctx).success


def test_mat_save_without_llm_fails_gracefully(env):
    reg, _, _ = env
    t = reg.get("zimeiti.mat_save")
    t.plugin_ctx.llm = None
    assert not t.run({"text": "x"}, t.plugin_ctx).success


def test_mat_save_fetch_url(env, monkeypatch):
    import urllib.request

    class _Headers:
        def get_content_charset(self):
            return None  # -> utf-8

    class _Resp:
        headers = _Headers()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n=-1):
            return "<html><style>x</style><body><p>网页正文内容</p><script>y</script></body></html>".encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())
    reg, _, _ = env
    t = _mat_skill(reg)
    r = t.run({"url": "https://example.com/a"}, t.plugin_ctx)
    assert r.success and r.data["title"] == "K3 评测"
    row = _run(reg, "zimeiti.mat_get", {"id": r.data["id"]}).data["rows"][0]
    assert row["kind"] == "link" and "网页正文内容" in row["content"] and "script" not in row["content"]


def test_mat_delete_flow(env):
    reg, _, _ = env
    t = _mat_skill(reg)
    rid = t.run({"text": "x"}, t.plugin_ctx).data["id"]
    r = _run(reg, "zimeiti.mat_delete", {"id": rid})
    assert r.success
    assert _run(reg, "zimeiti.mat_list", {}).data["rows"] == []


def test_mat_defer_then_enrich(env):
    """#17：defer 先存后整理——即席元数据秒落库，mat_enrich 后台补标题/摘要/标签。"""
    reg, _, _ = env
    t = reg.get("zimeiti.mat_save")
    t.plugin_ctx.llm = LlmChat(FakeProvider())  # defer 不经过 LLM，给个保底即可
    r = t.run({"text": "即席首行标题\n正文正文", "defer": True}, t.plugin_ctx)
    assert r.success and r.data["pending"] is True and r.data["title"] == "即席首行标题"
    mid = r.data["id"]
    row = _run(reg, "zimeiti.mat_get", {"id": mid}).data["rows"][0]
    assert row["tags"] == ""  # 即席落库：标签待补

    e = reg.get("zimeiti.mat_enrich")
    e.plugin_ctx.llm = LlmChat(FakeProvider(text='{"title": "精整标题", "summary": "精整摘要", "tags": ["AI", "硬件"]}'))
    r2 = e.run({"id": mid}, e.plugin_ctx)
    assert r2.success and r2.data["title"] == "精整标题"
    row = _run(reg, "zimeiti.mat_get", {"id": mid}).data["rows"][0]
    assert row["title"] == "精整标题" and row["summary"] == "精整摘要" and row["tags"] == "AI,硬件"
    assert not e.run({"id": "missing"}, e.plugin_ctx).success


def test_invoke_apis_registered_quiet(env):
    """#17：唤起条/浏览器扩展的静默直调方法已注册且 quiet（不弹面板）。"""
    _ = env
    for name in ("zimeiti.invoke_mat_save", "zimeiti.invoke_add_topic"):
        api = get_api(name)
        assert api is not None and api.direct and api.quiet, name


def test_mat_search(env):
    """A8：关键词扫标题/正文、tag 精确过滤、topic 过滤；结果不带正文；三参全空报错。"""
    reg, _, _ = env
    t = reg.get("zimeiti.mat_save")
    t.plugin_ctx.llm = LlmChat(FakeProvider())
    a = t.run({"text": "K3 实测：续航拉胯", "defer": True, "title": "K3 评测"}, t.plugin_ctx).data["id"]
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    b = t.run({"text": "茅台价格又跌了", "defer": True, "title": "茅台", "topic_id": tid}, t.plugin_ctx).data["id"]
    t.plugin_ctx.db.update("materials", a, {"tags": "AI,硬件"})

    r = _run(reg, "zimeiti.mat_search", {"q": "续航"})
    assert [row["id"] for row in r.data["rows"]] == [a]
    assert "content" not in r.data["rows"][0]  # 检索结果省 payload，全文走 mat_get
    assert [row["id"] for row in _run(reg, "zimeiti.mat_search", {"tag": "硬件"}).data["rows"]] == [a]
    assert [row["id"] for row in _run(reg, "zimeiti.mat_search", {"topic_id": tid}).data["rows"]] == [b]
    assert _run(reg, "zimeiti.mat_search", {"q": "不存在的东西"}).data["rows"] == []
    assert not _run(reg, "zimeiti.mat_search", {}).success


# ---------- hot_topics（热点雷达：多平台热榜聚合） ----------


def _fake_boards(monkeypatch, reg, broken=()):
    """按 url 分发假热榜；broken 里的平台名抛异常（模拟网络挂/结构变）。
    加载器不把模块挂进 sys.modules，故走方法 __globals__ 拿模块命名空间。"""
    g = type(reg.get("zimeiti.hot_topics")).run.__globals__
    boards = {"zhihu": _ZHIHU_JSON, "toutiao": _TOUTIAO_JSON, "baidu": _BAIDU_JSON}

    def _fake(url):
        for key, payload in boards.items():
            if key in url:
                if key in broken:
                    raise OSError("boom")
                return payload
        raise AssertionError(f"未预期的 url：{url}")

    monkeypatch.setitem(g, "_fetch_json", _fake)


_ZHIHU_JSON = {
    "data": [
        {   # 新结构：title_area/metrics_area/link
            "target": {
                "title_area": {"text": "如何看待 K3 翻车？"},
                "metrics_area": {"text": "320 万热度"},
                "link": {"url": "https://www.zhihu.com/question/123"},
            }
        },
        {   # 老结构：target.title/detail_text + api 链接转网页链接
            "target": {"title": "茅台价格又跌了？", "url": "https://api.zhihu.com/questions/456"},
            "detail_text": "99 万热度",
        },
    ]
}
_TOUTIAO_JSON = {
    "data": [{"Title": "AI 编程工具大战", "HotValue": "10000000", "Url": "https://www.toutiao.com/trending/789/"}]
}
_BAIDU_JSON = {
    "data": {"cards": [{"content": [{"word": "高考分数线公布", "hotScore": "4500000", "url": "https://www.baidu.com/s?wd=x"}]}]}
}


def test_hot_topics_aggregates_platforms(env, monkeypatch):
    reg, _, _ = env
    _fake_boards(monkeypatch, reg)
    r = _run(reg, "zimeiti.hot_topics", {})
    assert r.success and r.panel == "zimeiti:hot" and r.data["failed"] == []
    rows = r.data["rows"]
    assert [row["platform"] for row in rows] == ["zhihu", "zhihu", "toutiao", "baidu"]
    assert rows[0]["rank"] == 1 and rows[0]["title"] == "如何看待 K3 翻车？"
    assert rows[0]["meta"] == "知乎 #1 · 320 万热度" and rows[0]["source_ref"] == "知乎热榜#1"
    assert rows[1]["url"] == "https://www.zhihu.com/question/456"  # api 链接转网页链接
    assert rows[2]["heat"] == "1000万热度" and rows[3]["meta"] == "百度 #1 · 450万热度"


def test_hot_topics_platform_failure_isolated(env, monkeypatch):
    reg, _, _ = env
    _fake_boards(monkeypatch, reg, broken=("zhihu",))
    r = _run(reg, "zimeiti.hot_topics", {})
    assert r.success and r.data["failed"] == ["知乎"]
    assert [row["platform"] for row in r.data["rows"]] == ["toutiao", "baidu"]


def test_hot_topics_all_failed(env, monkeypatch):
    reg, _, _ = env
    _fake_boards(monkeypatch, reg, broken=("zhihu", "toutiao", "baidu"))
    r = _run(reg, "zimeiti.hot_topics", {})
    assert not r.success and "知乎" in r.error and "头条" in r.error


def test_hot_topics_platforms_limit_and_unknown(env, monkeypatch):
    reg, _, _ = env
    _fake_boards(monkeypatch, reg)
    r = _run(reg, "zimeiti.hot_topics", {"platforms": "zhihu", "limit": 1})
    assert [row["title"] for row in r.data["rows"]] == ["如何看待 K3 翻车？"]
    assert not _run(reg, "zimeiti.hot_topics", {"platforms": "weibo"}).success


def test_hot_topics_cache_and_stale_fallback(env, monkeypatch):
    """v2 #11：10 分钟内重复拉取走缓存（不重打网络）；过期后抓取失败回退陈缓存。"""
    reg, _, _ = env
    g = type(reg.get("zimeiti.hot_topics")).run.__globals__
    calls: list[str] = []

    def _fake(url):
        calls.append(url)
        return _ZHIHU_JSON

    monkeypatch.setitem(g, "_fetch_json", _fake)
    _run(reg, "zimeiti.hot_topics", {"platforms": "zhihu"})
    r = _run(reg, "zimeiti.hot_topics", {"platforms": "zhihu"})
    assert len(calls) == 1 and r.data["failed"] == []  # 第二次走缓存

    g["_CACHE"]["zhihu"] = (0, g["_CACHE"]["zhihu"][1])  # 手动过期
    def _boom(url):
        raise OSError("boom")

    monkeypatch.setitem(g, "_fetch_json", _boom)
    r = _run(reg, "zimeiti.hot_topics", {"platforms": "zhihu"})
    assert r.success and r.data["failed"] == [] and r.data["rows"]  # 陈缓存顶上，不记 failed


def test_hot_topics_api_registered(env):
    env  # 触发加载
    api = get_api("zimeiti.hot_topics")
    assert api is not None and api.direct
    add = get_api("zimeiti.hot_add")
    assert add is not None and add.direct
    assert add.handler == "zimeiti.add" and add.panel == "zimeiti:hot" and add.refresh == "zimeiti.hot_topics"


def test_hot_add_creates_topic_with_source(env, monkeypatch):
    reg, _, _ = env
    _fake_boards(monkeypatch, reg)
    row = _run(reg, "zimeiti.hot_topics", {"platforms": "zhihu", "limit": 1}).data["rows"][0]
    r = _run(reg, "zimeiti.add", {"title": row["title"], "source": row["source_ref"], "url": row["url"]})
    assert r.success
    topic = _run(reg, "zimeiti.get", {"id": r.data["id"]}).data["rows"][0]
    assert topic["title"] == "如何看待 K3 翻车？" and topic["source"] == "知乎热榜#1" and topic["status"] == "候选"
    assert topic["url"] == "https://www.zhihu.com/question/123"  # 热点转选题带原文链接（v2 #16）


def test_stat_add_dedup_same_day(env):
    """v2 #13：同选题同平台同日去重——再录改旧行（没传的字段保留旧值）；不同平台不去重。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r1 = _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "小红书", "views": 100, "favorites": 5})
    assert r1.success and r1.data["updated"] is False
    r2 = _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "小红书", "views": 200})
    assert r2.success and r2.data["updated"] is True
    rows = _run(reg, "zimeiti.stat_list", {}).data["rows"]
    assert len(rows) == 1 and rows[0]["views"] == 200 and rows[0]["favorites"] == 5
    _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "知乎", "views": 50})
    assert len(_run(reg, "zimeiti.stat_list", {}).data["rows"]) == 2
    assert not _run(reg, "zimeiti.stat_add", {"topic_id": "missing", "platform": "x"}).success


# ---------- 素材打通（materials.topic_id / hot_mat_save / mat_link） ----------


def test_hot_mat_save_api_registered(env):
    env  # 触发加载
    api = get_api("zimeiti.hot_mat_save")
    assert api is not None and api.direct and api.handler == "zimeiti.mat_save"
    assert api.panel == "zimeiti:hot" and api.refresh == "zimeiti.hot_topics"


def test_mat_save_with_topic_id(env):
    reg, _, _ = env
    t = _mat_skill(reg)
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r = t.run({"text": "一段关联素材", "topic_id": tid}, t.plugin_ctx)
    assert r.success
    row = _run(reg, "zimeiti.mat_get", {"id": r.data["id"]}).data["rows"][0]
    assert row["topic_id"] == tid
    # 不传 topic_id 时落表默认空串
    r2 = t.run({"text": "无关联素材"}, t.plugin_ctx)
    row2 = _run(reg, "zimeiti.mat_get", {"id": r2.data["id"]}).data["rows"][0]
    assert row2["topic_id"] == ""


def test_mat_link_and_topic_filtered_list(env):
    reg, _, _ = env
    from yibao_brain.ipc import RiskLevel

    assert reg.get("zimeiti.mat_link").default_risk == RiskLevel.L1_LOW
    t = _mat_skill(reg)
    rid = t.run({"text": "x"}, t.plugin_ctx).data["id"]
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r = _run(reg, "zimeiti.mat_link", {"id": rid, "topic_id": tid})
    assert r.success and r.data["id"] == rid
    row = _run(reg, "zimeiti.mat_get", {"id": rid}).data["rows"][0]
    assert row["topic_id"] == tid
    # 编辑器素材抽屉的查法：mat_list 运行时 where 按 topic_id 过滤
    rows = _run(reg, "zimeiti.mat_list", {"where": {"topic_id": tid}}).data["rows"]
    assert [r["id"] for r in rows] == [rid]


# ---------- ai_edit 全文三模式（polish / title / platform，selection 即整文） ----------


def test_ai_edit_polish_full_text(env):
    reg, _, _ = env
    t = _ai_skill(reg, "润色后的全文")
    r = t.run({"selection": "# 全文\n正文", "mode": "polish"}, t.plugin_ctx)
    assert r.success and r.data["replacement"] == "润色后的全文" and r.data["mode"] == "polish"


def test_ai_edit_polish_prompt_rules(env):
    reg, _, _ = env
    prov = FakeProvider(text="x")
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = LlmChat(prov)
    t.run({"selection": "正文内容", "mode": "polish"}, t.plugin_ctx)
    prompt = prov.calls[0]["messages"][0]["content"]
    assert "润色" in prompt and "markdown" in prompt and "正文内容" in prompt


def test_ai_edit_platform_rewrites_with_style(env):
    reg, _, _ = env
    prov = FakeProvider(text="小红书风")
    t = reg.get("zimeiti.ai_edit")
    t.plugin_ctx.llm = LlmChat(prov)
    r = t.run({"selection": "正文", "mode": "platform", "platform": "小红书"}, t.plugin_ctx)
    assert r.success and r.data["replacement"] == "小红书风"
    prompt = prov.calls[0]["messages"][0]["content"]
    assert "小红书" in prompt and "话题标签" in prompt


def test_ai_edit_platform_requires_platform(env):
    reg, _, _ = env
    t = _ai_skill(reg)
    assert not t.run({"selection": "正文", "mode": "platform"}, t.plugin_ctx).success


def test_ai_edit_title_parses_json_array(env):
    reg, _, _ = env
    t = _ai_skill(reg, '["悬念", "干货", "情绪", "数字", "提问", "第六个不要"]')
    r = t.run({"selection": "全文", "mode": "title"}, t.plugin_ctx)
    assert r.success and r.data["titles"] == ["悬念", "干货", "情绪", "数字", "提问"]


def test_ai_edit_title_lines_fallback(env):
    reg, _, _ = env
    t = _ai_skill(reg, '1. 悬念标题\n- 干货标题\n"情绪标题"\n\n4）数字标题\n提问标题\n第六条不要')
    r = t.run({"selection": "全文", "mode": "title"}, t.plugin_ctx)
    assert r.success and r.data["titles"] == ["悬念标题", "干货标题", "情绪标题", "数字标题", "提问标题"]


def test_ai_edit_title_unparseable_errors(env):
    reg, _, _ = env
    t = _ai_skill(reg, "   ")
    assert not t.run({"selection": "全文", "mode": "title"}, t.plugin_ctx).success


def test_ai_edit_full_length_limits(env):
    reg, _, _ = env
    t = _ai_skill(reg)
    # v2 #15：polish 长文不再踢出编辑器，走分段润色队列（8001 字硬切成 2 段）
    r = t.run({"selection": "x" * 8001, "mode": "polish"}, t.plugin_ctx)
    assert r.success and r.data["chunks"] == 2 and r.data["replacement"] == "改写后的表达\n\n改写后的表达"
    assert t.run({"selection": "x" * 8000, "mode": "polish"}, t.plugin_ctx).success
    # platform 改写需整文视角，长文仍拒（引导走对话改稿）
    r = t.run({"selection": "x" * 8001, "mode": "platform", "platform": "小红书"}, t.plugin_ctx)
    assert not r.success and "8001" in r.error
    # title 长文截断取要，不踢出
    assert t.run({"selection": "x" * 8001, "mode": "title"}, t.plugin_ctx).success
    r = t.run({"selection": "x" * 4001}, t.plugin_ctx)
    assert not r.success and "4001" in r.error  # 选段上限 4000（回归）


# ---------- 发布复盘（post_stats + stat_add/stat_list + review intent） ----------


def test_stat_add_and_list(env):
    reg, _, _ = env
    from yibao_brain.ipc import RiskLevel

    assert reg.get("zimeiti.stat_add").default_risk == RiskLevel.L1_LOW
    assert reg.get("zimeiti.stat_list").default_risk == RiskLevel.L0_READONLY
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    other = _run(reg, "zimeiti.add", {"title": "U"}).data["id"]
    r = _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "公众号", "views": 1200, "likes": 30, "comments": 5})
    assert r.success and r.data["id"]
    assert _run(reg, "zimeiti.stat_add", {"topic_id": tid, "platform": "小红书"}).success  # 数字缺省落表默认 0
    _run(reg, "zimeiti.stat_add", {"topic_id": other, "platform": "知乎", "views": 10})

    rows = _run(reg, "zimeiti.stat_list", {"where": {"topic_id": tid}}).data["rows"]  # 运行时 where 按选题过滤
    assert len(rows) == 2 and all(row["topic_id"] == tid for row in rows)
    first = next(row for row in rows if row["platform"] == "公众号")
    assert first["views"] == 1200 and first["likes"] == 30 and first["comments"] == 5 and first["recorded_at"] > 0
    second = next(row for row in rows if row["platform"] == "小红书")
    assert second["views"] == 0 and second["likes"] == 0 and second["comments"] == 0
    assert len(_run(reg, "zimeiti.stat_list", {}).data["rows"]) == 3  # 不传 where 列全部


def test_review_api_registered_intent(env):
    env  # 触发加载
    api = get_api("zimeiti.review")
    assert api is not None and not api.direct and api.handler == "zimeiti.stat_list"
    assert "{title}" in api.intent and "{id}" in api.intent
    assert "zimeiti.stat_list" in api.intent and "zimeiti.article_read" in api.intent


# ---------- 2026-08-25 内容创作 v1：数据完整性与发布留痕 ----------


def test_move_rejects_invalid_status_and_marks_publish(env):
    """MoveTool（声明式 move 的代码承接）：非法状态拒绝；已发布写 published_at + published_version。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    bad = _run(reg, "zimeiti.move", {"id": tid, "status": "火星"})
    assert not bad.success and "未知状态" in bad.error

    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v1"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# v2"})
    ok = _run(reg, "zimeiti.move", {"id": tid, "status": "已发布"})
    assert ok.success and ok.panel == "zimeiti:detail"
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["published_at"] > 0 and row["published_version"] == 2


def test_update_tool_edits_topic_fields(env):
    """选题可编辑（v1 新增）：只更新传入字段，不动其他。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "旧标题", "angle": "旧角度"}).data["id"]
    r = _run(reg, "zimeiti.update", {"id": tid, "title": "新标题", "platform": "小红书"})
    assert r.success
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["title"] == "新标题" and row["platform"] == "小红书" and row["angle"] == "旧角度"


def test_article_save_stores_blob_ref_and_prunes_relations(env):
    """content_path 落稳定 BlobRef；版本治理保留最近 20 条关系，内容延迟 GC。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "v1"})
    rows = reg.get("zimeiti.list").plugin_ctx.db.query("articles", where={"topic_id": tid})
    assert rows[0]["content_path"].startswith("blob://sha256/")
    # article_read 通过 ctx.blobs 解析稳定引用。
    r = _run(reg, "zimeiti.article_read", {"id": tid})
    assert r.success and r.data["content"] == "v1"
    # 写 25 版 → 只剩 20 版且最旧被清
    for i in range(2, 26):
        _run(reg, "zimeiti.article_save", {"id": tid, "content": f"v{i}"})
    rows = reg.get("zimeiti.list").plugin_ctx.db.query("articles", where={"topic_id": tid}, order="version DESC")
    assert len(rows) == 20 and rows[0]["version"] == 25 and rows[-1]["version"] == 6


def test_publish_records_version_and_copies_plain_text(env, monkeypatch):
    """发布记 published_version；剪贴板给纯文本（剥 markdown），不再带 `#`/`**`。"""
    import subprocess

    copied = {}

    class _R:
        returncode = 0

    def _fake_run(cmd, input=None, check=False):
        copied["text"] = (input or b"").decode("utf-8")
        return _R()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 大标题\n\n**加粗** 和 `代码`\n\n- 列表项"})
    r = _run(reg, "zimeiti.publish", {"id": tid})
    assert r.success
    assert "# 大标题" not in copied["text"] and "大标题" in copied["text"]
    assert "**" not in copied["text"] and "· 列表项" in copied["text"]
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["published_version"] == 1


def test_strip_md_plain_semantics(env):
    """v2 #12：发布纯文本与编辑器 toPlain 同语义（标题/列表/引用/图片/链接/行内代码全剥）。"""
    reg, _, _ = env
    strip = type(reg.get("zimeiti.publish")).run.__globals__["_strip_md"]
    src = "# 标题\n- 要点一\n> 引用一句\n![图alt](https://x/1.png)\n[文字](https://x/2) 和 `code`"
    assert strip(src).splitlines() == ["标题", "· 要点一", "引用一句", "图alt", "文字（https://x/2） 和 code"]


# ---------- 视频 workflow S0：选题卡扩展字段（hkrr/钩型/目标视频平台/封面概念） ----------


def test_work_graph_output_contracts_are_declared(env):
    reg, _memory, _results = env
    assert reg.get("zimeiti.add").work_outputs[0]["artifact_type"] == "zimeiti.topic"
    assert reg.get("zimeiti.article_save").work_outputs[0]["artifact_type"] == "video.script"
    assert reg.get("zimeiti.mat_save").work_outputs[0]["kind"] == "evidence"


def test_real_zimeiti_tool_results_automatically_advance_work_graph(env, tmp_path):
    reg, _memory, _results = env
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("video", "Agent 科普视频", str(tmp_path / "video"))
    invoker = ToolInvoker(
        reg, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
    )
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "video")

    def execute(tool_id, params):
        action = invoker.propose(ToolCall(id=f"tc-{tool_id}", tool_id=tool_id, params=params))
        return invoker.execute(action, params, {"conversation_id": "session-video", "surface": "home"})

    try:
        topic = execute("zimeiti.add", {"title": "Agent 为什么需要你点头"})
        assert topic.success
        saved = execute("zimeiti.article_save", {
            "id": topic.data["id"], "content": "Agent 会规划、调用工具，并把结果交还给用户确认。",
            "note": "初稿",
        })
        assert saved.success
        material = execute("zimeiti.mat_save", {
            "text": "Agent 系统由模型、工具、记忆与权限治理共同组成。", "defer": True,
        })
        assert material.success

        view = graph.workspace_view("video")
        assert {item["type"] for item in view["objects"]} == {
            "zimeiti.topic", "video.script", "research.evidence",
        }
        assert view["workflow_run"]["current_stage_id"] == "storyboard"
        assert len(graph.evidence_views("video")) == 1
        script = next(item for item in view["objects"] if item["type"] == "video.script")
        revision = graph.artifact_view(script["artifact_id"])["revisions"][-1]
        assert revision["content_ref"].startswith("blob://sha256/")
        assert reg.get("zimeiti.article_save").plugin_ctx.blobs.resolve(
            revision["content_ref"],
        ).read_text(encoding="utf-8").startswith("Agent 会规划")
        assert [item["status"] for item in graph.invocation_views(workspace_id="video")] == [
            "succeeded", "succeeded", "succeeded",
        ]
        outbox = reg.get("zimeiti.add").plugin_ctx.db.work_outbox_events()
        assert len(outbox) == 3
        assert {item["status"] for item in outbox} == {"acknowledged"}
    finally:
        graph.close()

_S0_COLS = ("hkrr", "hook_type", "target_platform", "cover_concepts", "project_id")


def test_add_writes_s0_video_fields(env):
    """S0 选题卡：add 收 4 个视频字段（hkrr/cover_concepts 为 JSON 文本），落库原样可读。"""
    reg, _, _ = env
    hkrr = json.dumps({"happy": "解压向", "knowledge": "", "resonance": "打工人心声",
                       "rhythm": "可行"}, ensure_ascii=False)  # 快乐/知识/共鸣 ≥1 + 节奏可行/存疑
    covers = json.dumps(["大字报标题+人物表情", "对比分屏", "结果前置截图"], ensure_ascii=False)
    r = _run(reg, "zimeiti.add", {"title": "视频选题", "hkrr": hkrr, "hook_type": "反常识",
                                  "target_platform": "B站", "cover_concepts": covers})
    assert r.success
    row = _run(reg, "zimeiti.get", {"id": r.data["id"]}).data["rows"][0]
    assert row["hkrr"] == hkrr and row["hook_type"] == "反常识" and row["target_platform"] == "B站"
    assert json.loads(row["cover_concepts"]) == ["大字报标题+人物表情", "对比分屏", "结果前置截图"]


def test_add_s0_fields_default_empty(env):
    """防御：只给标题 → S0 新列全部落空串默认值（不炸、不缺键）；聚合展示字段同步给空。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "裸选题"}).data["id"]
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    for col in _S0_COLS:
        assert row[col] == ""
    assert row["hkrr_happy"] == "" and row["cover_1"] == "" and row["project"] == ""


def test_add_coerces_structured_params_to_json(env):
    """防御：LLM 把 hkrr 给成 dict / cover_concepts 给成 list 时落 JSON 文本，不是绑定报错。"""
    reg, _, _ = env
    r = _run(reg, "zimeiti.add", {"title": "T", "hkrr": {"happy": "解压"}, "cover_concepts": ["a", "b", "c"]})
    assert r.success
    row = _run(reg, "zimeiti.get", {"id": r.data["id"]}).data["rows"][0]
    assert json.loads(row["hkrr"]) == {"happy": "解压"}
    assert json.loads(row["cover_concepts"]) == ["a", "b", "c"]
    assert row["hkrr_happy"] == "解压" and row["cover_3"] == "c"  # 详情聚合已拆平


def test_get_enriches_s0_display_bad_json_degrades(env):
    """详情聚合把 hkrr/cover_concepts JSON 拆平成行；坏 JSON / 类型不对降级空串，不炸。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    db = reg.get("zimeiti.get").plugin_ctx.db
    db.update("topics", tid, {"hkrr": "这不是 JSON", "cover_concepts": '{"not": "a list"}'})
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["hkrr_happy"] == "" and row["hkrr_rhythm"] == "" and row["cover_1"] == ""


def test_update_writes_s0_fields(env):
    """update 承接 S0 字段修订（含立项流程回写的 project_id），不动未传字段。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T", "angle": "旧角度"}).data["id"]
    assert _run(reg, "zimeiti.update", {"id": tid, "hook_type": "悬念", "project_id": "proj_x"}).success
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["hook_type"] == "悬念" and row["project_id"] == "proj_x" and row["angle"] == "旧角度"


def test_topics_table_migration_adds_s0_columns(tmp_path):
    """老库迁移：老 schema（无 S0 列）的 topics 有存量行，按新 manifest apply → ALTER 补列、数据不动。"""
    import tomllib

    from yibao_brain.plugindb import PluginDb

    doc = tomllib.loads((ZIMEITI_DIR / "manifest.toml").read_text(encoding="utf-8"))
    spec = next(t for t in doc["table"] if t["name"] == "topics")
    old_spec = {**spec, "columns": [c for c in spec["columns"] if c["name"] not in _S0_COLS]}
    path = str(tmp_path / "migrate" / "data.db")
    old = PluginDb("zimeiti", db_path=path)
    old.apply_schema([old_spec])
    rid = old.insert("topics", {"title": "老选题", "created_at": 1, "updated_at": 1})
    old.close()

    new = PluginDb("zimeiti", db_path=path)
    new.apply_schema([spec])
    row = new.query("topics", where={"id": rid})[0]
    assert row["title"] == "老选题"  # 存量数据不动
    for col in _S0_COLS:
        assert row[col] == ""  # 新列带默认值补齐
    new.apply_schema([spec])  # 重复 apply 幂等不炸
    new.close()


# ---------- 立项（S0 相变：选题 → 项目实体，L3 闸门卡由系统弹） ----------


def test_promote_api_registered_intent(env):
    """详情卡「立项」= intent 方法：走 agent 流程调 project.create（面板不直调绕过闸门）。"""
    env  # 触发加载
    api = get_api("zimeiti.promote")
    assert api is not None and not api.direct and api.handler == "zimeiti.get"
    assert "{title}" in api.intent and "{id}" in api.intent
    assert "project.create" in api.intent and "zimeiti.topic" in api.intent and "zimeiti.update" in api.intent


def test_promote_intent_renders_params(env):
    """intent 模板渲染：{title}/{id} 占位被面板参数替换干净。"""
    from yibao_brain.panel import _render_intent

    env
    text = _render_intent(get_api("zimeiti.promote"), {"id": "t1", "title": "AI 桌宠的一天"})
    assert "AI 桌宠的一天" in text and "t1" in text and "{title}" not in text and "{id}" not in text


def test_promote_flow_attach_and_writeback(env, data_dir, monkeypatch):
    """立项链路（模拟 agent 按印后的两步）：project.create 挂选题 → update 回写 → get 给「已立项 → 项目名」。"""
    from yibao_brain import config
    from yibao_brain.project_tools import make_project_tools
    from yibao_brain.projects import ProjectStore

    monkeypatch.setattr(config, "settings_path", lambda: str(data_dir / "settings.json"))
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "立项选题"}).data["id"]
    tools = {t.id: t for t in make_project_tools(ProjectStore(str(data_dir / "projects.json")))}

    r = tools["project.create"].run(
        {"name": "立项选题", "objects": [{"type": "zimeiti.topic", "ref": tid}]}, None)
    assert r.success
    proj = r.data["project"]
    assert proj["objects"] == [{"type": "zimeiti.topic", "ref": tid}]  # 选题已挂进新项目

    assert _run(reg, "zimeiti.update", {"id": tid, "project_id": proj["id"]}).success
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["project_id"] == proj["id"]
    assert row["project"] == f"已立项 → {proj['name']}"


def test_project_label_fallback_when_registry_missing(env):
    """防御：project_id 有值但 projects.json 不存在/读不到 → 保底显示 id，不炸。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.update", {"id": tid, "project_id": "proj_ghost"})
    row = _run(reg, "zimeiti.get", {"id": tid}).data["rows"][0]
    assert row["project"] == "已立项 → proj_ghost"


# ---------- wewrite CLI（ww_hotspots / ww_score：subprocess 薄封装，WEWRITE_HOME 指插件数据目录） ----------


def _completed(stdout="", stderr="", returncode=0):
    class _R:
        def __init__(self):
            self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
    return _R()


_HOTSPOTS_JSON = json.dumps({
    "sources": ["baidu", "toutiao", "weibo"],
    "sources_failed": ["weibo"],
    "items": [
        {"title": "AI 编程工具大战", "source": "今日头条", "hot": 24442080,
         "hot_normalized": 100.0, "url": "https://www.toutiao.com/trending/1/", "description": ""},
        {"title": "高考分数线公布", "source": "百度", "hot": 0,
         "hot_normalized": 98.5, "url": "https://m.baidu.com/s?word=x", "description": "热"},
    ],
})

_SCORE_JSON = json.dumps({
    "quality_score": 76.88,
    "composite_score": 23.12,
    "char_count": 1200,
    "tier1": {k: {"score": 1.0, "detail": f"d-{k}"} for k in (
        "sentence_length_stddev", "sentence_length_range", "paragraph_length_variance",
        "vocabulary_richness", "emotional_balance", "adverb_density")} | {"_summary": {"mean_score": 1.0}},
    "tier2": {k: {"score": 0.5, "detail": f"d-{k}"} for k in (
        "banned_words", "sentence_integrity", "real_sources",
        "register_consistency", "insertion_control")} | {"_summary": {"mean_score": 0.5}},
    "tier3": {"score": None, "source": "not_available"},
})


def test_ww_tools_registered_readonly(env):
    """两个 wewrite 工具随插件加载注册，只读级风险（ subprocess 只拉数据/打分，不改状态）。"""
    from yibao_brain.ipc import RiskLevel

    reg, _, _ = env
    assert reg.get("zimeiti.ww_hotspots").default_risk == RiskLevel.L0_READONLY
    assert reg.get("zimeiti.ww_score").default_risk == RiskLevel.L0_READONLY


def test_ww_hotspots_parses_json_and_wewrite_home(env, monkeypatch, data_dir):
    """正常 JSON 解析：items 六字段 + sources_failed 透传；WEWRITE_HOME 落插件数据目录 wewrite/。"""
    import subprocess

    seen = {}

    def _fake(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env") or {}
        return _completed(stdout=_HOTSPOTS_JSON)

    monkeypatch.setattr(subprocess, "run", _fake)
    reg, _, _ = env
    r = _run(reg, "zimeiti.ww_hotspots", {"limit": 5})
    assert r.success and r.data["sources_failed"] == ["weibo"]
    items = r.data["items"]
    assert [it["title"] for it in items] == ["AI 编程工具大战", "高考分数线公布"]
    assert set(items[0]) == {"title", "source", "hot", "hot_normalized", "url", "description"}
    assert items[1]["hot_normalized"] == 98.5
    assert seen["cmd"][1:] == ["hotspots", "--limit", "5"]
    home = seen["env"]["WEWRITE_HOME"]
    assert str(data_dir) in home and home.endswith("wewrite")  # 不写默认 ~/.wewrite


def test_ww_hotspots_limit_default_and_clamp(env, monkeypatch):
    import subprocess

    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or _completed(stdout=_HOTSPOTS_JSON))
    reg, _, _ = env
    _run(reg, "zimeiti.ww_hotspots", {})
    _run(reg, "zimeiti.ww_hotspots", {"limit": 999})
    _run(reg, "zimeiti.ww_hotspots", {"limit": "abc"})
    assert calls[0][-1] == "20" and calls[1][-1] == "50" and calls[2][-1] == "20"


def test_ww_hotspots_cli_missing(env, monkeypatch):
    import subprocess

    def _fake(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", _fake)
    reg, _, _ = env
    r = _run(reg, "zimeiti.ww_hotspots", {})
    assert not r.success and "wewrite" in r.error and "install" in r.error


def test_ww_hotspots_nonzero_exit(env, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _completed(returncode=2, stderr="boom: 网络不通"))
    reg, _, _ = env
    r = _run(reg, "zimeiti.ww_hotspots", {})
    assert not r.success and "退出码 2" in r.error and "boom" in r.error


def test_ww_hotspots_timeout_and_bad_json(env, monkeypatch):
    import subprocess

    def _timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", _timeout)
    reg, _, _ = env
    assert "超时" in _run(reg, "zimeiti.ww_hotspots", {}).error
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(stdout="不是 JSON"))
    assert "解析失败" in _run(reg, "zimeiti.ww_hotspots", {}).error


def test_ww_score_happy(env, monkeypatch):
    """对最新稿跑 wewrite score：临时稿文件喂 CLI、跑完即删；返回分数 + 11 项检测（_summary 不计）。"""
    import subprocess

    seen = {}

    def _fake(cmd, **kw):
        seen["cmd"] = cmd
        seen["existed"] = Path(cmd[2]).is_file()
        seen["content"] = Path(cmd[2]).read_text(encoding="utf-8")
        return _completed(stdout=_SCORE_JSON)

    monkeypatch.setattr(subprocess, "run", _fake)
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 初稿\n正文"})
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 二稿\n正文"})
    r = _run(reg, "zimeiti.ww_score", {"topic_id": tid})
    assert r.success and r.data["version"] == 2  # 打的是最新版稿
    assert r.data["quality_score"] == 76.88 and r.data["composite_score"] == 23.12
    assert r.data["char_count"] == 1200 and r.data["tier3_score"] is None
    checks = r.data["checks"]
    assert len(checks) == 11 and sum(c["tier"] == "tier1" for c in checks) == 6
    assert checks[0]["name"] == "sentence_length_stddev" and checks[0]["detail"] == "d-sentence_length_stddev"
    assert seen["cmd"][1] == "score" and seen["cmd"][3] == "--json"
    assert seen["existed"] and seen["content"] == "# 二稿\n正文"
    assert not Path(seen["cmd"][2]).exists()  # 临时稿文件跑完清理


def test_ww_score_rejects_bad_input(env):
    """无稿/选题不存在/缺参都给友好错误（不调 CLI）。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    r = _run(reg, "zimeiti.ww_score", {"topic_id": tid})
    assert not r.success and "还没有稿件" in r.error
    assert not _run(reg, "zimeiti.ww_score", {"topic_id": "missing"}).success
    assert not _run(reg, "zimeiti.ww_score", {}).success


def test_ww_score_cli_failure(env, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _completed(returncode=1, stderr="score boom"))
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "x"})
    r = _run(reg, "zimeiti.ww_score", {"topic_id": tid})
    assert not r.success and "退出码 1" in r.error and "score boom" in r.error


# ---------- night_brief（守夜人夜间流水线：抓热点 → 定选题 → 起稿 → 晨报） ----------


class _SeqLlm:
    """按调用顺序返回预置回复的 ctx.llm 替身（先选题 JSON、再初稿文本）。"""

    def __init__(self, replies):
        self._replies = list(replies)
        self.prompts = []

    def chat(self, prompt):
        self.prompts.append(prompt)
        return self._replies.pop(0)


_PICKS_JSON = json.dumps([
    {"title": "AI 编程工具大战，输家已经注定", "angle": "从开发者流失切入", "platform": "公众号",
     "reason": "挂上头条热一", "url": "https://www.toutiao.com/trending/1/"},
    {"title": "高考分数线背后的信号", "angle": "给家长看", "platform": "知乎",
     "reason": "百度热二", "url": "https://m.baidu.com/s?word=x"},
    {"title": "蹭热点第三条", "angle": "x", "platform": "小红书", "reason": "r", "url": ""},
])

_DRAFT = "字" * 700  # 初稿硬闸门：中文字数 ≥600


def _night_env(env, monkeypatch, replies):
    """加载插件 + 假 wewrite CLI（记调用）+ 假顺序 LLM；返回 (tool, llm, cli_calls)。"""
    import subprocess

    cli_calls = []

    def _fake_run(cmd, **kw):
        cli_calls.append(cmd)
        return _completed(stdout=_HOTSPOTS_JSON)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    reg, _, _ = env
    t = reg.get("zimeiti.night_brief")
    llm = _SeqLlm(replies)
    t.plugin_ctx.llm = llm
    return t, llm, cli_calls


def test_night_brief_happy_path(env, monkeypatch):
    """全链：热点 → LLM 定 3 个选题落库（source=守夜人）→ 头名起 v1 稿转写作中 → 晨报文本。"""
    t, llm, cli_calls = _night_env(env, monkeypatch, [_PICKS_JSON, _DRAFT])
    r = t.run({}, t.plugin_ctx)
    assert r.success and r.panel is None  # 夜里不弹任何面板
    assert r.data["drafted"] is True and len(r.data["topics"]) == 3
    assert len(cli_calls) == 1 and "hotspots" in cli_calls[0]
    assert len(llm.prompts) == 2  # 选题一次 + 起稿一次
    assert "为什么现在写" in llm.prompts[0] and "钩子" in llm.prompts[1]  # 方法论进了 prompt

    db = t.plugin_ctx.db
    rows = db.query("topics", order="updated_at DESC", limit=100)
    assert len(rows) == 3 and all(row["source"] == "守夜人" for row in rows)
    top = next(row for row in rows if row["title"] == "AI 编程工具大战，输家已经注定")
    assert top["status"] == "写作中" and top["url"] == "https://www.toutiao.com/trending/1/"
    assert all(row["status"] == "候选" for row in rows if row["id"] != top["id"])  # 只头名起稿
    arts = db.query("articles", where={"topic_id": top["id"]})
    assert len(arts) == 1 and arts[0]["version"] == 1 and arts[0]["note"] == "守夜人初稿"
    assert (t._root / arts[0]["content_path"]).is_file()  # 稿件照 article_save 约定落盘

    human = r.data["human"]
    assert "守夜人晨报" in human and "AI 编程工具大战，输家已经注定" in human
    assert "要哪个直接说" in human  # 收尾：发布永远等用户拍板


def test_night_brief_resume_skips_done_steps(env, monkeypatch):
    """断点续跑：每步落盘 night/<date>.json，同日期重跑不再调 CLI / LLM，直接出晨报。"""
    t, llm, cli_calls = _night_env(env, monkeypatch, [_PICKS_JSON, _DRAFT])
    assert t.run({"date": "2026-08-26"}, t.plugin_ctx).success
    assert len(cli_calls) == 1 and len(llm.prompts) == 2
    r = t.run({"date": "2026-08-26"}, t.plugin_ctx)
    assert r.success and len(cli_calls) == 1 and len(llm.prompts) == 2  # 零新增调用
    assert "AI 编程工具大战，输家已经注定" in r.data["human"]
    assert len(t.plugin_ctx.db.query("topics", limit=100)) == 3  # 不重复转选题


def test_night_brief_pick_json_retry_once(env, monkeypatch):
    """选题 JSON 解析失败重试一次：第一次垃圾、第二次 ```json 围栏 → 成功。"""
    t, llm, _ = _night_env(env, monkeypatch,
                           ["这不是 JSON", "```json\n" + _PICKS_JSON + "\n```", _DRAFT])
    r = t.run({}, t.plugin_ctx)
    assert r.success and len(llm.prompts) == 3  # 选题两次 + 起稿一次


def test_night_brief_pick_unparseable_twice_fails_step(env, monkeypatch):
    """两次都解析不了 → 整步报错（不硬编选题）；热点步已落盘，补跑从选题继续。"""
    t, llm, cli_calls = _night_env(env, monkeypatch, ["垃圾", "还是垃圾"])
    r = t.run({}, t.plugin_ctx)
    assert not r.success and "定选题" in r.error
    llm._replies = [_PICKS_JSON, _DRAFT]  # 补跑：CLI 不重调（热点已在状态里）
    r2 = t.run({}, t.plugin_ctx)
    assert r2.success and len(cli_calls) == 1


def test_night_brief_dedupes_board_and_batch(env, monkeypatch):
    """硬闸门：撞看板标题的丢、本批重复的丢、最多保留 3 个。"""
    picks = json.dumps([
        {"title": "已有选题", "angle": "", "platform": "公众号", "reason": "r", "url": ""},
        {"title": "新选题 A", "angle": "", "platform": "公众号", "reason": "r", "url": ""},
        {"title": "新选题 A", "angle": "", "platform": "知乎", "reason": "r", "url": ""},
        {"title": "新选题 B", "angle": "", "platform": "知乎", "reason": "r", "url": ""},
        {"title": "新选题 C", "angle": "", "platform": "小红书", "reason": "r", "url": ""},
        {"title": "新选题 D", "angle": "", "platform": "小红书", "reason": "r", "url": ""},
        {"title": "", "angle": "", "platform": "", "reason": "r", "url": ""},  # 空标题丢
    ])
    t, llm, _ = _night_env(env, monkeypatch, [picks, _DRAFT])
    _run(env[0], "zimeiti.add", {"title": "已有选题"})
    r = t.run({}, t.plugin_ctx)
    assert r.success
    titles = [row["title"] for row in t.plugin_ctx.db.query("topics", limit=100)]
    assert sorted(titles) == ["已有选题", "新选题 A", "新选题 B", "新选题 C"]


def test_night_brief_draft_too_short_marks_failed_continues(env, monkeypatch):
    """起稿字数不够重试一次仍不够 → 该选题标「起稿失败」，整 run 不失败。"""
    t, llm, _ = _night_env(env, monkeypatch, [_PICKS_JSON, "太短", "还是太短"])
    r = t.run({}, t.plugin_ctx)
    assert r.success and r.data["drafted"] is False
    assert "起稿失败" in r.data["human"]
    top = next(row for row in t.plugin_ctx.db.query("topics", limit=100)
               if row["title"] == "AI 编程工具大战，输家已经注定")
    assert top["status"] == "候选"  # 没写成稿不流转


def test_night_brief_hotspots_failure_fails_run(env, monkeypatch):
    """抓热点失败 → 整 run 报错（没热点不硬编），且不落任何状态。"""
    import subprocess

    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: _completed(returncode=1, stderr="boom"))
    reg, _, _ = env
    t = reg.get("zimeiti.night_brief")
    t.plugin_ctx.llm = _SeqLlm([])
    r = t.run({}, t.plugin_ctx)
    assert not r.success and "抓热点" in r.error
    assert t.plugin_ctx.db.query("topics", limit=100) == []


def test_night_brief_rejects_bad_date_and_no_llm(env, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _completed(stdout=_HOTSPOTS_JSON))
    reg, _, _ = env
    t = reg.get("zimeiti.night_brief")
    t.plugin_ctx.llm = _SeqLlm([_PICKS_JSON, _DRAFT])
    assert not t.run({"date": "昨天"}, t.plugin_ctx).success
    t.plugin_ctx.llm = None
    r = t.run({}, t.plugin_ctx)
    assert not r.success and "LLM" in r.error
