"""守夜人（隔夜任务运行器）测试：NightStore 存取/重排/持久化 + night_set/list/cancel 校验
+ _night_tick 到期执行与分发（成功/失败/工具不存在三条路）。"""
import asyncio
import json
import time

import pytest

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.nightwatch import NightStore, make_skills
from yibao_brain.tools import Tool, ToolRegistry


@pytest.fixture
def store(tmp_path):
    return NightStore(str(tmp_path / "nightwatch.json"))


# ---------- 假插件工具（night_set 只调度插件工具：id 必须带 "."） ----------


class _OkTool(Tool):
    id = "plug.ok"
    label = "内容创作·夜间流水线"
    default_risk = RiskLevel.L1_LOW

    def run(self, params, ctx):
        return ActionResult(success=True, data={"human": "晨报文本"})


class _RiskyTool(Tool):
    id = "plug.risky"
    default_risk = RiskLevel.L2_MEDIUM

    def run(self, params, ctx):
        raise AssertionError("不该被执行")


@pytest.fixture
def reg():
    r = ToolRegistry()
    r.register(_OkTool(), plugin="plug")
    r.register(_RiskyTool(), plugin="plug")
    return r


def _skill(store, reg, sid):
    return {s.id: s for s in make_skills(store, reg)}[sid]


# ---------- 存储 ----------


def test_store_add_list_cancel(store):
    j = store.add("plug.ok", {}, "夜间流水线", time.time() + 3600)
    assert j["id"] and not j["fired"] and j["rrule"] is None and j["last_status"] is None
    assert [i["name"] for i in store.list_pending()] == ["夜间流水线"]
    gone = store.cancel(j["id"])
    assert gone and gone["name"] == "夜间流水线"
    assert store.list_pending() == []
    assert store.cancel("不存在") is None


def test_store_cancel_by_id_prefix(store):
    j = store.add("plug.ok", {}, "x", time.time() + 60)
    assert store.cancel(j["id"][:4]) is not None  # 前缀可取消（LLM 可能截断 id）


def test_store_pop_due_once(store):
    store.add("plug.ok", {}, "已到期", time.time() - 1)
    store.add("plug.ok", {}, "未到期", time.time() + 3600)
    due = store.pop_due(time.time())
    assert [r["name"] for r in due] == ["已到期"]
    assert store.pop_due(time.time()) == []  # 一次性：不重复触发
    assert [i["name"] for i in store.list_pending()] == ["未到期"]


def test_pop_due_daily_reschedules(store):
    j = store.add("plug.ok", {}, "每天跑", time.time() - 1, rrule="daily")
    due = store.pop_due(time.time())
    assert [r["id"] for r in due] == [j["id"]]  # 本次照样触发
    pending = store.list_pending()
    assert len(pending) == 1 and pending[0]["fire_at"] > time.time()  # 重排到未来
    assert store.pop_due(time.time()) == []  # 不会立刻再触发


def test_pop_due_daily_missed_days_skips_to_future(store):
    store.add("plug.ok", {}, "睡过了", time.time() - 3 * 86400 - 60, rrule="daily")
    due = store.pop_due(time.time())
    assert len(due) == 1  # 关机错过的日子只补一次，不刷屏
    assert store.list_pending()[0]["fire_at"] > time.time()


def test_store_add_rejects_unknown_rrule(store):
    with pytest.raises(ValueError):
        store.add("plug.ok", {}, "x", time.time() + 60, rrule="weekly")


def test_store_persists_across_reload(tmp_path):
    path = str(tmp_path / "nightwatch.json")
    s1 = NightStore(path)
    s1.add("plug.ok", {"a": 1}, "重启后还在", time.time() + 3600)
    s2 = NightStore(path)
    items = s2.list_pending()
    assert [i["name"] for i in items] == ["重启后还在"] and items[0]["params"] == {"a": 1}


def test_store_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "nightwatch.json"
    p.write_text("not json", encoding="utf-8")
    s = NightStore(str(p))
    assert s.list_pending() == []  # 坏文件从空开始，不阻断启动


def test_mark_result(store, tmp_path):
    j = store.add("plug.ok", {}, "x", time.time() - 1, rrule="daily")
    store.pop_due(time.time())
    store.mark_result(j["id"], True)
    store.mark_result(j["id"], False, "炸了")
    item = store.list_pending()[0]  # daily 仍在待触发里，带上次结果
    assert item["last_status"] == "error" and item["last_error"] == "炸了"
    assert item["last_run_at"] > 0
    # 持久化：重建实例读得到
    again = NightStore(str(tmp_path / "nightwatch.json"))
    assert again.list_pending()[0]["last_status"] == "error"


# ---------- night_set 校验 ----------


def test_night_set_happy(store, reg):
    r = _skill(store, reg, "night_set").run({"tool": "plug.ok", "delay_minutes": 60}, None)
    assert r.success and r.data["fire_at"] > time.time() + 3500
    assert "内容创作·夜间流水线" in r.data["human"]
    assert store.list_pending()[0]["tool"] == "plug.ok"


def test_night_set_daily_human(store, reg):
    r = _skill(store, reg, "night_set").run({"tool": "plug.ok", "at": "23:30", "repeat": "daily"}, None)
    assert r.success and "每天 23:30" in r.data["human"]
    assert store.list_pending()[0]["rrule"] == "daily"


def test_night_set_rejects_non_plugin_tool(store, reg):
    r = _skill(store, reg, "night_set").run({"tool": "echo", "delay_minutes": 60}, None)
    assert not r.success and "插件工具" in r.error  # 底座工具 id 无点号，拒


def test_night_set_rejects_unregistered_tool(store, reg):
    r = _skill(store, reg, "night_set").run({"tool": "plug.missing", "delay_minutes": 60}, None)
    assert not r.success and "没有这个工具" in r.error


def test_night_set_rejects_high_risk_tool(store, reg):
    """L2+ 夜里会弹确认（无人值守）——布置时就拒掉。"""
    r = _skill(store, reg, "night_set").run({"tool": "plug.risky", "delay_minutes": 60}, None)
    assert not r.success and "L2" in r.error
    assert store.list_pending() == []


def test_night_set_rejects_bad_input(store, reg):
    sk = _skill(store, reg, "night_set")
    assert not sk.run({}, None).success                                  # 没给 tool
    assert not sk.run({"tool": "plug.ok"}, None).success                 # 没时间
    assert not sk.run({"tool": "plug.ok", "delay_minutes": "abc"}, None).success  # 非数字
    assert not sk.run({"tool": "plug.ok", "delay_minutes": 0.01}, None).success   # 太短
    assert not sk.run({"tool": "plug.ok", "at": "垃圾"}, None).success            # 看不懂
    assert not sk.run({"tool": "plug.ok", "at": "2000-01-01 00:00"}, None).success  # 已过
    assert not sk.run({"tool": "plug.ok", "delay_minutes": 60, "repeat": "weekly"}, None).success
    assert not sk.run({"tool": "plug.ok", "delay_minutes": 60, "params": "x"}, None).success
    assert store.list_pending() == []  # 全部拒绝，没落进任何脏数据


def test_night_list_and_cancel(store, reg):
    _skill(store, reg, "night_set").run({"tool": "plug.ok", "delay_minutes": 30, "name": "A"}, None)
    _skill(store, reg, "night_set").run({"tool": "plug.ok", "delay_minutes": 60, "name": "B"}, None)
    r = _skill(store, reg, "night_list").run({}, None)
    assert r.success and r.data["count"] == 2
    assert "A" in r.data["human"] and "plug.ok" in r.data["human"]
    rid = store.list_pending()[0]["id"]
    r = _skill(store, reg, "night_cancel").run({"id": rid[:4]}, None)
    assert r.success and "已取消" in r.data["human"]
    assert _skill(store, reg, "night_list").run({}, None).data["count"] == 1
    assert not _skill(store, reg, "night_cancel").run({"id": rid}, None).success  # 已取消不能再取消


def test_night_list_empty(store, reg):
    r = _skill(store, reg, "night_list").run({}, None)
    assert r.success and r.data["count"] == 0 and "没有" in r.data["human"]


def test_night_skills_registerable_as_base_skills(store, reg):
    """底座技能注册契约：id 禁点号（防伪装插件）——点号命名曾把大脑启动直接干崩。"""
    fresh = ToolRegistry()
    for sk in make_skills(store, reg):
        fresh.register(sk)
    assert fresh.get("night_set") is not None


# ---------- _night_tick：到期 → 执行 → 分发 → 落历史 ----------


class _Feed:
    def __init__(self):
        self.rows = []

    def add(self, kind, text, meta):
        self.rows.append((kind, text, meta))


class _Hist:
    def __init__(self):
        self.msgs = []

    def record_messages(self, msgs):
        self.msgs.extend(msgs)


class _Agent:
    def __init__(self, reg):
        self.skills = reg
        self.history = _Hist()


def _tick(store, reg, out):
    from yibao_brain.background import _night_tick

    feed = _Feed()
    asyncio.run(_night_tick(store=store, agent=_Agent(reg), settings={},
                            feed=feed, write_msg=out.append, dispatcher=None))
    return feed


def test_night_tick_success_dispatches_brief(store, reg):
    store.add("plug.ok", {}, "夜间流水线", time.time() - 1)
    out: list[dict] = []
    feed = _tick(store, reg, out)
    assert feed.rows and feed.rows[0][0] == "reminder" and feed.rows[0][1] == "晨报文本"
    assert feed.rows[0][2]["nid"]  # 带任务 id 供回溯
    evs = [m["event"] for m in out]
    assert evs == [{"kind": "reminder", "type": "night_job", "text": "晨报文本", "level": "full"}]
    item = json.loads(open(store._path, encoding="utf-8").read())[0]
    assert item["fired"] and item["last_status"] == "ok"
    assert store.pop_due(time.time()) == []  # 一次性不重复触发


def test_night_tick_records_history(store, reg):
    agent_hist = _Hist()

    class _A:
        skills = reg
        history = agent_hist

    from yibao_brain.background import _night_tick

    store.add("plug.ok", {}, "夜间流水线", time.time() - 1)
    asyncio.run(_night_tick(store=store, agent=_A(), settings={},
                            feed=_Feed(), write_msg=lambda m: None, dispatcher=None))
    assert agent_hist.msgs == [{"role": "assistant", "content": "晨报文本"}]


def test_night_tick_failure_still_reports(store, reg):
    """执行失败 → mark_result error + 落一条失败消息（fail-closed：人必须知道夜里没跑成）。"""

    class _Boom(Tool):
        id = "plug.boom"
        default_risk = RiskLevel.L1_LOW

        def run(self, params, ctx):
            return ActionResult(success=False, error="LLM 超时")

    reg.register(_Boom(), plugin="plug")
    store.add("plug.boom", {}, "夜间流水线", time.time() - 1)
    out: list[dict] = []
    feed = _tick(store, reg, out)
    assert "夜间任务失败" in feed.rows[0][1] and "LLM 超时" in feed.rows[0][1]
    item = json.loads(open(store._path).read())[0]
    assert item["last_status"] == "error" and "LLM 超时" in item["last_error"]


def test_night_tick_tool_missing(store, reg):
    store.add("plug.gone", {}, "夜间流水线", time.time() - 1)
    out: list[dict] = []
    feed = _tick(store, reg, out)
    assert "夜间任务失败" in feed.rows[0][1] and "工具不存在" in feed.rows[0][1]
    item = json.loads(open(store._path).read())[0]
    assert item["last_status"] == "error"


def test_night_tick_exception_in_run(store, reg):
    class _Raise(Tool):
        id = "plug.raise"
        default_risk = RiskLevel.L1_LOW

        def run(self, params, ctx):
            raise RuntimeError("炸了")

    reg.register(_Raise(), plugin="plug")
    store.add("plug.raise", {}, "夜间流水线", time.time() - 1)
    feed = _tick(store, reg, [])
    assert "夜间任务失败" in feed.rows[0][1] and "炸了" in feed.rows[0][1]


def test_night_tick_daily_stays_pending(store, reg):
    store.add("plug.ok", {}, "每天", time.time() - 1, rrule="daily")
    _tick(store, reg, [])
    pending = store.list_pending()
    assert len(pending) == 1 and pending[0]["fire_at"] > time.time()
    assert pending[0]["last_status"] == "ok"


def test_night_tick_quiet_level_skips_broadcast_but_feeds(store, reg):
    """quiet 档：不广播事件，但 Feed/历史照落（与 reminder 同一底线）。"""
    from yibao_brain.background import _night_tick

    store.add("plug.ok", {}, "夜间流水线", time.time() - 1)
    out: list[dict] = []
    feed = _Feed()
    asyncio.run(_night_tick(store=store, agent=_Agent(reg), settings={"proactive.level": "quiet"},
                            feed=feed, write_msg=out.append, dispatcher=None))
    assert out == []  # 不亮窗
    assert feed.rows and feed.rows[0][1] == "晨报文本"  # Feed 照落
