"""zimeiti.visual_card_save：降级视觉卡——视频 workflow assets 段的降级 provider。

**这是明确标注的降级 provider**：生成的是排版字幕卡（纯色底 + shot 文字），不是 AI 生成
图像。artifact metadata（degraded: true, provider: "pillow_ffmpeg.textcard"）与返回文案
都诚实标注「占位视觉卡」，后续可装真图片 provider 对同 artifact 重生成（ref 稳定，叠 revision）。

实现（本机实测 2026-09-02）：homebrew ffmpeg 8.1.1 未编译 libfreetype → 无 drawtext 过滤器，
drawtext 排版路线不可用；改为 Pillow（sidecar 既有依赖）渲染透明文字层（CJK 字体运行时探测
回退）→ ffmpeg lavfi color 底 + overlay 合成 1080×1920 PNG → ffprobe 验证分辨率。
落盘 visuals/<topic_id>/v<分镜版本>/s<idx>.png + visual_cards 表（替换式记录）。

Work Graph 投影（foreach_from 扇出，同 storyboard_save 先例）：
- asset.visual artifact ×N：ref=<topic>#s<idx>#visual 跨分镜版本稳定，重生成同镜 = 同
  artifact 新 revision（不带分镜版本号：shot 身份稳定，换主题/改文案重生成不另起 artifact）；
- derived_from 边 ×N：asset.visual → video.shot。
单镜失败不拖垮整批；ffmpeg/ffprobe 缺失或整批零产出：success=False + 人话，不抛栈。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 60
_SIZE = (1080, 1920)  # 竖屏 9:16（抖音/B站竖版）
# 底色主题预设：克制的 3 个（不做花哨）
_STYLES = {
    "dark": {"bg": "0x111418", "fg": (245, 245, 240, 255)},
    "light": {"bg": "0xF5F2EA", "fg": (26, 26, 26, 255)},
    "blue": {"bg": "0x0E2A47", "fg": (255, 255, 255, 255)},
}
# CJK 字体探测候选（按优先级）：本机实测 PingFang.ttc 已不在 /System/Library/Fonts
# （macOS 26 挪入 PrivateFrameworks Reserved，不稳定性高不取）；Hiragino Sans GB.ttc
# index 0 实测可用（Pillow truetype 直接认 .ttc）。运行时逐个探测回退。
_FONT_CANDIDATES = (
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)
_FONT_SIZE = 64
_MARGIN_X = 90          # 左右安全边距（文字不贴边）
_MAX_LINES = 10         # 超出行数截断加省略号（防文字溢出画面）
_MAX_CHARS = 200        # 超长文案硬截断
_LINE_SPACING = 18

# 可测试性接缝：测试经模块 globals 替换（ffmpeg 缺失/单镜失败隔离场景）
_which = shutil.which
_run_cmd = subprocess.run
_font_cache: dict = {}  # 进程内缓存探测结果：size → (path, truetype font)


class VisualCardSave(Tool):
    id = "zimeiti.visual_card_save"
    label = "生成占位视觉卡"
    description = (
        "【降级 provider】给分镜逐镜生成占位视觉卡：纯色底 + shot 文字排版的 1080×1920 PNG "
        "（Pillow 排字 + ffmpeg 合成，不是 AI 生成图像；元数据标 degraded，"
        "后续可装真图片 provider 重生成）。style 选底色主题（dark/light/blue），"
        "shots 只生成指定镜号；重生成同镜覆盖同一路径、叠 artifact 新版本。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    degraded = True  # 占位视觉卡是降级 provider（非 AI 生图），预检要如实提示降级路径
    work_outputs = (
        {   # 每镜一个 artifact：ref 稳定（不带分镜版本号），重生成同镜 = 同 artifact 新 revision
            "kind": "artifact",
            "artifact_type": "asset.visual",
            "foreach_from": "data.cards",
            "ref_from": "item.card_ref",
            "content_ref_from": "item.path",
            "metadata_fields": ["item.idx", "item.style", "item.degraded", "item.provider",
                                "data.storyboard_version"],
        },
        {   # asset.visual derived_from video.shot ×N
            "kind": "edge",
            "relation": "derived_from",
            "foreach_from": "data.cards",
            "source_artifact_type": "asset.visual",
            "source_ref_from": "item.card_ref",
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
                    "topic_id": {"type": "string", "description": "选题 id（视觉卡归属锚点）"},
                    "shots": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "只生成指定镜号 idx 列表（可选，也可传 JSON 数组字符串；缺省=全部镜）",
                    },
                    "style": {
                        "type": "string",
                        "description": f"底色主题（可选，缺省 dark）：{'/'.join(_STYLES)}",
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
        style = str(params.get("style", "") or "dark").strip()
        if style not in _STYLES:
            return ActionResult(
                success=False,
                error=f"未知 style：{style!r}（可选：{'/'.join(_STYLES)}）",
            )
        bins = {name: _which(name) for name in ("ffmpeg", "ffprobe")}
        missing = [name for name, path in bins.items() if not path]
        if missing:
            return ActionResult(
                success=False,
                error=f"本机缺少视觉卡依赖：{'、'.join(missing)}（brew install ffmpeg）",
            )
        font_path, font, error = _pick_font()
        if error:
            return ActionResult(success=False, error=error)
        out_dir = self._plugin_root / "visuals" / tid / f"v{version}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ActionResult(success=False, error=f"创建视觉卡目录失败：{e}")
        theme = _STYLES[style]
        cards, failed = [], []
        for shot in selected:
            idx = shot["idx"]
            text = str(shot.get("visual") or shot.get("narration") or "").strip() or f"第 {idx} 镜"
            out_path, error = _render_card(bins, font, theme, text, out_dir / f"s{idx}.png")
            if error:
                failed.append({"idx": idx, "error": error})
                continue
            cards.append({
                "idx": idx,
                "shot_ref": f"{tid}#s{idx}",
                "card_ref": f"{tid}#s{idx}#visual",
                "path": str(out_path),
                "style": style,
                "degraded": True,  # 诚实标注：占位视觉卡，非 AI 生成图像
                "provider": "pillow_ffmpeg.textcard",
                "text_len": len(text),
            })
        if not cards:
            head = failed[0]["error"] if failed else "无可生成镜"
            return ActionResult(
                success=False,
                error=f"没有生成任何视觉卡：{len(failed)} 镜失败（{head}）" if failed else "没有生成任何视觉卡",
            )
        now = int(time.time())
        for card in cards:  # 同（选题,分镜版,镜号）重生成：替换旧行不堆叠（artifact 侧叠 revision）
            for row in ctx.db.query("visual_cards", where={
                "topic_id": tid, "storyboard_version": version, "shot_idx": card["idx"],
            }):
                ctx.db.delete("visual_cards", str(row["id"]))
            ctx.db.insert("visual_cards", {
                "topic_id": tid, "storyboard_version": version, "shot_idx": card["idx"],
                "path": card["path"], "style": style, "degraded": 1, "created_at": now,
            })
        result = ActionResult(success=True, data={
            "topic_id": tid,
            "storyboard_version": version,
            "style": style,
            "font": font_path,  # 报告实测探测到的 CJK 字体（透明可审计）
            "degraded": True,
            "note": "占位视觉卡（降级 provider），可后装真图片 provider 重生成",
            "cards": cards,
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


def _pick_font() -> tuple[str, object, str]:
    """运行时探测 CJK 字体（候选逐个试，进程内缓存）；返回 (path, font, error)。"""
    if _FONT_SIZE in _font_cache:
        path, font = _font_cache[_FONT_SIZE]
        return path, font, ""
    try:
        from PIL import ImageFont
    except ImportError:
        return "", None, "缺少 Pillow（sidecar 依赖，排版文字层需要）"
    for candidate in _FONT_CANDIDATES:
        if not os.path.isfile(candidate):
            continue
        try:
            font = ImageFont.truetype(candidate, _FONT_SIZE, index=0)  # .ttc 取 0 号字面
        except OSError:
            continue
        _font_cache[_FONT_SIZE] = (candidate, font)
        return candidate, font, ""
    return "", None, f"未找到可用 CJK 字体（已试：{'、'.join(_FONT_CANDIDATES)}）"


def _wrap_text(draw, font, text: str, max_width: int) -> list[str]:
    """按实测像素宽度贪心换行（中英文混排都准）；超 _MAX_LINES 截断加省略号。"""
    text = " ".join(text.split())[:_MAX_CHARS]
    lines, current = [], ""
    for char in text:
        trial = current + char
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        lines[-1] = lines[-1].rstrip("，。、；：,.;:!? ") + "…"
    return lines


def _render_card(bins: dict, font, theme: dict, text: str, out_path: Path) -> tuple[Path, str]:
    """单镜渲染：Pillow 透明文字层 → ffmpeg color 底 + overlay 合成 → ffprobe 验证。返回 (path, error)。"""
    tmp_layer = None
    try:
        from PIL import Image, ImageDraw

        layer = Image.new("RGBA", _SIZE, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        lines = _wrap_text(draw, font, text, _SIZE[0] - 2 * _MARGIN_X)
        draw.multiline_text(
            (_SIZE[0] / 2, _SIZE[1] / 2), "\n".join(lines), font=font,
            fill=theme["fg"], anchor="mm", align="center", spacing=_LINE_SPACING,
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_layer = tmp.name
        layer.save(tmp_layer)
        r = _run_cmd(
            [bins["ffmpeg"], "-v", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={theme['bg']}:s={_SIZE[0]}x{_SIZE[1]}:d=1",
             "-i", tmp_layer,
             "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:(H-h)/2:format=auto",
             "-frames:v", "1", str(out_path)],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
        if r.returncode != 0 or not out_path.is_file():
            return None, f"ffmpeg 失败：{(r.stderr or r.stdout).strip()[:200]}"
        r = _run_cmd(
            [bins["ffprobe"], "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out_path)],
            capture_output=True, text=True, timeout=_TIMEOUT, check=False,
        )
        if r.returncode != 0 or r.stdout.strip() != f"{_SIZE[0]},{_SIZE[1]}":
            return None, f"ffprobe 校验失败：{(r.stdout or r.stderr).strip()[:200]}"
        return out_path, ""
    except ImportError:
        return None, "缺少 Pillow（sidecar 依赖，排版文字层需要）"
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"{type(e).__name__}：{e}"
    finally:
        if tmp_layer:
            try:
                os.unlink(tmp_layer)
            except OSError:
                pass


def make_tools(ctx):
    return [VisualCardSave(os.path.dirname(ctx.db.path))]
