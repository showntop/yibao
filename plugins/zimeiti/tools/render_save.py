"""zimeiti.render_save：渲染交付——视频 workflow deliver 段 provider，真 MP4。

管线（本机确定性，不烧 LLM）：读最新 timeline → 逐片段渲染（图片 -loop 1 成 duration
秒 1080×1920/30fps；有音轨 mux 该 m4a，无音轨用 anullsrc 静音轨补齐——保证 concat 后
音轨连续）→ concat demuxer 流拷贝拼接（失败自动重编码兜底）→ H.264 + AAC + yuv420p
（兼容性）→ ffprobe 实测 resolution/duration 写回（验收报告是交付物的一部分）→ 落盘
renders/<topic_id>/v<N>.mp4 + renders 表。

长任务走 DurableExecution（durable capability 的第一个真实消费者，设计文档 §5.2）：
- manifest 声明 durable capability，加载时把 provider 注册进引擎（capability
  video.render / provider zimeiti.ffmpeg）；
- run() 拿到 ctx.meta 里的 workspace_id（invoker 经 invocation_sink 注入的归属）→
  engine.start(stage=deliver) + wait：每渲完一个片段推进一次 checkpoint（断点续跑，
  关闭窗口/进程退出后从最后 checkpoint 恢复），片段间是取消安全点（协作式取消）；
- 拿不到 workspace 归属（未立项会话）或引擎拒收（workflow 没有 deliver 段）→ 同步
  退路照样出 MP4，结果如实标 durable: False（只是没有恢复/取消语义）。

Work Graph 投影：
- video.render artifact：ref=<topic>#render 跨版本稳定，每次渲染叠 revision；
- rendered_from 边（§4.4 合法关系）：video.render → timeline.composition。
ffmpeg/ffprobe 缺失 → success=False + 人话，不抛栈。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from yibao_brain.durable_execution import DurableProviderError
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 120           # 单条外部命令超时秒数（防挂死）
_SIZE = (1080, 1920)     # 竖屏 9:16
_FPS = 30
_CAPABILITY_ID = "video.render"
_PROVIDER_ID = "zimeiti.ffmpeg"
_STAGE_ID = "deliver"
# 可测试性接缝：测试经模块 globals 替换（binaries 缺失/渲染命令隔离场景）
_which = shutil.which
_run_cmd = subprocess.run


class RenderSave(Tool):
    id = "zimeiti.render_save"
    label = "渲染视频"
    description = (
        "把最新时间线渲染成真实 MP4（1080×1920 H.264+AAC）：逐镜图片成片段、"
        "配音 mux 进对应片段（静音镜补静音轨）、拼接成片，ffprobe 实测分辨率/时长写回。"
        "有项目上下文时走 durable 长任务（断点续跑、可取消），否则同步直渲。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 成片：一选题一个 artifact（ref 稳定），每次渲染叠 revision
            "kind": "artifact",
            "artifact_type": "video.render",
            "ref_from": "data.render_ref",
            "content_ref_from": "data.path",
            "metadata_fields": ["data.version", "data.timeline_version",
                                "data.storyboard_version", "data.duration_sec",
                                "data.resolution"],
        },
        {   # video.render rendered_from timeline.composition（导出来源血缘）
            "kind": "edge",
            "relation": "rendered_from",
            "source_artifact_type": "video.render",
            "source_ref_from": "data.render_ref",
            "target_artifact_type": "timeline.composition",
            "target_ref_from": "data.timeline_ref",
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
                    "topic_id": {"type": "string", "description": "选题 id（渲染归属锚点）"},
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
        timeline, error = _load_latest_timeline(ctx, tid)
        if error:
            return ActionResult(success=False, error=error)
        tl_version, doc = timeline
        clips = [
            {"idx": int(c["idx"]), "image_path": str(c["image_path"]),
             "audio_path": str(c["audio_path"]) if c.get("audio_path") else None,
             "duration": float(c["duration"]), "silent": bool(c.get("silent"))}
            for c in doc["clips"]
        ]
        missing = [c["idx"] for c in clips if not Path(c["image_path"]).is_file()]
        if missing:  # timeline 之后视觉卡被删：不渲指向死路径的片
            return ActionResult(
                success=False,
                error=f"第 {'、'.join(str(i) for i in missing)} 镜的视觉卡文件已丢失（重新 visual_card_save + timeline_save）",
            )
        bins = {name: _which(name) for name in ("ffmpeg", "ffprobe")}
        missing_bins = [name for name, path in bins.items() if not path]
        if missing_bins:
            return ActionResult(
                success=False,
                error=f"本机缺少渲染依赖：{'、'.join(missing_bins)}（brew install ffmpeg）",
            )
        latest = ctx.db.query("renders", where={"topic_id": tid}, order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        out_dir = self._plugin_root / "renders" / tid
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ActionResult(success=False, error=f"创建渲染目录失败：{e}")
        request = {
            "topic_id": tid,
            "timeline_version": tl_version,
            "storyboard_version": int(doc["storyboard_version"]),
            "out_path": str(out_dir / f"v{version}.mp4"),
            "work_dir": str(out_dir / f"v{version}.work"),
            "ffmpeg": bins["ffmpeg"],
            "ffprobe": bins["ffprobe"],
            "clips": clips,
        }
        outcome, execution_id, durable, error = self._execute(ctx, tid, tl_version, version, request)
        if error:
            return ActionResult(success=False, error=error)
        expected = float(doc["duration_sec"])
        tolerance = max(1.0, expected * 0.1)  # AAC 起音/封装对齐会带进少量漂移
        acceptance = {
            "aspect_ok": outcome["width"] == _SIZE[0] and outcome["height"] == _SIZE[1],
            "resolution": outcome["resolution"],
            "duration_sec": outcome["duration_sec"],
            "timeline_version": tl_version,
            "duration_expected": expected,
            "duration_ok": abs(outcome["duration_sec"] - expected) <= tolerance,
        }
        if not acceptance["aspect_ok"]:  # 实测画幅不对 = 渲染管线坏了，不算交付
            return ActionResult(
                success=False,
                error=f"渲染验收失败：实测分辨率 {outcome['resolution']}，应为 {_SIZE[0]}x{_SIZE[1]}",
            )
        now = int(time.time())
        ctx.db.insert("renders", {
            "topic_id": tid, "version": version, "timeline_version": tl_version,
            "storyboard_version": int(doc["storyboard_version"]),
            "path": outcome["path"], "duration_sec": outcome["duration_sec"],
            "resolution": outcome["resolution"], "execution_id": execution_id,
            "created_at": now,
        })
        result = ActionResult(success=True, data={
            "topic_id": tid,
            "render_ref": f"{tid}#render",
            "timeline_ref": tid,
            "version": version,
            "timeline_version": tl_version,
            "storyboard_version": int(doc["storyboard_version"]),
            "path": outcome["path"],
            "duration_sec": outcome["duration_sec"],
            "resolution": outcome["resolution"],
            "acceptance": acceptance,   # 验收报告是交付物的一部分
            "durable": durable,
            "execution_id": execution_id,
        })
        result.panel = "zimeiti:detail"
        return result

    def _execute(self, ctx, tid: str, tl_version: int, version: int, request: dict):
        """优先走 durable 引擎；不可用/拒收 → 同步退路。返回 (outcome, execution_id, durable, error)。"""
        engine = getattr(ctx, "durable", None)
        workspace_id = str((getattr(ctx, "meta", None) or {}).get("workspace_id") or "")
        idem_key = f"render:{tid}:tlv{tl_version}:rv{version}"
        if engine is not None and workspace_id:
            view = None
            try:
                view = engine.start(
                    workspace_id=workspace_id, stage_id=_STAGE_ID,
                    capability_id=_CAPABILITY_ID, provider_candidates=[_PROVIDER_ID],
                    request=request, idempotency_key=idem_key, cancel_mode="checkpoint",
                )
                # 同幂等键撞到死执行（上次失败/取消/成片文件被删）：换键重开一条
                stale = view["status"] in ("failed", "cancelled") or (
                    view["status"] == "completed"
                    and not Path(str((view.get("result") or {}).get("path") or "")).is_file()
                )
                if stale:
                    view = engine.start(
                        workspace_id=workspace_id, stage_id=_STAGE_ID,
                        capability_id=_CAPABILITY_ID, provider_candidates=[_PROVIDER_ID],
                        request=request, idempotency_key=f"{idem_key}:r{int(time.time() * 1000)}",
                        cancel_mode="checkpoint",
                    )
            except Exception:
                view = None  # 引擎拒收（如 workflow 无 deliver 段）→ 同步退路
            if view is not None:
                execution_id = str(view["id"])
                if view["status"] != "completed":
                    budget = min(1800.0, 120.0 + 30.0 * sum(c["duration"] for c in request["clips"]))
                    view = engine.wait(execution_id, timeout=budget)
                if not view:
                    return None, execution_id, True, f"渲染执行记录丢失（execution：{execution_id}）"
                if view["status"] == "completed":
                    outcome = dict(view.get("result") or {})
                    if outcome.get("path") and Path(str(outcome["path"])).is_file():
                        return outcome, execution_id, True, ""
                    return None, execution_id, True, "渲染完成但成片文件缺失（请重试渲染）"
                if view["status"] == "cancelled":
                    return None, execution_id, True, "渲染已取消"
                if view["status"] == "failed":
                    return None, execution_id, True, f"渲染失败：{view.get('error') or '未知错误'}"
                return (
                    None, execution_id, True,
                    f"渲染超时仍未完成：任务在后台继续，可在项目中恢复/取消（execution：{execution_id}）",
                )
        try:  # 同步退路：无 workspace 归属/无引擎——MP4 照样出，只是没有恢复语义
            outcome = _durable_render_handler(request, {}, _InlineControl())
        except DurableProviderError as e:
            return None, "", False, str(e)
        except (OSError, ValueError) as e:
            return None, "", False, f"渲染失败：{type(e).__name__}：{e}"
        return outcome, "", False, ""


class _InlineControl:
    """同步退路的最小 control 形态：无引擎可持久化 checkpoint，取消语义不可用。"""

    def checkpoint(self, value: dict, *, progress: float) -> dict:
        return {}

    def raise_if_cancelled(self) -> None:
        return None


# ---------- durable provider（引擎/同步共用同一渲染实现） ----------


def _durable_render_handler(request: dict, checkpoint: dict, control) -> dict:
    """provider 合同 handler(request, checkpoint, control)：逐片段渲染 + checkpoint 推进。

    checkpoint 形态 {"segments_done": [片段序号...]}：续跑只补未完成的片段；
    每片段渲完推进一次（片段间即取消安全点）。
    """
    clips = list(request["clips"])
    work_dir = Path(request["work_dir"])
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DurableProviderError(f"创建渲染工作目录失败：{e}", retryable=False)
    done = {int(i) for i in (checkpoint.get("segments_done") or [])}
    steps = len(clips) + 1  # N 片段 + 1 拼接
    segments = []
    for pos, clip in enumerate(clips):
        seg = work_dir / f"seg_{pos:03d}.mp4"
        if pos in done and seg.is_file():
            segments.append(seg)  # checkpoint 说完成且文件在：跳过（断点续跑的核心）
            continue
        _render_segment(str(request["ffmpeg"]), clip, seg)
        done.add(pos)
        control.checkpoint({"segments_done": sorted(done)}, progress=(pos + 1) / steps)
        segments.append(seg)
    control.raise_if_cancelled()
    out_path = Path(request["out_path"])
    _concat(str(request["ffmpeg"]), segments, work_dir, out_path)
    control.checkpoint(
        {"segments_done": sorted(done), "concat_done": True}, progress=1.0,
    )
    probed = _probe(str(request["ffprobe"]), out_path)
    return {
        "path": str(out_path),
        "duration_sec": probed["duration_sec"],
        "width": probed["width"],
        "height": probed["height"],
        "resolution": f"{probed['width']}x{probed['height']}",
        "segments": len(clips),
        "timeline_version": request["timeline_version"],
        "storyboard_version": request["storyboard_version"],
    }


def _render_segment(ffmpeg: str, clip: dict, out_path: Path) -> None:
    """单镜成片段：图片 loop 成 duration 秒；有音轨 mux m4a，无音轨 anullsrc 静音轨
    （各片段音轨参数统一，concat 后音轨连续不断）。"""
    argv = [
        ffmpeg, "-v", "error", "-y",
        "-loop", "1", "-framerate", str(_FPS), "-i", clip["image_path"],
    ]
    if clip.get("audio_path"):
        argv += ["-i", clip["audio_path"]]
    else:
        argv += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    argv += [
        "-t", f"{float(clip['duration']):.3f}",
        "-vf", (
            f"scale={_SIZE[0]}:{_SIZE[1]}:force_original_aspect_ratio=decrease,"
            f"pad={_SIZE[0]}:{_SIZE[1]}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(_FPS),
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        str(out_path),
    ]
    try:
        r = _run_cmd(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DurableProviderError(f"第 {clip['idx']} 镜片段渲染失败：{type(e).__name__}：{e}", retryable=False)
    if r.returncode != 0 or not out_path.is_file():
        detail = (r.stderr or r.stdout or "").strip()[:200]
        raise DurableProviderError(f"第 {clip['idx']} 镜片段渲染失败：{detail}", retryable=False)


def _concat(ffmpeg: str, segments: list, work_dir: Path, out_path: Path) -> None:
    """concat demuxer 拼接：参数统一走流拷贝（快）；拷贝失败自动重编码兜底。"""
    list_file = work_dir / "concat.txt"
    try:
        list_file.write_text(
            "".join(f"file '{seg}'\n" for seg in segments), encoding="utf-8",
        )
    except OSError as e:
        raise DurableProviderError(f"写拼接清单失败：{e}", retryable=False)
    base = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    try:
        r = _run_cmd([*base, "-c", "copy", str(out_path)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0 or not out_path.is_file():
            r = _run_cmd([*base, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
                          str(out_path)],
                         capture_output=True, text=True, timeout=_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DurableProviderError(f"拼接失败：{type(e).__name__}：{e}", retryable=False)
    if r.returncode != 0 or not out_path.is_file():
        detail = (r.stderr or r.stdout or "").strip()[:200]
        raise DurableProviderError(f"拼接失败：{detail}", retryable=False)


def _probe(ffprobe: str, path: Path) -> dict:
    """ffprobe 实测成片：resolution + duration（验收写回，不靠估算）。"""
    try:
        r = _run_cmd(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise DurableProviderError(f"ffprobe 验收失败：{type(e).__name__}：{e}", retryable=False)
    if r.returncode != 0:
        raise DurableProviderError(
            f"ffprobe 验收失败：{(r.stderr or '').strip()[:200]}", retryable=False,
        )
    try:
        doc = json.loads(r.stdout)
        stream = doc["streams"][0]
        return {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "duration_sec": round(float(doc["format"]["duration"]), 3),
        }
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise DurableProviderError(f"ffprobe 输出解析失败：{e}", retryable=False)


def _load_latest_timeline(ctx, tid: str) -> tuple[tuple, str]:
    """读 topic 最新版 timeline（timeline_save 的产物）；返回 ((version, doc), error)。"""
    rows = ctx.db.query("timelines", where={"topic_id": tid}, order="version DESC", limit=1)
    if not rows:
        return (), f"选题 {tid} 还没有时间线（先 timeline_save）"
    row = rows[0]
    try:
        doc = json.loads(Path(str(row["content_path"])).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return (), f"时间线读取失败（{row['content_path']}）：{e}"
    clips = doc.get("clips") if isinstance(doc, dict) else None
    if not isinstance(clips, list) or not clips:
        return (), f"时间线内容损坏（{row['content_path']}）：缺 clips 数组"
    return (int(row["version"]), doc), ""


def make_tools(ctx):
    engine = getattr(ctx, "durable", None)
    if engine is not None:  # manifest 声明 durable capability：加载期把 provider 注册进引擎
        engine.register_provider(
            capability_id=_CAPABILITY_ID,
            provider_id=_PROVIDER_ID,
            handler=_durable_render_handler,
        )
    return [RenderSave(os.path.dirname(ctx.db.path))]
