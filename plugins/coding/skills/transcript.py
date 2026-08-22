"""coding 插件的转录解析域：cc / codex 会话 transcript 的文件解析（纯函数，可独立测试）。

与 coding.py 的边界：本模块只管「读文件 → 对话行」，不落库、不调度；
落库（attach_*）、技能类、会话流式留在 coding.py（经 _sibling 引用本模块）。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


def _sibling(stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules（同 coding._sibling 惯例）。"""
    name = f"yibao_plugin_coding_{stem}"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name(f"{stem}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_codex = _sibling("_codex_reader")   # _DIALOG_ROLES / _text 复用；测试 monkeypatch 同 coding.py 惯例
_cc_reader = _sibling("_cc_reader")  # _text_of / _MAX_LINE_KB 复用；测试 monkeypatch HOME 改道


def _codex_sessions_root() -> str:
    """Codex session JSONL 根目录（间接层）。

    动态解析 coding 模块的同名函数：测试 monkeypatch codingmod._codex_sessions_root
    即可让 last_sessions/attach_codex 改道到 tmp 目录（与拆出前行为一致）。
    """
    import sys
    for name in ("yibao_plugin_coding_coding", "coding"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "_codex_sessions_root"):
            return mod._codex_sessions_root()
    return os.path.expanduser("~/.codex/sessions")


# sid -> {"cancel": threading.Event, ...}。stop 经此拿 cancel 信号；线程收尾后 pop。
# entry 可选键：steer（运行中督导补充队列，spec §A——SendSkill 对 running 会话的 prompt
def _cc_projects_base() -> Path:
    """CC transcript 根目录 ~/.claude/projects（测试 monkeypatch HOME 改道 tmp，同 _cc_reader）。"""
    return Path(os.path.expanduser("~/.claude/projects"))


def _cc_project_dir(cwd: str) -> Path:
    """cwd → CC 项目目录 ~/.claude/projects/<slug>。

    slug 规则：非字母数字/连字符一律转 -（实测 /Users/denny/.codex → -Users-denny--codex，
    即 / 与 . 都转 -）。对不上就当无记录（上游 None 兜底，不报错）。
    """
    return _cc_projects_base() / re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def _iso_ts(value) -> int | None:
    """ISO 8601 时间串（CC/codex transcript 行 timestamp）→ unix 秒；解析失败 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except (ValueError, OSError, OverflowError):
        return None


def _cc_transcript_rows(path: Path) -> list[dict]:
    """解析 CC transcript → [{role, text, uuid, ts}]（有文本的 user/assistant 行，时间正序）。

    防御语义同 _cc_reader.read_transcript：坏行跳过、任何失败 → []（检测/导入都以空降级，绝不抛）。
    """
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line[: _cc_reader._MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                role = row.get("type")
                if role not in ("user", "assistant"):
                    continue
                if row.get("isMeta"):  # 机器行（local-command-caveat 等）非用户内容，摘要/导入/计数一律跳过
                    continue
                text = _cc_reader._text_of(row.get("message"))
                if not text:
                    continue
                if text.startswith(("<local-command", "<command-")):  # 本地命令回声块（无 isMeta 标记的）
                    continue
                rows.append({"role": role, "text": text,
                             "uuid": str(row.get("uuid") or ""),
                             "ts": _iso_ts(row.get("timestamp"))})
    except Exception:
        return []
    return rows


# codex rollout 里 CLI 注入的机器条目（环境上下文/指令/插件推荐/中断标记等），非用户对话
# 内容——对齐 attach_cc 的 isMeta/本地命令回声过滤精神，导入/摘要一律跳过（实测 91 份 rollout 归纳）
_CODEX_META_PREFIXES = ("<environment_context", "<user_instructions", "<recommended_plugins",
                        "<in-app-browser-context", "<codex_internal_context", "<turn_aborted")


def _codex_transcript_rows(path: str) -> list[dict]:
    """解析 codex rollout → [{role, text, ts}]（user/assistant 对话行，时间正序）。

    只取 type=response_item 且 payload.role ∈ _codex._DIALOG_ROLES 的行（session_meta/
    turn_context/event_msg/reasoning/function_call/developer 指令注入天然排除）；文本提取
    复用 _codex._text（content 兼容字符串与块列表），ts 取行顶层 timestamp。防御语义同
    _cc_transcript_rows：坏行跳过、任何失败 → []（导入以空降级，绝不抛）。
    """
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line[: _cc_reader._MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                pl = o.get("payload") or {}
                if o.get("type") != "response_item" or pl.get("role") not in _codex._DIALOG_ROLES:
                    continue
                text = _codex._text(pl.get("content"))
                if not text or text.startswith(_CODEX_META_PREFIXES):  # 机器条目（见上常量）
                    continue
                rows.append({"role": pl["role"], "text": text, "ts": _iso_ts(o.get("timestamp"))})
    except Exception:
        return []
    return rows


def _cc_latest_session(cwd: str) -> dict | None:
    """~/.claude/projects/<slug>/ 顶层 mtime 最新 .jsonl → cc 上次会话卡数据。

    只扫 slug 目录顶层（不递归）：<uuid>/subagents/ 与 tool-results/ 天然排除。
    无记录/任何失败 → None（前端不显示该卡）。
    """
    try:
        files = [p for p in _cc_project_dir(cwd).glob("*.jsonl") if p.is_file()]
        if not files:
            return None
        latest = max(files, key=lambda p: p.stat().st_mtime)
        rows = _cc_transcript_rows(latest)
        first_user = next((r["text"] for r in rows if r["role"] == "user"), "")
        return {"cc_session_id": latest.stem,
                "ts": int(latest.stat().st_mtime),
                "summary": first_user[:60],
                "message_count": len(rows)}
    except Exception:
        return None


def _codex_latest_session(cwd: str) -> dict | None:
    """~/.codex/sessions 按 cwd 过滤的最新一条 → codex 上次会话卡数据。

    复用 handoff_list 同款扫描（_codex.list_sessions：日期树混存，读首行 session_meta.cwd
    过滤，已按 timestamp 倒序），取 [0]；session_id 形态与 handoff_brief 入参对齐。
    """
    try:
        sessions = _codex.list_sessions(cwd, root=_codex_sessions_root())
    except Exception:
        return None
    if not sessions:
        return None
    latest = sessions[0]
    ts = _iso_ts(latest.get("timestamp"))
    if ts is None:
        try:
            ts = int(os.path.getmtime(latest.get("path") or ""))
        except Exception:
            ts = 0
    return {"session_id": str(latest.get("session_id") or ""),
            "ts": ts,
            "summary": str(latest.get("first_line") or "")}


def _find_cc_transcript(cc_session_id: str, cwd: str) -> Path | None:
    """定位 cc transcript 文件：先 cwd slug 目录精确命中，再全 projects 顶层 glob 兜底
    （都不递归——subagents/ 在 <uuid>/ 下深度 ≥2，天然排除）。找不到 → None。"""
    if not cc_session_id or not re.fullmatch(r"[A-Za-z0-9_-]+", cc_session_id):
        return None  # 白名单挡 ../ 路径逃逸与 / 分段（同 _cc_reader.read_transcript）
    direct = _cc_project_dir(cwd) / f"{cc_session_id}.jsonl"
    if direct.is_file():
        return direct
    hits = sorted(p for p in _cc_projects_base().glob(f"*/{cc_session_id}.jsonl") if p.is_file())
    return hits[-1] if hits else None


def _find_codex_session(thread_id: str) -> dict | None:
    """~/.codex/sessions 全树扫首行 session_meta 匹配 thread_id → {cwd, timestamp, path, first_line}。

    attach_codex 只有 thread_id 没有 cwd（list_sessions 必须按 cwd 过滤，用不上）——
    直接扫 rollout 首行 session_meta（cwd/timestamp 都在 payload 里），再读首条 user 摘要。
    thread_id 白名单校验（只作等值比对，防路径逃逸意图）；任何失败 → None。
    """
    if not thread_id or not re.fullmatch(r"[A-Za-z0-9_-]+", thread_id):
        return None
    root = os.path.expanduser(_codex_sessions_root())
    try:
        candidates = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    except Exception:
        return None
    for path in candidates:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                meta = json.loads(f.readline())
        except Exception:
            continue
        if meta.get("type") != "session_meta":
            continue
        p = meta.get("payload") or {}
        if p.get("session_id") != thread_id:
            continue
        first_line = None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    pl = o.get("payload") or {}
                    if o.get("type") == "response_item" and pl.get("role") == "user":
                        first_line = _codex._text(pl.get("content"))[:60] or None
                        break
        except Exception:
            pass
        return {"cwd": p.get("cwd") or "", "timestamp": p.get("timestamp") or "",
                "path": path, "first_line": first_line}
    return None


def _import_codex_messages(db, sid: str, path: str, *, fallback_ts: int) -> None:
    """rollout 对话行落 messages 表（seq 1..n；ts 缺则 fallback_ts 兜底；codex 无 uuid 锚点留 ""）。

    单行落库失败不拖垮整段导入（仿 attach_cc/_stream._persist 隔离）。
    """
    for i, m in enumerate(_codex_transcript_rows(path), start=1):
        try:
            db.insert("messages", {
                "session_id": sid, "role": m["role"], "text": m["text"],
                "ts": m["ts"] if m["ts"] is not None else fallback_ts,
                "seq": i, "uuid": "",
            })
        except Exception as e:  # 单行落库失败不拖垮整段导入（仿 _stream._persist 隔离）
            print(f"[yibao/coding] attach_codex rollout 落库失败（跳过）：{e}", file=sys.stderr)
