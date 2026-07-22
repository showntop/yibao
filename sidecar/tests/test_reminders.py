"""主动提醒：存储 / 时间解析 / 三个技能。"""
from __future__ import annotations

import time

import pytest

from yibao_brain.reminders import ReminderStore, _parse_at, make_skills


@pytest.fixture()
def store(tmp_path):
    return ReminderStore(str(tmp_path / "reminders.json"))


def _skill(store, sid):
    return next(s for s in make_skills(store) if s.id == sid)


# ---------- 存储 ----------

def test_store_add_list_cancel(store):
    r = store.add("关火", time.time() + 3600)
    assert r["id"] and not r["fired"]
    assert [i["text"] for i in store.list_pending()] == ["关火"]
    gone = store.cancel(r["id"])
    assert gone and gone["text"] == "关火"
    assert store.list_pending() == []
    assert store.cancel("不存在") is None


def test_store_cancel_by_id_prefix(store):
    r = store.add("喝水", time.time() + 60)
    assert store.cancel(r["id"][:4]) is not None  # 前缀可取消（LLM 可能截断 id）


def test_store_pop_due(store):
    store.add("已到期", time.time() - 1)
    store.add("未到期", time.time() + 3600)
    due = store.pop_due(time.time())
    assert [r["text"] for r in due] == ["已到期"]
    assert store.pop_due(time.time()) == []  # 不重复触发
    assert [i["text"] for i in store.list_pending()] == ["未到期"]


def test_store_persists_across_reload(tmp_path):
    path = str(tmp_path / "reminders.json")
    s1 = ReminderStore(path)
    s1.add("重启后还在", time.time() + 3600)
    s2 = ReminderStore(path)
    assert [i["text"] for i in s2.list_pending()] == ["重启后还在"]


def test_store_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "reminders.json"
    p.write_text("not json", encoding="utf-8")
    s = ReminderStore(str(p))
    assert s.list_pending() == []  # 坏文件从空开始，不阻断启动


# ---------- 时间解析 ----------

def test_parse_at_iso():
    ts = _parse_at("2099-01-02 03:04")
    assert ts is not None and ts > time.time()


def test_parse_at_hhmm_future_today():
    now = time.localtime()
    ts = _parse_at(f"{(now.tm_hour + 1) % 24:02d}:00")
    assert ts is not None and ts > time.time()


def test_parse_at_hhmm_past_rolls_tomorrow():
    ts = _parse_at("00:01")  # 凌晨已过（除非恰在 00:00 跑测试）
    assert ts is not None and ts > time.time()


def test_parse_at_garbage():
    assert _parse_at("明天上午") is None
    assert _parse_at("") is None


# ---------- 技能 ----------

def test_set_with_delay(store):
    r = _skill(store, "reminder_set").run({"text": "关火", "delay_minutes": 60}, None)
    assert r.success and r.data["fire_at"] > time.time() + 3500
    assert "关火" in r.data["human"]


def test_set_with_at(store):
    from datetime import datetime, timedelta

    at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    r = _skill(store, "reminder_set").run({"text": "开会", "at": at}, None)
    assert r.success


def test_set_rejects_bad_input(store):
    sk = _skill(store, "reminder_set")
    assert not sk.run({"text": ""}, None).success                       # 空内容
    assert not sk.run({"text": "x"}, None).success                      # 没时间
    assert not sk.run({"text": "x", "delay_minutes": "abc"}, None).success  # 非数字
    assert not sk.run({"text": "x", "delay_minutes": 0.01}, None).success   # 太短
    assert not sk.run({"text": "x", "delay_minutes": 99999999}, None).success  # 太远
    assert not sk.run({"text": "x", "at": "2000-01-01 00:00"}, None).success  # 已过
    assert not sk.run({"text": "x", "at": "垃圾"}, None).success            # 看不懂
    assert store.list_pending() == []  # 全部拒绝，没落进任何脏数据


def test_list_and_cancel_skills(store):
    _skill(store, "reminder_set").run({"text": "A", "delay_minutes": 30}, None)
    _skill(store, "reminder_set").run({"text": "B", "delay_minutes": 60}, None)
    r = _skill(store, "reminder_list").run({}, None)
    assert r.success and r.data["count"] == 2
    assert "A" in r.data["human"] and "B" in r.data["human"]
    rid = store.list_pending()[0]["id"]
    r = _skill(store, "reminder_cancel").run({"id": rid}, None)
    assert r.success and "已取消" in r.data["human"]
    assert _skill(store, "reminder_list").run({}, None).data["count"] == 1
    assert not _skill(store, "reminder_cancel").run({"id": rid}, None).success  # 已取消的不能再取消


def test_list_empty(store):
    r = _skill(store, "reminder_list").run({}, None)
    assert r.success and r.data["count"] == 0 and "没有" in r.data["human"]


def test_reminder_skills_registerable_as_base_skills(store):
    """底座技能注册契约：id 禁点号（防伪装插件）——点号命名曾把大脑启动直接干崩。"""
    from yibao_brain.skills import SkillRegistry

    reg = SkillRegistry()
    for sk in make_skills(store):
        reg.register(sk)
    assert reg.get("reminder_set") is not None


# ---------- 提醒管理插件（reminders capability 共享底座 store） ----------

from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Http:
    def get(self, url, **kw):
        return {}

    def post(self, url, **kw):
        return {}


def _load(tmp_path, monkeypatch, store):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    from yibao_brain.llm import FakeProvider
    from yibao_brain.memory import FakeMemory
    from yibao_brain.plugins import LlmChat, load_plugins
    from yibao_brain.skills import SkillRegistry

    reg = SkillRegistry()
    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
        reminders=store,
    )
    return reg, results


def _run(reg, sid, params):
    t = reg.get(sid)
    assert t is not None, f"技能未注册: {sid}"
    return t.run(params, t.plugin_ctx)


def test_reminders_plugin_loads(tmp_path, monkeypatch):
    store = ReminderStore(str(tmp_path / "reminders.json"))
    _, results = _load(tmp_path, monkeypatch, store)
    assert results.get("reminders") == "ok"


def test_reminders_plugin_loads_without_store(tmp_path, monkeypatch):
    """底座未注入 store 时插件照常加载（ok），运行时才优雅报错。"""
    _, results = _load(tmp_path, monkeypatch, None)
    assert results.get("reminders") == "ok"


def test_reminders_api_registered(tmp_path, monkeypatch):
    from yibao_brain.plugins import get_api

    store = ReminderStore(str(tmp_path / "reminders.json"))
    _load(tmp_path, monkeypatch, store)
    lst = get_api("reminders.list")
    assert lst is not None and lst.direct and lst.panel == "reminders:main"
    cancel = get_api("reminders.cancel")
    assert cancel is not None and cancel.direct and cancel.refresh == "reminders.list"


def test_reminders_list_rows_sorted(tmp_path, monkeypatch):
    store = ReminderStore(str(tmp_path / "reminders.json"))
    later = store.add("晚点", time.time() + 7200)
    sooner = store.add("早点", time.time() + 600)
    reg, _ = _load(tmp_path, monkeypatch, store)
    r = _run(reg, "reminders.list", {})
    assert r.success and r.panel == "reminders:main"
    assert [row["id"] for row in r.data["rows"]] == [sooner["id"], later["id"]]
    assert all(row["text"] and row["when"] for row in r.data["rows"])


def test_reminders_cancel_by_prefix(tmp_path, monkeypatch):
    store = ReminderStore(str(tmp_path / "reminders.json"))
    item = store.add("关火", time.time() + 3600)
    reg, _ = _load(tmp_path, monkeypatch, store)
    r = _run(reg, "reminders.cancel", {"id": item["id"][:4]})
    assert r.success and "已取消" in r.data["human"]
    assert store.list_pending() == []
    assert not _run(reg, "reminders.cancel", {"id": item["id"]}).success  # 不能再取消


def test_reminders_tools_fail_gracefully_without_store(tmp_path, monkeypatch):
    reg, results = _load(tmp_path, monkeypatch, None)
    assert results["reminders"] == "ok"
    r = _run(reg, "reminders.list", {})
    assert not r.success and "底座未提供提醒存储" in r.error
    r = _run(reg, "reminders.cancel", {"id": "abcdef12"})
    assert not r.success and "底座未提供提醒存储" in r.error
