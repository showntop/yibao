"""zimeiti.add：记选题——声明式 db insert 的代码承接（2026-09-02，9-01 报告 P1-02 后续）。

立项自动回写：会话绑定项目（session_contexts.json 里 conversation_id → workspace_id，
同 list_topics._bound_project_id 姿势）时新选题直接写 project_id，不再要模型事后
zimeiti.update 双写（两份真相会漂移）；未绑定会话保持空（后续立项流程仍可回写）。

声明形态与原 manifest 声明式等价：同 required=["title"]、同 9 个业务参数、
created_at/updated_at 由系统生成（unix 秒）、panel=zimeiti:board、refresh=zimeiti.list、
work_output zimeiti.topic、quiet 保留参数（取数不弹面板，剥掉不写库）。
与声明式的唯一行为差异：title 空时直接报错（原声明式只在 schema 层约束，运行时会插
NULL 标题行）；未声明的杂键不再进库（原样进库会被 sqlite 拒）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）；
数据目录从插件 scoped ctx 的 db.path 推导（同 list_topics 姿势）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_FIELDS = ("title", "angle", "platform", "source", "url",
           "hkrr", "hook_type", "target_platform", "cover_concepts")


class AddTopicTool(Tool):
    id = "zimeiti.add"
    label = "记选题"
    description = (
        "记录一条新选题（标题必填），初始状态为「候选」进看板；面向视频的选题可附 "
        "HKRR/钩型/目标视频平台/3 条封面概念（调用成功会在屏幕面板窗打开「选题看板」）"
    )
    default_risk = RiskLevel.L1_LOW
    work_outputs = ({
        "kind": "artifact",
        "artifact_type": "zimeiti.topic",
        "ref_from": "data.id",
        "metadata_fields": ["params.title", "params.angle", "params.target_platform"],
    },)

    def __init__(self):
        self.refresh = "zimeiti.list"  # 写入后面板拿刷新后的看板，而非回执 {"id":…}

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "选题标题（一句话说清要写什么）"},
                    "angle": {"type": "string", "description": "切入角度：从哪个点写、给谁看"},
                    "platform": {"type": "string", "description": "目标平台（如公众号/小红书/播客）"},
                    "source": {"type": "string", "description": "选题来源（如灵感/读者反馈/热点）"},
                    "url": {"type": "string", "description": "来源链接（热点转选题时的原文地址，可选）"},
                    "hkrr": {"type": "string",
                             "description": "HKRR 标注 JSON 字符串（视频选题用）：{\"happy\": 快乐, "
                                            "\"knowledge\": 知识, \"resonance\": 共鸣, \"rhythm\": 节奏}"
                                            "——快乐/知识/共鸣至少沾 1 项（成立理由），节奏标注可行/存疑"},
                    "hook_type": {"type": "string",
                                  "description": "钩型（视频选题用，如 悬念/反常识/利益承诺/身份代入）"},
                    "target_platform": {"type": "string",
                                        "description": "目标视频平台（视频选题用，如 抖音/B站/YouTube）"},
                    "cover_concepts": {"type": "string",
                                       "description": "封面概念 JSON 数组字符串，恰好 3 条文案级方案"
                                                      "（只出文字，不出图；视频选题用）"},
                    "quiet": {"type": "boolean",
                              "description": "取数不弹面板开关。只有用户明确说了「打开/看看 XX 看板/面板」"
                                             "时才不传；你自己为回答问题而取数时必须传 true"},
                },
                "required": ["title"],
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        params = params or {}
        quiet = bool(params.get("quiet"))  # 保留参数：取数不弹面板，剥掉不写库
        title = str(params.get("title") or "").strip()
        if not title:
            return ActionResult(success=False, error="title 不能为空")
        row = {}
        for key in _FIELDS:
            value = params.get(key)
            if value is None:
                continue
            # 写库防御（同声明式 _coerce_json）：LLM 给的结构化值落 JSON 文本
            row[key] = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
        pid = _bound_project_id(db, str((getattr(ctx, "meta", None) or {}).get("conversation_id") or ""))
        if pid:
            row["project_id"] = pid
        now = int(time.time())  # auto 时间戳由系统生成（覆盖入参防伪造，同声明式）
        row["created_at"] = row["updated_at"] = now
        row_id = db.insert("topics", row)
        result = ActionResult(success=True, data={"id": row_id, "project_id": pid})
        if not quiet:
            result.panel = "zimeiti:board"
        return result


def _bound_project_id(db, conversation_id: str) -> str:
    """会话绑定的项目 id：读 <data_dir>/session_contexts.json；未绑定/读不到 → 空串。

    插件库在 <data_dir>/plugins/zimeiti/data.db，会话绑定表在 <data_dir>/session_contexts.json。
    （与 list_topics._bound_project_id 同一份逻辑的自包含拷贝。）
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
    return [AddTopicTool()]
