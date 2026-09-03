"""zimeiti.voice_save：逐镜合成口播配音——视频 workflow voice 段 provider。

生成归本机确定性管线（不烧 LLM）：读 topic 最新分镜 → 逐镜 `say -o tmp.aiff`（zh_CN 语音
运行时从 `say -v '?'` 选，缺省回退系统默认）→ afconvert 转 m4a（小体积）→ ffprobe 取真实
音频时长（timeline 段要用真实时长对齐画面，不靠估算）→ 落盘
voice/<topic_id>/v<分镜版本>/s<idx>.m4a（插件数据根下，路径风格对齐 storyboards 目录先例）
→ voice_tracks 表按（topic_id, 分镜版本, 镜号）替换式记录。

Work Graph 投影（动态 N 镜走 work_output foreach_from 扇出，同 storyboard_save 先例）：
- voice.track artifact ×N：ref=<topic>#s<idx>#voice 跨版本稳定，重合成同镜 = 同 artifact
  新 revision（换语音/改口播重合成不另起 artifact）；
- derived_from 边 ×N：voice.track → video.shot（配音血缘挂到稳定 shot 身份上）。
空 narration 的镜跳过（结果里列 skipped）；单镜失败不拖垮整批（结果里列 failed）；
say/afconvert/ffprobe 任一缺失或整批零产出：success=False + 人话，不抛栈。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 60  # 单条外部命令超时秒数（短口播合成足够，防挂死拖住整批）
# 可测试性接缝：测试经模块 globals 替换（binaries 缺失/单镜失败隔离场景）
_which = shutil.which
_run_cmd = subprocess.run


class VoiceSave(Tool):
    id = "zimeiti.voice_save"
    label = "合成配音"
    description = (
        "给分镜逐镜合成口播配音（本机 say → m4a，ffprobe 实测时长）：默认合成最新分镜全部镜，"
        "shots 可只合成指定镜号，voice 指定 say 语音（缺省自动选 zh_CN 语音）。"
        "fit=true 时按分镜每镜目标时长收敛：短了补尾静音、长了轻度变速（≤1.15x），"
        "超出能力只标注不改（该去改文案而不是毁音）。"
        "重合成同镜覆盖同一路径、叠 artifact 新版本；空口播的镜自动跳过。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 每镜一个 artifact：ref 稳定，重合成同镜 = 同 artifact 新 revision
            "kind": "artifact",
            "artifact_type": "voice.track",
            "foreach_from": "data.tracks",
            "ref_from": "item.track_ref",
            "content_ref_from": "item.path",
            "metadata_fields": ["item.idx", "item.duration_sec", "item.voice", "data.storyboard_version"],
        },
        {   # voice.track derived_from video.shot ×N（配音血缘挂稳定 shot 身份）
            "kind": "edge",
            "relation": "derived_from",
            "foreach_from": "data.tracks",
            "source_artifact_type": "voice.track",
            "source_ref_from": "item.track_ref",
            "target_artifact_type": "video.shot",
            "target_ref_from": "item.shot_ref",
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
                    "topic_id": {"type": "string", "description": "选题 id（配音归属锚点）"},
                    "voice": {"type": "string", "description": "say 语音名（可选；缺省自动选 zh_CN 可用语音）"},
                    "shots": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "只合成指定镜号 idx 列表（可选，也可传 JSON 数组字符串；缺省=全部镜）",
                    },
                    "fit": {
                        "type": "boolean",
                        "description": "按分镜目标时长收敛每镜配音：短了补尾静音、长了轻度变速（≤1.15x），超出则标 over 不改",
                    },
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
        selected, error = _select_shots(shots, params.get("shots"))
        if error:
            return ActionResult(success=False, error=error)
        bins = {name: _which(name) for name in ("say", "afconvert", "ffprobe", "ffmpeg")}
        fit = bool(params.get("fit"))
        # fit 才刚需 ffmpeg（补静音/变速）；不 fit 时缺 ffmpeg 不影响
        required = ("say", "afconvert", "ffprobe") + (("ffmpeg",) if fit else ())
        missing = [name for name in required if not bins.get(name)]
        if missing:
            return ActionResult(
                success=False,
                error=f"本机缺少配音依赖：{'、'.join(missing)}（macOS 自带 say/afconvert；ffprobe/ffmpeg 随 ffmpeg 安装）",
            )
        voice = str(params.get("voice", "") or "").strip() or _pick_zh_voice(bins["say"])
        out_dir = self._plugin_root / "voice" / tid / f"v{version}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ActionResult(success=False, error=f"创建配音目录失败：{e}")
        tracks, skipped, failed = [], [], []
        for shot in selected:
            narration = str(shot.get("narration") or "").strip()
            idx = shot["idx"]
            if not narration:
                skipped.append({"idx": idx, "reason": "narration 为空"})
                continue
            track, error = _synthesize(bins, narration, voice, out_dir / f"s{idx}.m4a")
            if error:
                failed.append({"idx": idx, "error": error})
                continue
            duration, fit_action, target = track[1], "asis", None
            if fit:  # 按分镜目标时长收敛（N5）：把「人来回压字」变成能力内收敛 + 验收兜底
                target = float(shot.get("duration") or 0) or None
                if target:
                    duration, fit_action, fit_err = _fit_to_target(bins, track[0], track[1], target)
                    # fit 失败不丢音轨：音频本身可用，时长保留原实测值，动作标 unfit 如实可见
            tracks.append({
                "idx": idx,
                "shot_ref": f"{tid}#s{idx}",
                "track_ref": f"{tid}#s{idx}#voice",
                "path": str(track[0]),
                "duration_sec": duration,
                "target_sec": target,
                "fit": fit_action,
                "voice": voice or "system",
            })
        if not tracks:
            detail = "；".join(
                part for part in (
                    f"{len(failed)} 镜合成失败（{failed[0]['error']}）" if failed else "",
                    "口播全为空" if skipped and not failed else "",
                ) if part
            )
            return ActionResult(success=False, error=f"没有合成任何配音：{detail or '无可合成镜'}")
        now = int(time.time())
        for track in tracks:  # 同（选题,分镜版,镜号）重合成：替换旧行不堆叠（artifact 侧叠 revision）
            for row in ctx.db.query("voice_tracks", where={
                "topic_id": tid, "storyboard_version": version, "shot_idx": track["idx"],
            }):
                ctx.db.delete("voice_tracks", str(row["id"]))
            ctx.db.insert("voice_tracks", {
                "topic_id": tid, "storyboard_version": version, "shot_idx": track["idx"],
                "path": track["path"], "duration_sec": track["duration_sec"],
                "voice": track["voice"], "created_at": now,
            })
        result = ActionResult(success=True, data={
            "topic_id": tid,
            "storyboard_version": version,
            "voice": voice or "system",
            "tracks": tracks,
            "skipped": skipped,
            "failed": failed,
        })
        result.panel = "zimeiti:detail"
        return result


def _load_latest_storyboard(ctx, tid: str) -> tuple[tuple, str]:
    """读 topic 最新版分镜（镜像 storyboard_get 的读回姿势）；返回 ((version, shots), error)。"""
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
        cp = Path(raw)  # 旧库相对/绝对路径兼容（同 storyboard_get）
        path = cp if cp.is_absolute() else Path(os.path.dirname(ctx.db.path)) / cp
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return (), f"分镜读取失败（{row['content_path']}）：{e}"
    shots = doc.get("shots") if isinstance(doc, dict) else None
    if not isinstance(shots, list) or not shots:
        return (), f"分镜内容损坏（{row['content_path']}）：缺 shots 数组"
    return (int(row["version"]), shots), ""


def _select_shots(shots: list, raw) -> tuple[list, str]:
    """按 shots 参数过滤镜号（可传 JSON 字符串）；缺省=全部镜。返回 (selected, error)。"""
    if raw is None:
        return list(shots), ""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as e:
            return [], f"shots JSON 解析失败：{e}"
    if not isinstance(raw, list) or not raw:
        return [], "shots 必须是非空镜号数组（如 [1, 3]，可传 JSON 数组字符串）"
    wanted = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            return [], f"shots 镜号必须是正整数（当前：{item!r}）"
        wanted.append(item)
    by_idx = {int(shot["idx"]): shot for shot in shots}
    unknown = [idx for idx in wanted if idx not in by_idx]
    if unknown:
        return [], f"镜号不存在：{unknown}（当前分镜镜号：{sorted(by_idx)}）"
    return [by_idx[idx] for idx in wanted], ""


def _pick_zh_voice(say_bin: str) -> str:
    """从 `say -v '?'` 选 zh_CN 可用语音（偏好无括号备注的稳定名，如 Tingting）；找不到回退 ""。"""
    try:
        out = _run_cmd([say_bin, "-v", "?"], capture_output=True, text=True,
                       timeout=_TIMEOUT, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2}_[A-Z]{2})\s+#", line)
        if m and m.group("locale") == "zh_CN":
            voices.append(m.group("name").strip())
    if not voices:
        return ""
    simple = [v for v in voices if "(" not in v]  # 「Eddy (中文（中国大陆）)」带备注，Tingting 干净稳定
    return sorted(simple or voices)[0]


def _synthesize(bins: dict, narration: str, voice: str, out_path: Path) -> tuple[tuple, str]:
    """单镜合成：say → afconvert → ffprobe 实测时长。返回 ((path, duration_sec), error)。"""
    tmp_aiff = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp_aiff = tmp.name
        argv = [bins["say"], "-o", tmp_aiff]
        if voice:
            argv += ["-v", voice]
        argv.append(narration)
        r = _run_cmd(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0:
            return (), f"say 失败：{(r.stderr or r.stdout).strip()[:200]}"
        r = _run_cmd([bins["afconvert"], "-f", "m4af", "-d", "aac", tmp_aiff, str(out_path)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0 or not out_path.is_file():
            return (), f"afconvert 失败：{(r.stderr or r.stdout).strip()[:200]}"
        r = _run_cmd([bins["ffprobe"], "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=noprint_wrappers=1:nokey=1", str(out_path)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0:
            return (), f"ffprobe 失败：{(r.stderr or '').strip()[:200]}"
        return (out_path, round(float(r.stdout.strip()), 3)), ""
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return (), f"{type(e).__name__}：{e}"
    finally:
        if tmp_aiff:
            try:
                os.unlink(tmp_aiff)
            except OSError:
                pass


def _fit_to_target(bins: dict, path: Path, measured: float, target: float) -> tuple:
    """把音轨收敛到分镜目标时长：短了补尾静音（apad），长了轻度变速（atempo ≤1.15x）。

    返回 (最终实测时长, 动作, error)；动作 ∈ asis / padded / sped / over。
    over = 超目标 >15%：不毁音、不蒙混，原样返回，由上游改文案。
    """
    eps = 0.05
    if target - eps <= measured <= target + eps:
        return measured, "asis", ""
    if measured < target:
        af, action = f"apad=whole_dur={target}", "padded"
    else:
        ratio = measured / target
        if ratio > 1.15:
            return measured, "over", ""
        af, action = f"atempo={ratio:.4f}", "sped"
    tmp = path.with_name(path.stem + ".fit.m4a")
    try:
        r = _run_cmd([bins["ffmpeg"], "-y", "-v", "error", "-i", str(path), "-af", af, str(tmp)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0 or not tmp.is_file():
            return measured, "unfit", f"ffmpeg 时长收敛失败：{(r.stderr or r.stdout).strip()[:200]}"
        os.replace(tmp, path)
        r = _run_cmd([bins["ffprobe"], "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                     capture_output=True, text=True, timeout=_TIMEOUT, check=False)
        if r.returncode != 0:
            return measured, "unfit", f"收敛后 ffprobe 失败：{(r.stderr or '').strip()[:200]}"
        return round(float(r.stdout.strip()), 3), action, ""
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return measured, "unfit", f"{type(e).__name__}：{e}"


def make_tools(ctx):
    return [VoiceSave(os.path.dirname(ctx.db.path))]
