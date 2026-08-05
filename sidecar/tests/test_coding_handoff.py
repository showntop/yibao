"""coding 交接：codex_reader + build_brief + handoff skills。"""
from __future__ import annotations
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "skills"))
from codex_reader import list_sessions, read_conversation, git_summary  # noqa: E402


def _write_session(root, rel, cwd, sid, ts, turns):
    """在 root 下造一个 Codex JSONL session 文件。rel=相对路径（含 年/月/日/文件名）。"""
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lines = [json.dumps({"type": "session_meta",
                         "payload": {"session_id": sid, "cwd": cwd, "timestamp": ts}})]
    for role, text in turns:
        lines.append(json.dumps({"type": "response_item",
                                 "payload": {"role": role, "content": text}}))
    with open(p, "w") as f:
        f.write("\n".join(lines) + "\n")
    return p


def test_list_sessions_matches_cwd(tmp_path):
    root = str(tmp_path / "sessions")
    proj = str(tmp_path / "proj"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                   [("user", "实现登录"), ("assistant", "好的")])
    _write_session(root, "2026/08/05/b.jsonl", str(tmp_path / "other"), "sid-b", "2026-08-05T11:00:00Z",
                   [("user", "别的项目")])
    res = list_sessions(proj, root=root)
    assert [s["session_id"] for s in res] == ["sid-a"]   # 只命中 cwd 匹配的
    assert res[0]["first_line"] == "实现登录"


def test_read_conversation_tail_and_incomplete(tmp_path):
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p")
    turns = [("user", f"msg{i}") for i in range(12)] + [("assistant", "ans")]
    p = _write_session(root, "2026/08/05/x.jsonl", proj, "sid", "2026-08-05T09:00:00Z", turns)
    # 注入一行坏 JSON
    with open(p, "a") as f: f.write("{NOT JSON\n")
    out = read_conversation(p, tail=4)
    assert out["incomplete"] is True
    roles = [t["role"] for t in out["turns"]]
    assert roles[-1] == "assistant"          # 末尾 assistant 收到
    assert len(out["turns"]) <= 5            # tail=4 + 末 assistant

def test_git_summary_runs_or_empty(tmp_path):
    # 非 git 目录 → 返 ""，不抛
    assert git_summary(str(tmp_path / "nope")) == ""


from _brief import build_brief  # noqa: E402


class _FakeProv:
    def __init__(self, text): self._t = text; self.calls = []
    def chat(self, msgs, timeout=None):
        self.calls.append(msgs); return type("R", (), {"text": self._t})()


def test_build_brief_returns_summary():
    prov = _FakeProv("任务：实现登录\n已完成：auth.py\n下一步：token 刷新")
    out = build_brief(prov, [{"role": "user", "text": "实现登录"}, {"role": "assistant", "text": "好的"}],
                      "【近提交】abc")
    assert out and "登录" in out


def test_build_brief_provider_failure_returns_none():
    class Boom:
        def chat(self, msgs, timeout=None): raise RuntimeError("llm down")
    assert build_brief(Boom(), [{"role": "user", "text": "x"}], "") is None


# ---------- Task 3: start_session source 透传（handoff 路径可追溯）----------
from coding import start_session  # noqa: E402


class _FakeDB:
    """与 test_coding_plugin 同款极小鸭式 db：记 insert 行，query 全量回。"""
    def __init__(self): self.rows = {}
    def insert(self, table, row): self.rows[row["id"]] = dict(row); return row["id"]
    def update(self, table, rid, fields): self.rows.setdefault(rid, {}).update(fields)
    def query(self, *a, **k): return list(self.rows.values())


def test_start_session_records_source_when_provided():
    """handoff 路径：source='codex:sid' 写入行 source 字段。"""
    db = _FakeDB()
    sid = start_session(db, agent="claude-code", cwd="/tmp/p", prompt="hi", source="codex:sid")
    assert db.rows[sid]["source"] == "codex:sid"


def test_start_session_source_defaults_empty():
    """用户直起（不传 source）→ 行 source == ''，保证既有 StartSkill 调用人不破。"""
    db = _FakeDB()
    sid = start_session(db, agent="claude-code", cwd="/tmp/p", prompt="hi")
    assert db.rows[sid]["source"] == ""


# ---------- Task 4: HandoffListSkill + HandoffBriefSkill ----------
import coding as codingmod  # noqa: E402
from coding import HandoffListSkill, HandoffBriefSkill  # noqa: E402


class _Ctx:
    """最小鸭式 ctx：db/llm/emit_event 三属性。"""
    db = None
    llm = None
    emit_event = None


def test_handoff_list_skill(tmp_path, monkeypatch):
    """HandoffListSkill 经 monkeypatch 后的 root 读 tmp session → 返回 sessions 列表。"""
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z", [("user", "hi")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    r = HandoffListSkill().run({"cwd": proj}, _Ctx())
    assert r.success and r.data["sessions"][0]["session_id"] == "sid-a"


def test_handoff_list_skill_cwd_empty():
    """cwd 缺失 → success=False，便于前端提示用户选目录。"""
    r = HandoffListSkill().run({}, _Ctx())
    assert not r.success and "cwd" in r.error


def test_handoff_brief_skill(tmp_path, monkeypatch):
    """HandoffBriefSkill：monkeypatch root + _build_brief → 返回 brief='BRIEF'，sid 透传。"""
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                   [("user", "实现登录"), ("assistant", "好的")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    monkeypatch.setattr(codingmod, "_build_brief", lambda prov, conv, git: "BRIEF")

    class _CtxWithLlm(_Ctx):
        class llm:
            @staticmethod
            def chat(m, timeout=None): return type("R", (), {"text": "BRIEF"})()

    r = HandoffBriefSkill().run({"session_id": "sid-a", "cwd": proj}, _CtxWithLlm())
    assert r.success and r.data["brief"] == "BRIEF" and r.data["session_id"] == "sid-a"


def test_handoff_brief_skill_session_not_found(tmp_path, monkeypatch):
    """session_id 在 cwd 的 session 列表中找不到 → success=False。"""
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                   [("user", "hi")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    r = HandoffBriefSkill().run({"session_id": "nope", "cwd": proj}, _Ctx())
    assert not r.success and "nope" in r.error


def test_handoff_brief_skill_no_llm(tmp_path, monkeypatch):
    """ctx.llm None（capability 未声明）→ error '未声明 llm capability'。"""
    root = str(tmp_path / "sessions"); proj = str(tmp_path / "p"); os.makedirs(proj)
    _write_session(root, "2026/08/05/a.jsonl", proj, "sid-a", "2026-08-05T10:00:00Z",
                   [("user", "hi")])
    monkeypatch.setattr(codingmod, "_codex_sessions_root", lambda: root)
    # _Ctx 默认 llm=None
    r = HandoffBriefSkill().run({"session_id": "sid-a", "cwd": proj}, _Ctx())
    assert not r.success and "llm" in r.error
