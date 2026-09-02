"""zimeiti.mat_list：素材库——声明式 mat_list 的代码承接（2026-09-02 项目作用域数据边界）。

materials 表无 project_id 列，作用域归属经 topic_id 反解选题的 project_id；
孤儿素材（无 topic_id 或选题未立项）不属于任何项目，只在全球作用域可见。
会话绑定项目时默认只列本项目素材（「不复用旧素材」的数据边界）；
scope="global" 显式放宽到全库，project_id 显式指定项目（优先于会话绑定）。
未绑定项目的会话维持现状（全库）。where 等值过滤（编辑器素材抽屉按 topic_id 查）
保留，与项目作用域取交集。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）；
数据目录从插件 scoped ctx 的 db.path 推导（同 get_topic 姿势）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class MatListTool(Tool):
    id = "zimeiti.mat_list"
    label = "素材库"
    description = (
        "列出素材库（标题/摘要/标签，按更新时间倒序，最多 100 条）。写稿前先看有没有可当论据"
        "的素材，有就 mat_get 取正文。当前会话绑定了项目时默认只列该项目（经关联选题归属）"
        "的素材；要看全库传 scope=\"global\"，要看指定项目传 project_id。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "where": {"type": "object",
                              "description": "等值过滤条件（如 {\"topic_id\": \"...\"} 只看某选题的关联素材），"
                                             "与项目作用域取交集"},
                    "scope": {"type": "string",
                              "description": "传 \"global\" 列全库（含孤儿素材与他项目素材）；"
                                             "缺省只列当前会话绑定项目的素材，未绑定项目时即全库"},
                    "project_id": {"type": "string",
                                   "description": "显式指定项目 id，只列该项目素材（优先于会话绑定）"},
                    "quiet": {"type": "boolean",
                              "description": "取数不弹面板开关。只有用户明确说了「打开/看看素材库面板」"
                                             "时才不传；你自己为回答问题而取数时必须传 true"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        params = params or {}
        quiet = bool(params.get("quiet"))
        where = params.get("where") if isinstance(params.get("where"), dict) else None
        rows = db.query("materials", where=where, order="updated_at DESC", limit=100)
        pid = _scope_project_id(db, params, ctx)
        if pid:
            in_scope = {str(t["id"]) for t in db.query("topics", where={"project_id": pid})}
            rows = [r for r in rows if str(r.get("topic_id") or "") in in_scope]
        result = ActionResult(success=True, data={"rows": rows})
        if not quiet:  # quiet：只要数据，不带面板引用
            result.panel = "zimeiti:materials"
        return result


def _scope_project_id(db, params: dict, ctx: Any) -> str:
    """作用域项目 id：显式 project_id > scope=global（不过滤）> 会话绑定 > 未绑定（不过滤）。"""
    explicit = str(params.get("project_id") or "").strip()
    if explicit:
        return explicit
    if str(params.get("scope") or "").strip().lower() == "global":
        return ""
    cid = str((getattr(ctx, "meta", None) or {}).get("conversation_id") or "")
    return _bound_project_id(db, cid)


def _bound_project_id(db, conversation_id: str) -> str:
    """会话绑定的项目 id：读 <data_dir>/session_contexts.json；未绑定/读不到 → 空串。

    插件库在 <data_dir>/plugins/zimeiti/data.db，会话绑定表在 <data_dir>/session_contexts.json。
    """
    if not conversation_id:
        return ""
    try:
        raw = json.loads(
            (Path(db.path).parents[2] / "session_contexts.json").read_text(encoding="utf-8")
        )
        item = (raw.get("contexts") or {}).get(conversation_id)
        return str(item.get("workspace_id") or "") if isinstance(item, dict) else ""
    except (OSError, ValueError, AttributeError, IndexError):
        return ""


def make_tools(ctx: Any) -> list[Tool]:
    return [MatListTool()]
