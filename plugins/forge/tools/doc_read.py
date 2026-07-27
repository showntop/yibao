"""forge.doc_read：在面板里读挑战/PRD 文档全文（替代详情页里干看的文件路径）。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
proto 原型是 HTML，面板内不渲染源码，返回说明+路径（挑战/PRD 是 markdown，直接渲染）。
"""
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

KINDS = ("challenge", "prd", "proto")
_LABEL = {"challenge": "挑战文档", "prd": "PRD", "proto": "原型"}
_MAX_TEXT = 20000  # 面板渲染上限，截断防卡


class DocRead(Skill):
    id = "forge.doc_read"
    label = "读文档"
    description = (
        "在面板里打开一条需求的挑战文档或 PRD 全文（kind=challenge/prd/proto）。"
        "用户说「看看挑战记录/PRD/文档」时调用。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "需求 id"},
                    "kind": {"type": "string", "description": "文档种类：challenge / prd / proto"},
                },
                "required": ["id", "kind"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        rid = str(params.get("id", "")).strip()
        kind = str(params.get("kind", "")).strip()
        if kind not in KINDS:
            return ActionResult(success=False, error=f"非法 kind：{kind!r}（可用：{' / '.join(KINDS)}）")
        rows = ctx.db.query("requirements", where={"id": rid})
        if not rows:
            return ActionResult(success=False, error=f"需求不存在：{rid}")
        title = rows[0].get("title") or rid
        path = rows[0].get(f"{kind}_path") or ""
        label = _LABEL[kind]
        if not path:
            return ActionResult(success=False, error=f"「{title}」还没有{label}——先走对应流程生成")
        if kind == "proto":  # HTML 原型面板内不渲染源码
            return ActionResult(success=False, error=f"原型是 HTML 文件，面板里看不了，直接在浏览器打开：{path}")
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"文档读取失败：{e}")
        if len(text) > _MAX_TEXT:
            text = text[:_MAX_TEXT] + "\n\n……（过长已截断）"
        result = ActionResult(success=True, data={"id": rid, "title": f"{title} · {label}", "text": text})
        result.panel = "forge:doc"
        return result


def make_tools(ctx):
    return [DocRead()]
