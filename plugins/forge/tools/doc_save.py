"""forge.doc_save：挑战/PRD 文档落盘到插件数据目录 docs/，并把路径记回需求行。

文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
数据目录从插件 scoped ctx 的 db.path 推导，不 import config（保持插件可搬运）。
"""
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

# 文档种类白名单：决定写回哪一列（{kind}_path），也挡住乱写列名
KINDS = ("challenge", "prd")


class DocSave(Skill):
    id = "forge.doc_save"
    description = (
        "把写好的挑战文档或 PRD 落盘并记到需求行上（kind=challenge 时需求状态顺带变为「挑战中」）。"
        "挑战追问收敛后、PRD 撰写完成后调用。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, data_dir: str):
        self._docs_dir = Path(data_dir) / "docs"
        self.refresh = "forge.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "需求 id"},
                    "kind": {"type": "string", "description": "文档种类：challenge / prd"},
                    "content": {"type": "string", "description": "文档全文（markdown，原样落盘）"},
                },
                "required": ["id", "kind", "content"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        rid = str(params.get("id", "")).strip()
        kind = str(params.get("kind", "")).strip()
        content = params.get("content")
        if kind not in KINDS:
            return ActionResult(success=False, error=f"非法 kind：{kind!r}（可用：{' / '.join(KINDS)}）")
        if not rid or content is None or not str(content).strip():
            return ActionResult(success=False, error="id 和 content 均不能为空")
        # 先查行：既给「需求不存在」明确报错，也顺带保证拼文件名的 id 是库里真实 id（uuid hex，路径安全）
        rows = ctx.db.query("requirements", where={"id": rid})
        if not rows:
            return ActionResult(success=False, error=f"需求不存在：{rid}")
        self._docs_dir.mkdir(parents=True, exist_ok=True)
        path = self._docs_dir / f"{rid}-{kind}.md"
        try:
            path.write_text(str(content), encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"写文件失败：{e}")
        fields = {f"{kind}_path": str(path), "updated_at": int(time.time())}
        if kind == "challenge":  # 挑战文档落盘 = 进入挑战中（状态机的一环）
            fields["status"] = "挑战中"
        ctx.db.update("requirements", rid, fields)
        result = ActionResult(success=True, data={"id": rid, "path": str(path)})
        result.panel = "forge:detail"
        return result


def make_tools(ctx):
    return [DocSave(os.path.dirname(ctx.db.path))]
