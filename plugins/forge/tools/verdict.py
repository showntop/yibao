"""forge.verdict：需求最终裁决——更新行状态，理由写长期记忆（飞轮：下次快筛召回比对历史裁决）。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import time

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

# 合法裁决值：直接作为需求终态落库，必须白名单校验
VERDICTS = ("已立项", "已搁置", "已否决")


class Verdict(Skill):
    id = "forge.verdict"
    description = "用户对需求做出最终裁决（立项/搁置/否决）时调用；裁决理由会写入长期记忆影响以后快筛"
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self):
        self.refresh = "forge.list"  # 裁决后看板卡片落到新状态列（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "需求 id"},
                    "verdict": {"type": "string", "description": "裁决：已立项 / 已搁置 / 已否决"},
                    "reason": {"type": "string", "description": "裁决理由（会记入长期记忆）"},
                },
                "required": ["id", "verdict", "reason"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        rid = str(params.get("id", "")).strip()
        verdict = str(params.get("verdict", "")).strip()
        reason = str(params.get("reason", "")).strip()
        if verdict not in VERDICTS:
            return ActionResult(success=False, error=f"非法裁决：{verdict!r}（合法值：{' / '.join(VERDICTS)}）")
        if not rid or not reason:
            return ActionResult(success=False, error="id 和 reason 均不能为空")
        rows = ctx.db.query("requirements", where={"id": rid})
        if not rows:
            return ActionResult(success=False, error=f"需求不存在：{rid}")
        now = int(time.time())
        ctx.db.update("requirements", rid, {
            "status": verdict,
            "verdict_reason": reason,
            "decided_at": now,
            "updated_at": now,
        })
        # 裁决理由进长期记忆：下次快筛新想法时召回比对，同类坑不踩第二次
        title = rows[0].get("title", "")
        ctx.memory.add(f"需求「{title}」裁决为{verdict}：{reason}", "user")
        result = ActionResult(success=True, data={"id": rid, "status": verdict})
        result.panel = "forge:board"
        return result


def make_tools(ctx):
    return [Verdict()]
