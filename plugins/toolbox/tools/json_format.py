"""toolbox.json_format：JSON 美化 / 压缩（L0 只读，纯函数）。"""
from __future__ import annotations

import json
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_BYTES = 512 * 1024


class JsonFormatSkill(Skill):
    id = "toolbox.json_format"
    description = (
        "格式化或压缩 JSON 文本。用户贴出 JSON 说「格式化 / 美化 / 压缩」时调用；"
        "返回格式化结果并打开工具箱面板展示。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "待处理的 JSON 文本"},
                        "mode": {
                            "type": "string",
                            "enum": ["pretty", "minify"],
                            "description": "pretty=美化缩进（默认）；minify=压缩成单行",
                        },
                        "indent": {
                            "type": "integer",
                            "description": "美化缩进空格数 0-8，默认 2",
                        },
                        "sort_keys": {
                            "type": "boolean",
                            "description": "是否按键名排序，默认 false",
                        },
                    },
                    "required": ["text"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        text = str(params.get("text") or "")
        if not text.strip():
            return ActionResult(success=False, error="输入为空：请提供 JSON 文本")
        if len(text.encode("utf-8")) > _MAX_BYTES:
            return ActionResult(success=False, error="输入过大：超过 512KB 限制")

        mode = params.get("mode", "pretty")
        if mode not in ("pretty", "minify"):
            return ActionResult(success=False, error=f"未知模式: {mode}")
        try:
            indent = max(0, min(8, int(params.get("indent", 2))))
        except (TypeError, ValueError):
            indent = 2
        sort_keys = bool(params.get("sort_keys", False))

        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return ActionResult(
                success=False,
                error=f"JSON 不合法：第 {e.lineno} 行第 {e.colno} 列：{e.msg}",
                data={"tool": "json", "input": text},
                panel="toolbox:main",
            )

        if mode == "minify":
            out = json.dumps(obj, ensure_ascii=False, sort_keys=sort_keys,
                             separators=(",", ":"))
        else:
            out = json.dumps(obj, ensure_ascii=False, sort_keys=sort_keys, indent=indent)

        return ActionResult(
            success=True,
            data={"tool": "json", "input": text, "output": out,
                  "mode": mode, "indent": indent, "sort_keys": sort_keys},
            panel="toolbox:main",
        )


def make_tools(ctx: Any) -> list[Skill]:
    return [JsonFormatSkill()]
