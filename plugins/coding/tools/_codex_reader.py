"""读 Codex session JSONL + git 摘要，供跨 agent 交接。"""
from __future__ import annotations
import glob, json, os, subprocess, sys

_META_TYPE = "session_meta"
_DIALOG_ROLES = {"user", "assistant"}


def _text(content) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, str): parts.append(b)
            elif isinstance(b, dict): parts.append(str(b.get("text") or b.get("content") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


def list_sessions(cwd: str, *, root: str = "~/.codex/sessions") -> list[dict]:
    target = os.path.realpath(os.path.expanduser(cwd))
    out = []
    for path in glob.glob(os.path.join(os.path.expanduser(root), "**", "*.jsonl"), recursive=True):
        try:
            with open(path) as f:
                first = f.readline()
            meta = json.loads(first)
        except Exception:
            continue
        if meta.get("type") != _META_TYPE:
            continue
        p = meta.get("payload") or {}
        scwd = p.get("cwd")
        if not scwd or os.path.realpath(os.path.expanduser(scwd)) != target:
            continue
        first_line = None
        try:
            with open(path) as f:
                for line in f:
                    try: o = json.loads(line)
                    except Exception: continue
                    pl = o.get("payload") or {}
                    if o.get("type") == "response_item" and pl.get("role") == "user":
                        first_line = _text(pl.get("content"))[:80] or None
                        break
        except Exception:
            pass
        out.append({"session_id": p.get("session_id"), "cwd": scwd,
                    "timestamp": p.get("timestamp", ""), "path": path, "first_line": first_line})
    out.sort(key=lambda s: s["timestamp"], reverse=True)
    return out


def read_conversation(path: str, tail: int = 8) -> dict:
    turns: list[dict] = []
    incomplete = False
    try:
        with open(path) as f:
            for line in f:
                try: o = json.loads(line)
                except Exception:
                    incomplete = True; continue
                pl = o.get("payload") or {}
                if o.get("type") == "response_item" and pl.get("role") in _DIALOG_ROLES:
                    t = _text(pl.get("content"))
                    if t:
                        turns.append({"role": pl["role"], "text": t})
    except Exception as e:
        print(f"[yibao/coding] codex session 读取失败：{e}", file=sys.stderr)
        incomplete = True
    return {"turns": turns[-(tail + 1):], "incomplete": incomplete}   # +1 容末 assistant


def git_summary(cwd: str) -> str:
    try:
        log = subprocess.run(["git", "-C", cwd, "log", "--oneline", "-10"],
                             capture_output=True, text=True, timeout=5)
        st = subprocess.run(["git", "-C", cwd, "status", "--short"],
                            capture_output=True, text=True, timeout=5)
        if log.returncode != 0:
            return ""
        return f"【近 10 条提交】\n{log.stdout.strip()}\n\n【工作区状态】\n{st.stdout.strip()}"
    except Exception:
        return ""
