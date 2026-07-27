"""toolbox.json_format：JSON 美化 / 压缩（L0 只读，纯函数）。"""
from __future__ import annotations

import json
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_BYTES = 512 * 1024


def _escape(text: str) -> str:
    """添加转义：把任意文本转成可嵌入字符串字面量的形式（不加外层引号）。

    json.dumps 会给字符串加一层外层引号并转义内部特殊字符；去掉外层引号即得
    「内容已转义」的文本，与 _unescape 互为逆操作。例：{"a":1} ⇒ {\\\"a\\\":1}。
    """
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _unescape(text: str) -> str:
    """去除转义：还原被反斜杠转义的文本（常见于从日志 / 代码字符串里拷出的 JSON）。

    三级尝试，覆盖两种常见形态：
      1) 整体本身就是 JSON 字符串字面量（带外层引号，如 "\\\"a\\\":1"）→ 直接 json.loads 解码；
      2) 无外层引号、内部是 \\\\\" 这类转义体（如 {\\\"a\\\":1}）→ 包一层引号再解码；
      3) 兜底机械替换常见转义序列（输入畸形时尽力还原）。
    """
    s = text.strip()
    try:  # 1) 带外层引号的字符串字面量
        v = json.loads(s)
        if isinstance(v, str):
            return v
    except (json.JSONDecodeError, ValueError):
        pass
    try:  # 2) 无外层引号的转义体
        return json.loads('"' + s + '"')
    except (json.JSONDecodeError, ValueError):
        pass
    # 3) 兜底
    return (s.replace('\\"', '"')
             .replace("\\/", "/")
             .replace("\\n", "\n")
             .replace("\\r", "\r")
             .replace("\\t", "\t")
             .replace("\\\\", "\\"))


class JsonFormatSkill(Skill):
    id = "toolbox.json_format"
    label = "JSON 格式化"
    description = (
        "格式化、压缩或转义/去转义 JSON 文本。用户贴出 JSON 说「格式化 / 美化 / 压缩 / "
        "去除转义 / 添加转义」时调用；返回结果并打开工具箱面板展示。"
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
                            "enum": ["pretty", "minify", "escape", "unescape"],
                            "description": "pretty=美化缩进（默认）；minify=压缩成单行；"
                                           "escape=添加转义（嵌入字符串字面量用）；"
                                           "unescape=去除转义（还原被转义的 JSON）",
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
        if mode not in ("pretty", "minify", "escape", "unescape"):
            return ActionResult(success=False, error=f"未知模式: {mode}")

        # 转义 / 去转义：纯字符串变换，不要求输入是合法 JSON，先于 json.loads 处理
        if mode in ("escape", "unescape"):
            out = _escape(text) if mode == "escape" else _unescape(text)
            return ActionResult(
                success=True,
                data={"tool": "json", "input": text, "output": out, "mode": mode},
                panel="toolbox:main",
            )

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
