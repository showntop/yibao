"""zimeiti.stat_add：录发布数据——同选题同平台同日去重（再录改旧行，不堆重复行）。

声明式 stat_add（纯 insert）2026-08-25 退役：同平台同日补录会堆重复行，且缺收藏/转发
（小红书核心指标）。文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_NUM_FIELDS = ("views", "likes", "comments", "favorites", "shares")


def _num(params: dict, key: str) -> int | None:
    """数值入参归一化：没传/传了 null → None（更新路径不覆盖旧值）；脏值 → None。"""
    v = params.get(key)
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class StatAddTool(Tool):
    id = "zimeiti.stat_add"
    label = "录发布数据"
    description = (
        "记录某选题在某平台的发布数据（阅读/赞/评论/收藏/转发），用于复盘。"
        "同选题同平台同日只留一行：当天重复录会更新旧行而不是新增。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id"},
                    "platform": {"type": "string", "description": "发布平台（如 公众号/小红书/知乎）"},
                    **{k: {"type": "integer", "description": n} for k, n in zip(
                        _NUM_FIELDS, ("阅读数", "点赞数", "评论数", "收藏数", "转发数"))},
                },
                "required": ["topic_id", "platform"],
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("topic_id") or "").strip()
        platform = str(params.get("platform") or "").strip()
        if not tid or not platform:
            return ActionResult(success=False, error="topic_id 和 platform 均不能为空")
        if not db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        nums = {k: _num(params, k) for k in _NUM_FIELDS}
        now = int(time.time())
        today = time.localtime(now)
        # 同选题同平台同日 → 更新旧行（只覆盖本次传了的字段，没传的保留旧值）
        for row in db.query("post_stats", where={"topic_id": tid, "platform": platform}):
            ts = int(row.get("recorded_at") or 0)
            if time.localtime(ts)[:3] == today[:3]:
                fields = {k: v for k, v in nums.items() if v is not None}
                fields["recorded_at"] = now
                db.update("post_stats", str(row["id"]), fields)
                return ActionResult(success=True, data={"id": row["id"], "topic_id": tid,
                                                        "platform": platform, "updated": True})
        rid = uuid.uuid4().hex
        db.insert("post_stats", {
            "id": rid,
            "topic_id": tid,
            "platform": platform,
            **{k: v or 0 for k, v in nums.items()},
            "recorded_at": now,
        })
        return ActionResult(success=True, data={"id": rid, "topic_id": tid, "platform": platform, "updated": False})


def make_tools(ctx: Any) -> list[Tool]:
    return [StatAddTool()]
