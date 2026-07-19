"""forge.verdict_form：打开裁决表单——只读，返回需求 id/title 喂 verdict_form 面板。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


class VerdictForm(Skill):
    id = "forge.verdict_form"
    description = "打开某条需求的裁决表单（面板交互）：返回需求 id/title，表单提交走 forge.verdict"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "需求 id"},
                },
                "required": ["id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        rid = str(params.get("id", "")).strip()
        rows = ctx.db.query("requirements", where={"id": rid})
        if not rows:
            return ActionResult(success=False, error=f"需求不存在：{rid}")
        # 表单 schema 里 submit.params 绑 $data.id，所以 data 必须带 id；title 给标题栏展示
        result = ActionResult(success=True, data={"id": rows[0]["id"], "title": rows[0].get("title", "")})
        result.panel = "forge:verdict_form"
        return result


def make_tools(ctx):
    return [VerdictForm()]
