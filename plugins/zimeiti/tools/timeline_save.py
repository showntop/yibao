"""zimeiti.timeline_save：时间线组装——视频 workflow compose 段 provider。

生成归本机确定性管线（不烧 LLM）：读 topic 最新分镜 + 同分镜版本的 voice_tracks /
visual_cards → 每镜组装一个 clip（duration 取音轨 ffprobe 实测时长，无音轨的镜回退分镜
duration 并标 silent）→ 落盘 timelines/<topic_id>/v<N>.json（版本递增，路径风格对齐
voice/visuals 目录先例）→ timelines 表记录。

验收语义（产品审计「Timeline 引用 Shot/Asset，时长、比例、版本来源均可验收」）：
- 缺 visual 的镜 = 阻塞错误（success=False + 人话，列出缺哪些镜）——timeline 不能指向
  不存在的画面；visual_cards 行在但文件被删视同缺失；
- 缺 voice 不阻塞：空 narration 的镜本来就被 voice_save 跳过，按静音镜处理（silent 标注，
  结果 missing_voice 如实列出）；voice 行在但文件被删同样回退静音；
- 总时长 = 各 clip 真实时长求和；aspect 9:16、resolution 1080×1920、storyboard_version 溯源。

Work Graph 投影（foreach_from 扇出，同 storyboard_save 先例）：
- timeline.composition artifact：一选题一个（ref=topic_id），每次组装叠 revision；
- derived_from 边：timeline.composition → video.storyboard；
- uses 边 ×N（设计文档 §4.4 合法关系，运行时引用）：timeline uses 每个 asset.visual；
  timeline uses 每条 voice.track——静音镜没有音轨，audio_links 空数组零事件
  （数据驱动的条件产出，同 storyboard 有稿才产 script_links 先例）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_KEEP_VERSIONS = 20  # 每选题保留的 timeline 版本数上限（同 storyboards 治理先例）
_RESOLUTION = {"width": 1080, "height": 1920}  # 竖屏 9:16（与 visual_cards 产物一致）
_TIMEOUT = 30  # ffprobe 单文件实测超时
# 可测试性接缝：测试经模块 globals 替换（同 voice_save 先例）
_which = shutil.which
_run_cmd = subprocess.run


class TimelineSave(Tool):
    id = "zimeiti.timeline_save"
    label = "组装时间线"
    description = (
        "把最新分镜 + 配音 + 视觉卡组装成视频时间线：每镜一个 clip（时长取音轨实测，"
        "无音轨的镜按静音镜回退分镜时长），缺视觉卡的镜直接报阻塞错误。"
        "落盘 timelines/<选题>/v<N>.json（版本递增），供 render_save 渲染。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 时间线本体：一选题一个 artifact，每次组装叠 revision
            "kind": "artifact",
            "artifact_type": "timeline.composition",
            "ref_from": "data.timeline_ref",
            "content_ref_from": "data.content_ref",
            "metadata_fields": ["data.version", "data.storyboard_version",
                                "data.duration_sec", "data.clip_count"],
        },
        {   # timeline derived_from video.storyboard（组自哪版分镜的血缘）
            "kind": "edge",
            "relation": "derived_from",
            "source_artifact_type": "timeline.composition",
            "source_ref_from": "data.timeline_ref",
            "target_artifact_type": "video.storyboard",
            "target_ref_from": "data.storyboard_ref",
        },
        {   # timeline uses asset.visual ×N（每镜画面引用；缺 visual 已阻塞，必全量）
            "kind": "edge",
            "relation": "uses",
            "foreach_from": "data.clips",
            "source_artifact_type": "timeline.composition",
            "source_ref_from": "data.timeline_ref",
            "target_artifact_type": "asset.visual",
            "target_ref_from": "item.image_ref",
        },
        {   # timeline uses voice.track ×N：静音镜无音轨 → audio_links 空数组零事件
            "kind": "edge",
            "relation": "uses",
            "foreach_from": "data.audio_links",
            "source_artifact_type": "timeline.composition",
            "source_ref_from": "data.timeline_ref",
            "target_artifact_type": "voice.track",
            "target_ref_from": "item.track_ref",
        },
    )

    def __init__(self, data_dir: str):
        self._plugin_root = Path(data_dir)
        self.refresh = "zimeiti.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id（时间线归属锚点）"},
                },
                "required": ["topic_id"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("topic_id", "")).strip()
        if not tid:
            return ActionResult(success=False, error="topic_id 不能为空")
        if not ctx.db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        storyboard, error = _load_latest_storyboard(ctx, tid)
        if error:
            return ActionResult(success=False, error=error)
        version, shots = storyboard
        voice = {
            int(row["shot_idx"]): row
            for row in ctx.db.query("voice_tracks", where={
                "topic_id": tid, "storyboard_version": version,
            })
        }
        visuals = {
            int(row["shot_idx"]): row
            for row in ctx.db.query("visual_cards", where={
                "topic_id": tid, "storyboard_version": version,
            })
        }
        ffprobe_bin = _which("ffprobe")  # 没有 ffprobe 时回退入库元数据（clip 里标来源）
        clips, missing_visual, missing_voice = [], [], []
        for shot in shots:
            idx = int(shot["idx"])
            card = visuals.get(idx)
            card_path = Path(str(card["path"])) if card else None
            if card_path is None or not card_path.is_file():
                missing_visual.append(idx)
                continue
            track = voice.get(idx)
            track_path = Path(str(track["path"])) if track else None
            if track_path is not None and track_path.is_file():
                # 时长不信入库元数据（N3）：文件可能在入库后被外部修改（如补静音），
                # 组装前 ffprobe 实测；实测不可用才回退 duration_sec 并在 clip 标注来源。
                measured = _probe_duration(ffprobe_bin, track_path)
                duration = measured if measured is not None else float(track["duration_sec"])
                duration_source = "measured" if measured is not None else "metadata"
                silent = False
            else:  # 无音轨（空口播跳过镜 / 音轨文件丢失）：静音镜回退分镜时长
                duration = float(shot["duration"])
                duration_source = "storyboard"
                silent = True
                missing_voice.append(idx)
            clips.append({
                "idx": idx,
                "shot_ref": f"{tid}#s{idx}",
                "image_ref": f"{tid}#s{idx}#visual",
                "image_path": str(card_path),
                "audio_ref": None if silent else f"{tid}#s{idx}#voice",
                "audio_path": None if silent else str(track_path),
                "duration": round(duration, 3),
                "duration_source": duration_source,
                "narration": str(shot.get("narration") or ""),
                "silent": silent,
            })
        if missing_visual:  # 缺 visual = 阻塞：timeline 不能指向不存在的画面
            return ActionResult(
                success=False,
                error=(
                    f"第 {'、'.join(str(i) for i in missing_visual)} 镜还没有视觉卡"
                    f"（先 visual_card_save 生成；缺失即阻塞，不出半成品时间线）"
                ),
            )
        latest = ctx.db.query("timelines", where={"topic_id": tid}, order="version DESC", limit=1)
        tl_version = (int(latest[0]["version"]) if latest else 0) + 1
        total = round(sum(clip["duration"] for clip in clips), 3)
        now = int(time.time())
        doc = {
            "topic_id": tid,
            "version": tl_version,
            "storyboard_version": version,
            "aspect": "9:16",
            "resolution": dict(_RESOLUTION),
            "duration_sec": total,
            "clips": clips,
            "tracks": {  # 两轨 clip refs：渲染/审查按轨取引用
                "video": [
                    {"idx": c["idx"], "image_ref": c["image_ref"], "duration": c["duration"]}
                    for c in clips
                ],
                "audio": [
                    {"idx": c["idx"], "audio_ref": c["audio_ref"],
                     "duration": c["duration"], "silent": c["silent"]}
                    for c in clips
                ],
            },
            "created_at": now,
        }
        out_dir = self._plugin_root / "timelines" / tid
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"v{tl_version}.json"
            path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"写时间线文件失败：{e}")
        ctx.db.insert("timelines", {
            "topic_id": tid, "version": tl_version, "storyboard_version": version,
            "clip_count": len(clips), "duration_sec": total,
            "content_path": str(path), "created_at": now,
        })
        self._prune(ctx, tid)
        result = ActionResult(success=True, data={
            "topic_id": tid,
            "timeline_ref": tid,   # artifact ref 跨版本稳定（同 video.storyboard 先例）
            "storyboard_ref": tid,
            "version": tl_version,
            "storyboard_version": version,
            "clip_count": len(clips),
            "duration_sec": total,
            "aspect": "9:16",
            "resolution": dict(_RESOLUTION),
            "content_ref": str(path),
            "path": str(path),
            "clips": clips,
            "audio_links": [  # uses voice.track 边的开关：静音镜不在其中（零事件先例）
                {"track_ref": c["audio_ref"]} for c in clips if not c["silent"]
            ],
            "missing_voice": missing_voice,
        })
        result.panel = "zimeiti:detail"
        return result

    def _prune(self, ctx, tid: str) -> None:
        """版本治理：保留最近 _KEEP_VERSIONS 版（删行不删文件，与 storyboards 同姿势）。"""
        try:
            rows = ctx.db.query("timelines", where={"topic_id": tid}, order="version DESC")
            for row in rows[_KEEP_VERSIONS:]:
                ctx.db.delete("timelines", str(row["id"]))
        except Exception:
            pass


def _probe_duration(ffprobe_bin: str | None, path: Path) -> float | None:
    """ffprobe 实测音频文件时长（秒，3 位小数）；无 ffprobe / 失败 / 解析异常一律 None（调用方回退）。"""
    if not ffprobe_bin:
        return None
    try:
        r = _run_cmd([ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0:
            return None
        return round(float(r.stdout.strip()), 3)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def _load_latest_storyboard(ctx, tid: str) -> tuple[tuple, str]:
    """读 topic 最新版分镜（镜像 storyboard_get/voice_save 的读回姿势）；返回 ((version, shots), error)。"""
    rows = ctx.db.query("storyboards", where={"topic_id": tid}, order="version DESC", limit=1)
    if not rows:
        return (), f"选题 {tid} 还没有分镜（先 storyboard_save）"
    row = rows[0]
    raw = str(row["content_path"])
    if raw.startswith("blob://sha256/"):
        if getattr(ctx, "blobs", None) is None:
            return (), "底座未提供 blobs capability"
        path = ctx.blobs.resolve(raw, require_exists=False)
    else:
        cp = Path(raw)  # 旧库相对/绝对路径兼容
        path = cp if cp.is_absolute() else Path(os.path.dirname(ctx.db.path)) / cp
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return (), f"分镜读取失败（{row['content_path']}）：{e}"
    shots = doc.get("shots") if isinstance(doc, dict) else None
    if not isinstance(shots, list) or not shots:
        return (), f"分镜内容损坏（{row['content_path']}）：缺 shots 数组"
    return (int(row["version"]), shots), ""


def make_tools(ctx):
    return [TimelineSave(os.path.dirname(ctx.db.path))]
