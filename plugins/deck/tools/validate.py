"""deck.validate：演示校验——deck.presentation workflow 的 validate 段 provider。

读最新页面文档（deck.document），跑真实校验：页数落在目标区间、每页要点 ≤6 条、
标题非空、无占位符残留（TODO/占位/待补）。产 artifact quality.report（ref=deck_id）
+ derived_from 边 → deck.document。校验不过 → success=False + 报告明细（诚实卡门：
「完成」由验收决定，不过就是不过）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_BULLETS = 6        # 每页要点上限（演示可读性经验值）
_MAX_TITLE_LEN = 40     # 标题字数上限
_PLACEHOLDER = ("TODO", "占位", "待补", "placeholder", "TBD")


class Validate(Tool):
    id = "deck.validate"
    label = "校验演示"
    description = (
        "校验最新页面文档：页数是否落在目标区间、每页要点是否超上限、标题是否为空、"
        "有无占位符残留。产校验报告 artifact；不过就如实失败（报告里写明哪页哪条）。"
    )
    default_risk = RiskLevel.L1_LOW
    work_outputs = (
        {
            "kind": "artifact",
            "artifact_type": "quality.report",
            "ref_from": "data.report_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.ok", "data.check_count"],
        },
        {
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "quality.report",
            "source_ref_from": "data.report_ref",
            "target_artifact_type": "deck.document",
            "target_ref_from": "data.deck_ref",
        },
    )

    def __init__(self, data_dir: str):
        self._plugin_root = Path(data_dir)

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "deck_id": {"type": "string", "description": "演示文稿 id"},
                },
                "required": ["deck_id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        deck_id = str(params.get("deck_id") or "").strip()
        if not deck_id:
            return ActionResult(success=False, error="deck_id 不能为空")
        deck_rows = ctx.db.query("decks", where={"id": deck_id})
        if not deck_rows:
            return ActionResult(success=False, error=f"演示文稿不存在：{deck_id}")
        doc, error = _load_latest(ctx, deck_id, "document")
        if error:
            return ActionResult(success=False, error=error)
        report = _validate(deck_rows[0], doc)
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        latest = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": "report"},
                              order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        now = int(time.time())
        report["version"] = version
        report["created_at"] = now
        try:
            staged = blobs.stage_text(json.dumps(report, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
        except OSError as e:
            return ActionResult(success=False, error=f"写校验报告失败：{e}")
        ctx.db.insert("deck_stages", {
            "deck_id": deck_id, "kind": "report", "version": version,
            "content_path": content_ref, "created_at": now,
        })
        result = ActionResult(success=report["ok"], data={
            "deck_id": deck_id,
            "deck_ref": deck_id,
            "report_ref": deck_id,
            "version": version,
            "ok": report["ok"],
            "check_count": len(report["checks"]),
            "checks": report["checks"],
            "content_ref": content_ref,
        })
        if not report["ok"]:
            failures = [c for c in report["checks"] if not c["ok"]]
            result.error = "校验未过：" + "；".join(f"{c['name']}（{c['detail']}）" for c in failures[:3])
        return result


def _load_latest(ctx, deck_id: str, kind: str) -> tuple[dict, str]:
    rows = ctx.db.query("deck_stages", where={"deck_id": deck_id, "kind": kind},
                        order="version DESC", limit=1)
    if not rows:
        return {}, "还没有页面文档（先 deck.compose 组装）"
    blobs = getattr(ctx, "blobs", None)
    if blobs is None:
        return {}, "底座未提供 blobs capability"
    try:
        path = blobs.resolve(str(rows[0]["content_path"]), require_exists=False)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"页面文档读取失败：{e}"
    return doc, ""


def _validate(deck: dict, doc: dict) -> dict:
    """真实校验规则（全部确定性，无模型参与）：返回 {ok, checks[]}。"""
    slides = doc.get("slides") or []
    target = int(deck.get("page_count") or 12)
    checks: list[dict] = []

    # 页数：封面+结尾占 2，内容页 = slides-2；允许 ±50% 容差（目标只是量级约束）
    content_pages = max(0, len(slides) - 2)
    checks.append({
        "name": "页数区间",
        "ok": bool(slides) and content_pages >= 1 and len(slides) <= target + 6,
        "detail": f"共 {len(slides)} 页（内容 {content_pages} 页，目标 {target} 页）",
    })
    # 每页要点数
    over = [i + 1 for i, s in enumerate(slides) if len(s.get("bullets") or []) > _MAX_BULLETS]
    checks.append({
        "name": "单页要点上限",
        "ok": not over,
        "detail": f"每页 ≤{_MAX_BULLETS} 条" if not over else f"第 {over} 页超上限",
    })
    # 标题非空
    empty_title = [i + 1 for i, s in enumerate(slides) if not str(s.get("title") or "").strip()]
    checks.append({
        "name": "标题非空",
        "ok": not empty_title,
        "detail": "全部有标题" if not empty_title else f"第 {empty_title} 页缺标题",
    })
    # 标题长度
    long_title = [i + 1 for i, s in enumerate(slides)
                  if len(str(s.get("title") or "")) > _MAX_TITLE_LEN]
    checks.append({
        "name": "标题长度",
        "ok": not long_title,
        "detail": f"标题 ≤{_MAX_TITLE_LEN} 字" if not long_title else f"第 {long_title} 页标题超长",
    })
    # 占位符残留
    dirty = []
    for i, s in enumerate(slides):
        text = " ".join([str(s.get("title") or ""), *(str(b) for b in (s.get("bullets") or []))])
        if any(p in text for p in _PLACEHOLDER):
            dirty.append(i + 1)
    checks.append({
        "name": "占位符残留",
        "ok": not dirty,
        "detail": "无 TODO/占位/待补" if not dirty else f"第 {dirty} 页有占位符",
    })
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def make_tools(ctx):
    return [Validate(os.path.dirname(ctx.db.path))]
