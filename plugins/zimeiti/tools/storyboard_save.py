"""zimeiti.storyboard_save：分镜落盘——shot 从稿件文本里独立成一等对象（视频 workflow S2）。

生成归 agent（LLM 拆分镜），本 tool 只做确定性的校验+落盘+版本（代码 vs Agent，同
article_save 分工）。结构镜像 article_save：blob content-ref + DB 行 + 版本递增 +
20 版治理；归属锚点是 topic（topic 带 project_id，写路径不需要 scope 参数）。

Work Graph 投影（动态 N 个 shot 走 work_output foreach_from 扇出，见 work_events）：
- video.storyboard artifact：一选题一个（ref=topic_id，同 video.script 先例），每版叠 revision；
- video.shot artifact ×N：ref=<topic>#s<idx> 跨版本稳定，重存同 idx = 同 artifact 新 revision，
  单镜可选中/变体/approve/局部重生成/传血缘都挂在这个稳定身份上；
- contains 边 ×N（storyboard→shot）+ derived_from 边（storyboard→video.script，有稿才产：
  script_links 空数组 = foreach 零事件，即数据驱动的条件产出）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import time

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_KEEP_VERSIONS = 20  # 每选题保留的分镜版本数上限（超出删最旧的行；blob 由 Host 延迟 GC）
# 设计文档 2026-08-30 §4 shot 字段：必填四件之外的可选项原样留存（资产引用 frames/clips
# 是 S3/S4 阶段的事，这里只记创作意图字段）。
_OPTIONAL_FIELDS = ("scene", "shot_size", "camera_move", "narrative_purpose", "dialogue_ref")


class StoryboardSave(Tool):
    id = "zimeiti.storyboard_save"
    label = "保存分镜"
    description = (
        "把拆好的分镜落盘为选题的下一个版本（v1/v2/…）：每镜含序号/口播/时长/画面描述，"
        "每镜成为可单独寻址的 shot 对象（后续可单镜重生、标 approved/failed、挂素材血缘）。"
        "分镜完成或改分镜后调用；note 记一句本版改了什么。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 分镜本体：一选题一个 artifact，每版叠 revision
            "kind": "artifact",
            "artifact_type": "video.storyboard",
            "ref_from": "data.storyboard_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.shot_count", "params.note"],
        },
        {   # 每镜一个 artifact（动态 N → foreach 扇出；内容在分镜 blob 内，随本体换 revision）
            "kind": "artifact",
            "artifact_type": "video.shot",
            "foreach_from": "data.shots",
            "ref_from": "item.shot_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["item.idx", "item.narration", "item.visual", "item.duration", "data.version"],
        },
        {   # storyboard contains shot ×N（§4.4 结构组成边）
            "kind": "edge",
            "relation": "contains",
            "foreach_from": "data.shots",
            "source_artifact_type": "video.storyboard",
            "source_ref_from": "data.storyboard_ref",
            "target_artifact_type": "video.shot",
            "target_ref_from": "item.shot_ref",
        },
        {   # storyboard derived_from video.script（有稿才产：script_links 空数组 = 零事件）
            "kind": "edge",
            "relation": "derived_from",
            "foreach_from": "data.script_links",
            "source_artifact_type": "video.storyboard",
            "source_ref_from": "data.storyboard_ref",
            "target_artifact_type": "video.script",
            "target_ref_from": "item.script_ref",
        },
    )

    def __init__(self):
        self.refresh = "zimeiti.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id（分镜归属锚点）"},
                    "shots": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": (
                            "分镜数组（也可传 JSON 数组字符串），按 idx 升序，每镜："
                            "idx 镜号（正整数，唯一递增）、narration 口播台词、duration 时长秒数（正数）、"
                            "visual 画面描述；可选 scene/shot_size/camera_move/narrative_purpose/dialogue_ref"
                        ),
                    },
                    "note": {"type": "string", "description": "本版说明（如初版分镜 / 重排了 3-5 镜）"},
                },
                "required": ["topic_id", "shots"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("topic_id", "")).strip()
        note = str(params.get("note", "")).strip()
        if not tid:
            return ActionResult(success=False, error="topic_id 不能为空")
        # 先查行：既给「选题不存在」明确报错，也保证归属锚点是库里真实选题
        if not ctx.db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        shots, error = _normalize_shots(params.get("shots"))
        if error:
            return ActionResult(success=False, error=error)
        latest = ctx.db.query("storyboards", where={"topic_id": tid}, order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        now = int(time.time())
        doc = {"topic_id": tid, "version": version, "note": note, "shots": shots, "created_at": now}
        try:
            # promote 先于 PluginDb commit（同 article_save）：崩溃最多留可 GC 的孤儿 blob
            staged = blobs.stage_text(json.dumps(doc, ensure_ascii=False, indent=2))
            content_ref = staged.finalize()
            path = blobs.resolve(content_ref)
        except OSError as e:
            return ActionResult(success=False, error=f"写文件失败：{e}")
        row_id = ctx.db.insert(
            "storyboards",
            {"topic_id": tid, "version": version, "shot_count": len(shots),
             "content_path": content_ref, "note": note, "created_at": now},
        )
        self._prune(ctx, tid)
        # 投影用的扁平结构：shot_ref 跨版本稳定；有稿才给 script_links（derived_from 边的开关）
        has_script = bool(ctx.db.query("articles", where={"topic_id": tid}, limit=1))
        result = ActionResult(success=True, data={
            "id": row_id,
            "storyboard_ref": tid,
            "version": version,
            "shot_count": len(shots),
            "content_ref": content_ref,
            "path": str(path),
            "shots": [{**shot, "shot_ref": f"{tid}#s{shot['idx']}"} for shot in shots],
            "script_links": [{"script_ref": tid}] if has_script else [],
        })
        result.panel = "zimeiti:detail"
        return result

    def _prune(self, ctx, tid: str) -> None:
        """版本治理：保留最近 _KEEP_VERSIONS 版，超出的删行（blob 不 unlink，Host 按引用延迟 GC）。"""
        try:
            rows = ctx.db.query("storyboards", where={"topic_id": tid}, order="version DESC")
            for row in rows[_KEEP_VERSIONS:]:
                ctx.db.delete("storyboards", str(row["id"]))
        except Exception:
            pass


def _normalize_shots(raw) -> tuple[list, str]:
    """校验并规范化分镜数组；返回 (shots, error)，error 非空即失败（人话，不抛栈）。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            return [], f"shots JSON 解析失败：{e}"
    if not isinstance(raw, list) or not raw:
        return [], "shots 必须是非空数组（每镜一个对象，可传 JSON 数组字符串）"
    shots = []
    prev_idx = 0
    for pos, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return [], f"第 {pos} 镜必须是对象（含 idx/narration/duration/visual）"
        idx = item.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx <= 0:
            return [], f"第 {pos} 镜 idx 必须是正整数（当前：{idx!r}）"
        if idx <= prev_idx:
            return [], f"idx 必须唯一且严格递增：第 {pos} 镜 idx={idx} 与前镜冲突"
        if "narration" not in item or not isinstance(item.get("narration"), str):
            return [], f"第 {pos} 镜（idx={idx}）缺 narration 口播（无台词给空串）"
        visual = item.get("visual")
        if not isinstance(visual, str) or not visual.strip():
            return [], f"第 {pos} 镜（idx={idx}）缺 visual 画面描述"
        duration = item.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            return [], f"第 {pos} 镜（idx={idx}）duration 必须是正数秒（当前：{duration!r}）"
        shot = {
            "idx": idx,
            "narration": item["narration"],
            "duration": duration,
            "visual": visual.strip(),
        }
        for key in _OPTIONAL_FIELDS:  # 设计文档可选字段：字符串才留存（枚举/引用都是文本）
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                shot[key] = value.strip()
        shots.append(shot)
        prev_idx = idx
    return shots, ""


def make_tools(ctx):
    return [StoryboardSave()]
