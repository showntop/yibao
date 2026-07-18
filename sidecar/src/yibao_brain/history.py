"""会话历史：短期对话上下文，JSON 落盘，大脑重启后恢复（mem0 管长期事实，这里管最近几轮对话）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class ConversationHistory:
    """最近 N 轮 user/assistant 消息。load 容错（文件缺失/损坏 → 空），save 失败只告警不炸 run。"""

    def __init__(self, path: str | Path, max_turns: int = 10):
        self.path = Path(path)
        self.max_turns = max_turns
        self._messages: list[dict] = self._load()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [
            m for m in data
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
        ]

    def messages(self) -> list[dict]:
        return list(self._messages)

    def record_turn(self, user_text: str, assistant_text: str) -> None:
        self._messages.append({"role": "user", "content": user_text})
        self._messages.append({"role": "assistant", "content": assistant_text})
        overflow = len(self._messages) - self.max_turns * 2
        if overflow > 0:
            del self._messages[:overflow]
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._messages, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[yibao] 会话历史写入失败（已跳过）：{e}", file=sys.stderr)
