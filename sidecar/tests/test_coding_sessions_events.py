"""coding_sessions 会话生命周期事件：coding.py 发射点 + proactive 只广播不落 feed（会话墙实时刷新链）。"""
from __future__ import annotations
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))

import coding as codingmod  # noqa: E402
from coding import StartSkill, StopSkill, _report_final  # noqa: E402
from yibao_brain.proactive import ProactiveDispatcher  # noqa: E402


class _FakeDB:
    """极小鸭式 db（同 test_coding_plugin 约定）。"""
    def __init__(self): self.rows = {}
    def insert(self, table, row):
        row = dict(row); self.rows[row["id"]] = row; return row["id"]
    def update(self, table, rid, fields): self.rows.setdefault(rid, {}).update(fields)
    def query(self, table, where=None, order=None, limit=None, **_):
        out = list(self.rows.values())
        if where:
            out = [r for r in out if all(r.get(k) == v for k, v in where.items())]
        return out[:limit] if limit else out


class _Ctx:
    def __init__(self, db):
        self.db = db
        self.events = []
        self.emit_event = self.events.append


def test_start_emits_sessions_changed(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(codingmod, "_spawn_stream", lambda *a, **k: None)
    monkeypatch.setattr(codingmod, "_runner_for", lambda agent: object())
    ctx = _Ctx(db)
    r = StartSkill().run({"cwd": "/tmp", "prompt": "干点啥"}, ctx)
    assert r.success
    ev = [e for e in ctx.events if e.get("kind") == "coding_sessions"]
    assert len(ev) == 1 and ev[0]["state"] == "started" and ev[0]["session_id"]


def test_report_final_emits_sessions_changed_with_state():
    events = []
    _report_final(events.append, "sid1", "任务", "done", None)
    ev = [e for e in events if e.get("kind") == "coding_sessions"]
    assert len(ev) == 1 and ev[0]["state"] == "done" and ev[0]["session_id"] == "sid1"
    # 终态任务卡（reminder）照常，不受新增信号影响
    assert any(e.get("kind") == "reminder" for e in events)


def test_stop_emits_sessions_changed(monkeypatch):
    db = _FakeDB()
    db.insert("sessions", {"id": "s1", "status": "running"})
    monkeypatch.setattr(codingmod, "_stop_session", lambda db, reg, sid: True)
    ctx = _Ctx(db)
    r = StopSkill().run({"id": "s1"}, ctx)
    assert r.success
    ev = [e for e in ctx.events if e.get("kind") == "coding_sessions"]
    assert len(ev) == 1 and ev[0]["state"] == "stopped"


def test_emit_helper_none_emit_noop():
    codingmod._emit_sessions_changed(None, "sid", "started")   # 不抛即过


class _Feed:
    def __init__(self): self.items = []
    def add(self, kind, text, meta): self.items.append((kind, text, meta))


def test_dispatch_coding_sessions_broadcasts_without_feed():
    async def run():
        feed, messages = _Feed(), []
        d = ProactiveDispatcher(settings={"proactive.level": "full"}, feed=feed,
                                write_msg=messages.append, voice=None, run_state={"task": None})
        await d.dispatch({"kind": "coding_sessions", "session_id": "s1", "state": "started"})
        assert feed.items == []                                  # 高频信号不落 feed
        assert len(messages) == 1 and messages[0]["event"]["kind"] == "coding_sessions"

    asyncio.run(run())
