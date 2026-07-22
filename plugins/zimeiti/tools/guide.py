"""zimeiti.guide：按需加载写作方法论文档——方法论不占常驻上下文，用时才读。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

# 白名单：name 直接拼文件路径，必须挡住目录穿越和任意文件读取
GUIDES = ("write",)


class Guide(Skill):
    id = "zimeiti.guide"
    description = (
        "加载写作方法论全文：write=成文框架（结构/钩子/节奏）。"
        "为选题写初稿或改稿之前先调它拿到方法论。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "方法论名：write"},
                },
                "required": ["name"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        name = str(params.get("name", "")).strip()
        if name not in GUIDES:
            return ActionResult(success=False, error=f"未知方法论：{name!r}（可用：{' / '.join(GUIDES)}）")
        # 插件目录 = tools/ 的上一级；guides/ 与 tools/ 平级
        path = Path(__file__).resolve().parent.parent / "guides" / f"{name}.md"
        try:
            return ActionResult(success=True, data={"text": path.read_text(encoding="utf-8")})
        except OSError as e:
            return ActionResult(success=False, error=f"方法论文件读取失败：{e}")


def make_tools(ctx):
    return [Guide()]
