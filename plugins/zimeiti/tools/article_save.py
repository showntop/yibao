"""zimeiti.article_save：稿件落盘为新版本——文件存 articles/<topic_id>/v<n>.md，库记一行。

生成归 agent（LLM 写稿），本 tool 只做确定性的落盘+版本+状态流转（代码 vs Agent）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
数据目录从插件 scoped ctx 的 db.path 推导，不 import config（保持插件可搬运）。
2026-08-25：content_path 改存相对路径（相对插件数据根，迁移/换机不炸；老库绝对路径由
article_read 兼容）；版本治理——每选题保留最近 20 版，超出删行删文件。
"""
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_KEEP_VERSIONS = 20  # 每选题保留的版本数上限（超出删最旧的行与文件）


class ArticleSave(Tool):
    id = "zimeiti.article_save"
    label = "保存稿件"
    description = (
        "把写好的稿件落盘为选题的下一个版本（v1/v2/…），选题状态顺带从「候选」变为「写作中」。"
        "初稿完成或改稿完成后调用；note 记一句本版改了什么。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, data_dir: str):
        self._articles_dir = Path(data_dir) / "articles"
        self.refresh = "zimeiti.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id"},
                    "content": {"type": "string", "description": "稿件全文（markdown，原样落盘）"},
                    "note": {"type": "string", "description": "本版说明（如初稿 / 改了开头）"},
                },
                "required": ["id", "content"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("id", "")).strip()
        content = params.get("content")
        note = str(params.get("note", "")).strip()
        if not tid or content is None or not str(content).strip():
            return ActionResult(success=False, error="id 和 content 均不能为空")
        # 先查行：既给「选题不存在」明确报错，也保证拼目录名的 id 是库里真实 id（uuid hex，路径安全）
        rows = ctx.db.query("topics", where={"id": tid})
        if not rows:
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        latest = ctx.db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        dest = self._articles_dir / tid
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"v{version}.md"
        try:
            path.write_text(str(content), encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"写文件失败：{e}")
        now = int(time.time())
        rel_path = f"articles/{tid}/v{version}.md"  # 相对插件数据根落库（迁移/换机不炸）
        ctx.db.insert(
            "articles",
            {"topic_id": tid, "version": version, "content_path": rel_path, "note": note, "created_at": now},
        )
        fields = {"updated_at": now}
        if rows[0].get("status") == "候选":  # 有稿即进入写作中；已流转的状态不回退
            fields["status"] = "写作中"
        ctx.db.update("topics", tid, fields)
        self._prune(ctx, tid)
        result = ActionResult(success=True, data={"id": tid, "version": version, "path": str(path)})
        result.panel = "zimeiti:detail"
        return result

    def _prune(self, ctx, tid: str) -> None:
        """版本治理：保留最近 _KEEP_VERSIONS 版，超出的删行删文件（失败不阻塞保存）。"""
        try:
            rows = ctx.db.query("articles", where={"topic_id": tid}, order="version DESC")
            for row in rows[_KEEP_VERSIONS:]:
                ctx.db.delete("articles", str(row["id"]))
                cp = Path(str(row.get("content_path") or ""))
                old = cp if cp.is_absolute() else self._articles_dir.parent / cp
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception:
            pass


def make_tools(ctx):
    return [ArticleSave(os.path.dirname(ctx.db.path))]
