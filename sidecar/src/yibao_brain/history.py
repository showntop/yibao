"""会话历史：短期对话上下文，JSON 落盘，大脑重启后恢复（mem0 管长期事实，这里管最近几轮对话）。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 历史里的 tool 结果只留要点：完整结果可能很大（长列表/截图数据），
# 模型从历史需要的是「调过工具、拿到过什么」的模式，不是全量数据。
_TOOL_CONTENT_MAX = 300


def _valid_msg(m) -> bool:
    if not isinstance(m, dict):
        return False
    role = m.get("role")
    if role in ("user", "assistant"):
        return isinstance(m.get("content"), str)
    if role == "tool":  # tool 消息必须挂得住调用（严格校验的 provider 缺 tool_call_id 会 400）
        return isinstance(m.get("content"), str) and bool(m.get("tool_call_id"))
    return False


def _sanitize(m: dict) -> dict:
    """落史前的清洗：tool 结果截断。"""
    if m.get("role") == "tool" and len(m.get("content") or "") > _TOOL_CONTENT_MAX:
        m = dict(m)
        m["content"] = m["content"][:_TOOL_CONTENT_MAX] + "…"
    return m


class ConversationHistory:
    """最近 N 轮消息（含 tool 调用轨迹）。load 容错（文件缺失/损坏 → 空），save 失败只告警不炸 run。

    一轮 = 一条 user 消息 + 其后的 assistant/tool 消息（工具轮一轮多条）。
    裁剪只在 user 边界下刀：孤儿 tool 消息（缺配对的 assistant tool_calls）会让严格校验的 provider 400。
    """

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
        msgs = [m for m in data if _valid_msg(m)]
        # 必须从 user 开始（老文件/手工编辑可能留下孤儿 assistant/tool 头）
        while msgs and msgs[0].get("role") != "user":
            msgs.pop(0)
        return msgs

    def messages(self) -> list[dict]:
        return list(self._messages)

    def record_turn(self, user_text: str, assistant_text: str) -> None:
        """纯对话轮（无工具调用）。"""
        self.record_messages([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ])

    def record_messages(self, msgs: list[dict]) -> None:
        """记录一轮完整轨迹：user + (assistant tool_calls + tool 结果)* + assistant 终复。

        关键：工具调用轨迹必须入史。只记「请求→文字答复」会教会模型跳过工具直接声称完成
        （模型模仿自己历史的说话模式），带轨迹它才模仿「先调工具再答复」。
        """
        self._messages.extend(_sanitize(m) for m in msgs if _valid_msg(m))
        self._trim()
        self._save()

    def _trim(self) -> None:
        user_idx = [i for i, m in enumerate(self._messages) if m.get("role") == "user"]
        if len(user_idx) > self.max_turns:
            del self._messages[: user_idx[len(user_idx) - self.max_turns]]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._messages, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[yibao] 会话历史写入失败（已跳过）：{e}", file=sys.stderr)
