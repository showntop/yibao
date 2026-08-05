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
