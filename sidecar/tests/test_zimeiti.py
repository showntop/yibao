"""zimeiti 插件（自媒体：选题+写作）端到端测试：加载真实 plugins/zimeiti/（数据目录重定向到 tmp）。

覆盖：声明式 CRUD/状态流转全链 + 代码 tool（article_save/article_read 版本管理）
+ bundled skill（skills/write/SKILL.md 成文框架，use_skill 展开）
+ api.toml 白名单 + 面板 schema 与 api 方法的一致性 + webview 编辑器面板。
"""
import asyncio
import json
from pathlib import Path

import pytest

from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.plugins import LlmChat, get_api, load_plugins
from yibao_brain.tools import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
ZIMEITI_DIR = REPO_ROOT / "plugins" / "zimeiti"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("YIBAO_DATA_DIR", str(d))
    return d


@pytest.fixture
def env(data_dir):
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
    }
    for tid, risk in expected.items():
        assert reg.get(tid).default_risk == risk, tid


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
    """delete 代码承接（2026-08-25）：级联清稿件（行+文件+目录）、素材关联、发布数据。"""
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
    assert not p1.exists() and not p2.exists() and not p1.parent.exists()  # 文件+目录清掉
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


# ---------- article_save / article_read（版本管理） ----------


def test_article_save_versions_and_status_flow(env, data_dir):
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]

    r1 = _run(reg, "zimeiti.article_save", {"id": tid, "content": "# 初稿", "note": "初稿"})
    assert r1.success and r1.data["version"] == 1 and r1.panel == "zimeiti:detail"
    path1 = Path(r1.data["path"])
    assert path1.is_file() and path1.name == "v1.md"
    assert data_dir in path1.parents  # 落在插件数据目录，不污染仓库
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


def test_article_save_stores_relative_path_and_prunes(env):
    """content_path 落相对路径（迁移不炸）；版本治理：保留最近 20 版。"""
    reg, _, _ = env
    tid = _run(reg, "zimeiti.add", {"title": "T"}).data["id"]
    _run(reg, "zimeiti.article_save", {"id": tid, "content": "v1"})
    rows = reg.get("zimeiti.list").plugin_ctx.db.query("articles", where={"topic_id": tid})
    assert rows[0]["content_path"].startswith("articles/")  # 相对路径
    # article_read 兼容解析（相对 → 拼数据根）
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
