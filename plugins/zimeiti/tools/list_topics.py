"""zimeiti.list：选题看板——声明式 list 的代码承接（2026-09-02 项目作用域数据边界）。

项目是会话的数据边界：会话绑定项目（session_contexts.json 里 conversation_id →
workspace_id）时默认只列该项目（topics.project_id）的选题，他项目与未立项选题
不进项目作用域；scope="global" 显式放宽到全库，project_id 显式指定项目（优先于
会话绑定）。未绑定项目的会话维持现状（全库）。
面板/quiet/explicit 语义对齐原声明式（带面板 tool 的 quiet 保留参数约定）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）；
数据目录从插件 scoped ctx 的 db.path 推导（同 get_topic 姿势）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


class ListTopicsTool(Tool):
    id = "zimeiti.list"
    label = "选题看板"
    description = (
        "列出选题看板（按更新时间倒序，最多 100 条）。当前会话绑定了项目时默认只列该项目"
        "的选题；要看全库传 scope=\"global\"，要看指定项目传 project_id。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string",
                              "description": "传 \"global\" 列全库（含他项目与未立项选题）；"
                                             "缺省只列当前会话绑定项目的选题，未绑定项目时即全库"},
                    "project_id": {"type": "string",
                                   "description": "显式指定项目 id，只列该项目选题（优先于会话绑定）"},
                    "quiet": {"type": "boolean",
                              "description": "取数不弹面板开关。只有用户明确说了「打开/看看 XX 看板/面板」"
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
        pid = _scope_project_id(db, params, ctx)
        rows = db.query("topics", order="updated_at DESC", limit=100)
        if pid:
            rows = [r for r in rows if str(r.get("project_id") or "") == pid]
        result = ActionResult(success=True, data={"rows": rows})
        if not quiet:  # quiet：只要数据，不带面板引用/明确意图信号
            result.panel = "zimeiti:board"
            result.explicit = True
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
    return [ListTopicsTool()]
