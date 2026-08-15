"""会话历史：按会话（conversation_id）分桶的短期对话上下文，JSON 落盘。

M3 会话隔离闭环：模型上下文与前端 UI 会话一一对齐。
- 每个会话独立桶，历史互不可见（小窗不再"知道"你在其他会话问过什么）。
- 无会话 id 的事件（reminder 等主动推送）落 default 桶。
- mem0 管长期事实，这里管最近几轮对话（per 会话）。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 历史里的 tool 结果只留要点：完整结果可能很大（长列表/截图数据），
# 模型从历史需要的是「调过工具、拿到过什么」的模式，不是全量数据。
_TOOL_CONTENT_MAX = 300

# 无会话 id 事件的默认桶（reminder 等主动推送）
_DEFAULT_BUCKET = "default"


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
    """按会话分桶的最近 N 轮消息（含 tool 调用轨迹）。load 容错（文件缺失/损坏 → 空）。

    一轮 = 一条 user 消息 + 其后的 assistant/tool 消息（工具轮一轮多条）。
    裁剪只在 user 边界下刀：孤儿 tool 消息（缺配对的 assistant tool_calls）会让严格校验的 provider 400。
    """

    def __init__(self, path: str | Path, max_turns: int = 10):
        self.path = Path(path)
        self.max_turns = max_turns
        # {conversation_id: [messages...]}；空字符串会话也归 default 桶
        self._buckets: dict[str, list[dict]] = self._load()

    def _bucket(self, conversation_id: str) -> str:
        return conversation_id or _DEFAULT_BUCKET

    def _load(self) -> dict[str, list[dict]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            # 兼容旧版：单列表结构 → 归入 default 桶（一次迁移，不反复）
            if isinstance(data, list):
                msgs = self._clean_msgs(data)
                return {_DEFAULT_BUCKET: msgs} if msgs else {}
            return {}
        out: dict[str, list[dict]] = {}
        for cid, msgs in data.items():
            if isinstance(cid, str) and isinstance(msgs, list):
                cleaned = self._clean_msgs(msgs)
                if cleaned:
                    out[cid] = cleaned
        return out

    @staticmethod
    def _clean_msgs(msgs: list) -> list[dict]:
        valid = [m for m in msgs if _valid_msg(m)]
        # 必须从 user 开始（老文件/手工编辑可能留下孤儿 assistant/tool 头）
        while valid and valid[0].get("role") != "user":
            valid.pop(0)
        return valid

    def messages(self, conversation_id: str | None = None) -> list[dict]:
        """喂给 LLM 的上下文：只取指定会话桶（None/空 → default）。
        剥掉 surface 元数据（provider 不认的字段），面板场景的 user 轮加【xx 面板】标记。"""
        out: list[dict] = []
        for m in self._buckets.get(self._bucket(conversation_id or ""), []):
            surface = m.get("surface")
            if not surface:
                out.append(m)
                continue
            m = {k: v for k, v in m.items() if k != "surface"}
            if surface != "pet" and m.get("role") == "user":
                label = surface.split(":", 1)[-1] or surface
                m["content"] = f"【{label} 面板】{m['content']}"
            out.append(m)
        return out

    def conversations(self) -> list[dict]:
        """桶摘要列表（mobile /v1/conversations）：{id, preview, turns}。
        preview=末条 assistant 文本前 50 字（无 assistant 轮 → 空串）；turns=该桶消息数。
        注意桶内仅最近 10 轮（max_turns 裁剪）——摘要反映的是「最近上下文」非完整存档。"""
        out: list[dict] = []
        for cid, msgs in self._buckets.items():
            preview = next((m.get("content") or "" for m in reversed(msgs)
                            if m.get("role") == "assistant"), "")
            out.append({"id": cid, "preview": preview[:50], "turns": len(msgs)})
        return out

    def record_messages(self, msgs: list[dict], conversation_id: str | None = None) -> None:
        """记录一轮完整轨迹到指定会话桶：user + (assistant tool_calls + tool 结果)* + assistant 终复。

        conversation_id 缺省 → default 桶（无会话维度的事件/兼容旧调用）。
        关键：工具调用轨迹必须入史。只记「请求→文字答复」会教会模型跳过工具直接声称完成
        （模型模仿自己历史的说话模式），带轨迹它才模仿「先调工具再答复」。
        """
        bucket = self._bucket(conversation_id or "")
        bucket_msgs = self._buckets.setdefault(bucket, [])
        bucket_msgs.extend(_sanitize(m) for m in msgs if _valid_msg(m))
        self._trim(bucket_msgs)
        self._save()

    def record_turn(self, user_text: str, assistant_text: str, conversation_id: str | None = None) -> None:
        """纯对话轮（无工具调用）。"""
        self.record_messages([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ], conversation_id)

    def _trim(self, bucket_msgs: list[dict]) -> None:
        user_idx = [i for i, m in enumerate(bucket_msgs) if m.get("role") == "user"]
        if len(user_idx) > self.max_turns:
            del bucket_msgs[: user_idx[len(user_idx) - self.max_turns]]

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._buckets, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[yibao] 会话历史写入失败（已跳过）：{e}", file=sys.stderr)
