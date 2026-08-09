"""toolbox.timestamp：Unix 时间戳 ↔ 可读时间互转（L0 只读，纯函数）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class TimestampSkill(Skill):
    id = "toolbox.timestamp"
    label = "Unix 时间戳转换"
    description = (
        "在 Unix 时间戳（秒/毫秒）与可读时间（ISO 8601 / 本地时间）之间互转。"
        "用户给出时间戳或日期问「转成几点几分 / 转成时间戳」时调用；返回四种格式并打开工具箱面板展示。"
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
                        "input": {
                            "type": "string",
                            "description": "秒（10 位）/ 毫秒（13 位）/ ISO 8601 / 可读日期，"
                                           "如 1736342400 或 2025-01-08T16:00:00Z",
                        },
                    },
                    "required": ["input"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        raw = str(params.get("input") or "").strip()
        if not raw:
            return ActionResult(success=False, error="输入为空：请提供时间戳或日期")

        # ---- 解析输入：10 位=秒 / 13 位=毫秒 / 其他数字按秒；否则按 ISO/可读日期 ----
        dt: datetime | None = None
        if raw.isdigit():
            n = int(raw)
            try:
                dt = (datetime.fromtimestamp(n, tz=timezone.utc) if len(raw) != 13
                      else datetime.fromtimestamp(n / 1000.0, tz=timezone.utc))
            except (OverflowError, OSError, ValueError):
                dt = None
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
            except ValueError:
                dt = None

        if dt is None:
            return ActionResult(
                success=False,
                error="无法解析：应为 10 位秒 / 13 位毫秒 / ISO 8601 / 可读日期",
                data={"tool": "timestamp", "input": raw},
                panel="toolbox:main",
            )

        epoch_s = int(dt.timestamp())
        millis = epoch_s * 1000 + dt.microsecond // 1000
        data = {
            "tool": "timestamp",
            "input": raw,
            "seconds": str(epoch_s),
            "millis": str(millis),
            "iso": dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "local": dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        return ActionResult(success=True, data=data, panel="toolbox:main")


def make_tools(ctx: Any) -> list[Skill]:
    return [TimestampSkill()]
