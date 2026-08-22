"""coding 引擎 chip 跨引擎切换：SessionBriefSkill（DB 消息双向交接摘要）+ build_brief src/dst wording。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))

import coding as codingmod  # noqa: E402
from coding import SessionBriefSkill  # noqa: E402
from _brief import build_brief  # noqa: E402


class _FakeDB:
    """极小鸭式 db（同 test_coding_plugin 约定）：sessions 按 id 存，messages 存 _tables；
    query 支持 where 等值 + order（"col [DESC]"）+ limit。"""
    def __init__(self): self.rows = {}; self._tables = {}
    def insert(self, table, row):
        row = dict(row)
        if table == "sessions":
            self.rows[row["id"]] = row; return row["id"]
        self._tables.setdefault(table, []).append(row)
        return row.get("id")
    def query(self, table, where=None, order=None, limit=None, **_):
        out = list(self.rows.values()) if table == "sessions" else list(self._tables.get(table, []))
        if where:
            out = [r for r in out if all(r.get(k) == v for k, v in where.items())]
        if order:
            parts = order.split()
            out = sorted(out, key=lambda r: r.get(parts[0]) or 0,
                         reverse=len(parts) > 1 and parts[1].upper() == "DESC")
        return out[:limit] if limit else out


class _Ctx:
    def __init__(self, db, llm=None):
        self.db = db; self.llm = llm; self.emit_event = None


class _FakeLlm:
    def __init__(self, text="BRIEF"): self._t = text; self.calls = []
    def chat(self, prompt): self.calls.append(prompt); return self._t


def _mk_session(db, sid="s1", agent="claude-code", cwd="/p", msgs=()):
    db.insert("sessions", {"id": sid, "agent": agent, "cwd": cwd, "status": "done"})
    for i, (role, text) in enumerate(msgs):
        db.insert("messages", {"session_id": sid, "role": role, "text": text, "seq": i + 1})


def test_session_brief_not_found():
    r = SessionBriefSkill().run({"id": "nope"}, _Ctx(_FakeDB()))
    assert not r.success and "nope" in r.error


def test_session_brief_requires_id():
    r = SessionBriefSkill().run({}, _Ctx(_FakeDB()))
    assert not r.success


def test_session_brief_llm_brief_and_direction_wording(monkeypatch):
    """CC 会话 + target=codex → _build_brief 收到 src='Claude Code' / dst='Codex'；brief 透传。"""
    db = _FakeDB()
    _mk_session(db, agent="claude-code", msgs=[("user", "实现登录"), ("assistant", "好的")])
    seen = {}
    monkeypatch.setattr(codingmod._sess, "_build_brief",
                        lambda llm, turns, git, src, dst: seen.update(src=src, dst=dst) or "BRIEF")
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")
    r = SessionBriefSkill().run({"id": "s1", "target": "codex"}, _Ctx(db, _FakeLlm()))
    assert r.success and r.data["brief"] == "BRIEF" and r.data["session_id"] == "s1"
    assert seen == {"src": "Claude Code", "dst": "Codex"}


def test_session_brief_default_target_is_opposite(monkeypatch):
    """codex 会话缺省 target → dst='Claude Code'（取源的另一端）。"""
    db = _FakeDB()
    _mk_session(db, agent="codex", msgs=[("user", "hi")])
    seen = {}
    monkeypatch.setattr(codingmod._sess, "_build_brief",
                        lambda llm, turns, git, src, dst: seen.update(src=src, dst=dst) or "B")
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")
    r = SessionBriefSkill().run({"id": "s1"}, _Ctx(db, _FakeLlm()))
    assert r.success and seen == {"src": "Codex", "dst": "Claude Code"}


def test_session_brief_no_llm_falls_back_to_excerpt():
    """llm 未声明 → 兜底原文节选（含最近消息），不报错不挡路。"""
    db = _FakeDB()
    _mk_session(db, msgs=[("user", "改一下入口"), ("assistant", "已改 main.rs")])
    r = SessionBriefSkill().run({"id": "s1"}, _Ctx(db))   # llm=None
    assert r.success
    assert "节选" in r.data["brief"] and "改一下入口" in r.data["brief"]


def test_session_brief_llm_failure_falls_back_to_excerpt(monkeypatch):
    """LLM 返回 None（provider 失败）→ 同样兜底原文节选。"""
    db = _FakeDB()
    _mk_session(db, msgs=[("user", "加个日志")])
    monkeypatch.setattr(codingmod._sess, "_build_brief", lambda *a: None)
    monkeypatch.setattr(codingmod._codex, "git_summary", lambda cwd: "")
    r = SessionBriefSkill().run({"id": "s1"}, _Ctx(db, _FakeLlm()))
    assert r.success and "节选" in r.data["brief"] and "加个日志" in r.data["brief"]


def test_session_brief_empty_history():
    """无消息 → 兜底「（无历史消息）」，不发 LLM。"""
    db = _FakeDB()
    _mk_session(db, msgs=())
    llm = _FakeLlm()
    r = SessionBriefSkill().run({"id": "s1"}, _Ctx(db, llm))
    assert r.success and "无历史消息" in r.data["brief"]
    assert llm.calls == []


def test_build_brief_src_dst_wording():
    """build_brief 的 src/dst 参数进入 prompt（双向交接 wording）。"""
    prov = _FakeLlm("BRIEF")
    out = build_brief(prov, [{"role": "user", "text": "x"}], "", src="Claude Code", dst="Codex")
    assert out == "BRIEF"
    assert "Claude Code" in prov.calls[0] and "Codex" in prov.calls[0]
