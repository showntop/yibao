"""读 Claude Code 本地 transcript（~/.claude/projects/**/*.jsonl）：会话历史恢复用。

私有格式、鸭子类型防御：任何一步失败都返回 []（恢复不了就当新会话，绝不报错）。
每行一个 JSON：{"type":"user"|"assistant"|...,"message":{"content": str|list}}。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_MAX_LINE_KB = 512  # 单行防御上限（超长行截断解析）


def read_transcript(cc_session_id: str, limit: int = 40) -> list[dict]:
    """按 session id 找 transcript，提取最近 limit 条 user/assistant 文本消息（时间正序）。"""
    if not cc_session_id:
        return []
    try:
        base = Path(os.path.expanduser("~/.claude/projects"))
        hits = sorted(base.glob(f"**/{cc_session_id}.jsonl"))
        if not hits:
            return []
        rows: list[dict] = []
        with open(hits[-1], encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line[: _MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                role = row.get("type")
                if role not in ("user", "assistant"):
                    continue
                text = _text_of(row.get("message"))
                if text:
                    rows.append({"role": role, "text": text})
        return rows[-limit:] if limit else rows
    except Exception:
        return []


def _text_of(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                t = getattr(block, "text", None)  # SDK 对象序列化形态
                if t:
                    parts.append(str(t))
        return "\n".join(p for p in parts if p).strip()
    return ""
