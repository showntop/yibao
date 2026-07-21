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
    r = _skill(store, "reminder.set").run({"text": "关火", "delay_minutes": 60}, None)
    assert r.success and r.data["fire_at"] > time.time() + 3500
    assert "关火" in r.data["human"]


def test_set_with_at(store):
    from datetime import datetime, timedelta

    at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    r = _skill(store, "reminder.set").run({"text": "开会", "at": at}, None)
    assert r.success


def test_set_rejects_bad_input(store):
    sk = _skill(store, "reminder.set")
    assert not sk.run({"text": ""}, None).success                       # 空内容
    assert not sk.run({"text": "x"}, None).success                      # 没时间
    assert not sk.run({"text": "x", "delay_minutes": "abc"}, None).success  # 非数字
    assert not sk.run({"text": "x", "delay_minutes": 0.01}, None).success   # 太短
    assert not sk.run({"text": "x", "delay_minutes": 99999999}, None).success  # 太远
    assert not sk.run({"text": "x", "at": "2000-01-01 00:00"}, None).success  # 已过
    assert not sk.run({"text": "x", "at": "垃圾"}, None).success            # 看不懂
    assert store.list_pending() == []  # 全部拒绝，没落进任何脏数据


def test_list_and_cancel_skills(store):
    _skill(store, "reminder.set").run({"text": "A", "delay_minutes": 30}, None)
    _skill(store, "reminder.set").run({"text": "B", "delay_minutes": 60}, None)
    r = _skill(store, "reminder.list").run({}, None)
    assert r.success and r.data["count"] == 2
    assert "A" in r.data["human"] and "B" in r.data["human"]
    rid = store.list_pending()[0]["id"]
    r = _skill(store, "reminder.cancel").run({"id": rid}, None)
    assert r.success and "已取消" in r.data["human"]
    assert _skill(store, "reminder.list").run({}, None).data["count"] == 1
    assert not _skill(store, "reminder.cancel").run({"id": rid}, None).success  # 已取消的不能再取消


def test_list_empty(store):
    r = _skill(store, "reminder.list").run({}, None)
    assert r.success and r.data["count"] == 0 and "没有" in r.data["human"]
