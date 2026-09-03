"""zimeiti.visual_generate：真图 provider——视频 workflow assets 段的 AI 生图能力。

与 visual_card_save（降级占位卡）同一 artifact 合同：asset.visual、ref=<topic>#s<idx>#visual
跨版本稳定、derived_from video.shot 边、同（选题,分镜版,镜号）替换式落库。区别只在产物是
真实 AI 生成图像而非排版文字卡——degraded 默认 False，预检的 assets 段据此从「降级」
翻成「满血」（换 provider 不换流程的验收锚点）。

链路：分镜 visual 描述 → 图像生成 API（OpenAI images 兼容：POST {base}/images/generations；
缺省智谱 cogview）→ url/b64 取回 → Pillow 居中裁 9:16 并规整到 1080×1920 PNG →
落盘 visuals/<topic_id>/v<分镜版本>/s<idx>.png（与占位卡同位，真图直接顶替）→
visual_cards 表替换式记录（degraded=0）。

未配置图像 API（YIBAO_IMAGE_API_KEY，或视觉/主 provider key 兜底）→ 诚实报错：
success=False + 人话，提示可先用 visual_card_save 占位卡走通流程——能力缺口不伪装。
单镜失败不拖垮整批（结果里列 failed）；整批零产出 → success=False。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 90            # 图像生成单镜最长 90s（cogview 实测 5–20s）
_MAX_DOWNLOAD = 32 * 1024 * 1024  # 图像下载上限 32MB（防失控响应撑爆内存）
_SIZE = (1080, 1920)     # 竖屏 9:16（与占位卡/渲染链一致）
_GEN_SIZE = "720x1440"   # 请求生成尺寸（cogview 竖屏档；出来再居中裁到 9:16）
_PROMPT_SUFFIX = "，竖屏科普视频插画，扁平矢量风，高对比，画面干净，无文字无水印"
_UA = "yibao-visual-generate/1.0"


# 可测试性接缝：测试经模块 globals 替换（网络调用全部走这两个函数）
def _post_json(url: str, payload: dict, api_key: str) -> dict:
    """POST JSON 并解析响应；非 2xx / 非 JSON / 超时都转人话异常。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": _UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read(4 * 1024 * 1024).decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read(2048).decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"图像 API HTTP {e.code}：{detail[:200]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"图像 API 请求失败：{e}") from e
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"图像 API 返回非 JSON：{body[:200]}") from e


def _fetch_bytes(url: str) -> bytes:
    """下载图像二进制（生成 API 回 url 形态时用）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read(_MAX_DOWNLOAD)


class VisualGenerate(Tool):
    id = "zimeiti.visual_generate"
    label = "生成视觉图"
    description = (
        "【真图 provider】给分镜逐镜生成 AI 视觉图：调图像生成 API（缺省智谱 cogview，"
        "YIBAO_IMAGE_API_KEY/BASE_URL/MODEL 可换任意 OpenAI images 兼容端点），"
        "产物居中裁 9:16、规整到 1080×1920 PNG。与占位视觉卡同一 artifact 合同"
        "（asset.visual，ref 稳定，重生成同镜叠 revision 直接顶替占位卡）。"
        "shots 只生成指定镜号。未配置图像 API 时诚实报错（可先用 visual_card_save 占位卡）。"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = (
        {   # 每镜一个 artifact：ref 稳定，重生成同镜 = 同 artifact 新 revision
            "kind": "artifact",
            "artifact_type": "asset.visual",
            "foreach_from": "data.cards",
            "ref_from": "item.card_ref",
            "content_ref_from": "item.path",
            "metadata_fields": ["item.idx", "item.provider", "item.degraded",
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
        self.refresh = "zimeiti.get"

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id（视觉图归属锚点）"},
                    "shots": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "只生成指定镜号 idx 列表（可选，也可传 JSON 数组字符串；缺省=全部镜）",
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
        from yibao_brain import config

        api_key = config.image_api_key()
        if not api_key:
            return ActionResult(
                success=False,
                error=(
                    "未配置图像生成 API（YIBAO_IMAGE_API_KEY，或视觉/主 provider key 兜底均为空）："
                    "真图能力不可用。可先调 visual_card_save 出占位卡走通流程，配好 key 再重生成。"
                ),
            )
        base_url = config.image_base_url()
        model = config.image_model()
        out_dir = self._plugin_root / "visuals" / tid / f"v{version}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ActionResult(success=False, error=f"创建视觉图目录失败：{e}")
        cards, failed = [], []
        for shot in selected:
            idx = shot["idx"]
            visual = str(shot.get("visual") or shot.get("narration") or "").strip() or f"第 {idx} 镜"
            out_path, error = _generate_shot(
                base_url, api_key, model, visual, out_dir / f"s{idx}.png",
            )
            if error:
                failed.append({"idx": idx, "error": error})
                continue
            cards.append({
                "idx": idx,
                "shot_ref": f"{tid}#s{idx}",
                "card_ref": f"{tid}#s{idx}#visual",
                "path": str(out_path),
                "degraded": False,  # 真图：非降级
                "provider": model,
                "prompt_len": len(visual),
            })
        if not cards:
            head = failed[0]["error"] if failed else "无可生成镜"
            return ActionResult(
                success=False,
                error=f"没有生成任何视觉图：{len(failed)} 镜失败（{head}）" if failed else "没有生成任何视觉图",
            )
        now = int(time.time())
        for card in cards:  # 同（选题,分镜版,镜号）重生成：替换旧行不堆叠（占位卡行也被顶掉）
            for row in ctx.db.query("visual_cards", where={
                "topic_id": tid, "storyboard_version": version, "shot_idx": card["idx"],
            }):
                ctx.db.delete("visual_cards", str(row["id"]))
            ctx.db.insert("visual_cards", {
                "topic_id": tid, "storyboard_version": version, "shot_idx": card["idx"],
                "path": card["path"], "style": "ai", "degraded": 0, "created_at": now,
            })
        result = ActionResult(success=True, data={
            "topic_id": tid,
            "storyboard_version": version,
            "degraded": False,
            "provider": model,
            "note": "AI 生成视觉图（真图 provider）；不满意可对同镜重生成（叠 revision）",
            "cards": cards,
            "failed": failed,
        })
        result.panel = "zimeiti:detail"
        return result


def _load_latest_storyboard(ctx, tid: str) -> tuple[tuple, str]:
    """读 topic 最新版分镜（镜像 visual_card_save 的读回姿势）；返回 ((version, shots), error)。"""
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


def _generate_shot(base_url: str, api_key: str, model: str, visual: str, out_path: Path) -> tuple[Path, str]:
    """单镜生图：API 生成 → 取回（url/b64）→ Pillow 裁 9:16 规整 1080×1920 PNG。返回 (path, error)。"""
    try:
        resp = _post_json(
            f"{base_url}/images/generations",
            {"model": model, "prompt": visual + _PROMPT_SUFFIX, "size": _GEN_SIZE},
            api_key,
        )
        items = resp.get("data") if isinstance(resp, dict) else None
        if not items or not isinstance(items, list):
            return None, f"图像 API 返回缺 data：{str(resp)[:200]}"
        raw: bytes
        first = items[0] or {}
        if first.get("b64_json"):
            import base64

            raw = base64.b64decode(first["b64_json"])
        elif first.get("url"):
            raw = _fetch_bytes(str(first["url"]))
        else:
            return None, f"图像 API 返回缺 url/b64_json：{str(first)[:200]}"
        _normalize_to_canvas(raw, out_path)
        return out_path, ""
    except (RuntimeError, ValueError, OSError) as e:
        return None, f"{type(e).__name__}：{e}"


def _normalize_to_canvas(raw: bytes, out_path: Path) -> None:
    """居中裁 9:16 → 规整 1080×1920 PNG。不是图像/解码失败由 Pillow 抛错（上层按镜记失败）。"""
    from PIL import Image

    import io

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    target_ratio = _SIZE[0] / _SIZE[1]
    if w / h > target_ratio:  # 太宽：裁两边
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:  # 太高：裁上下
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize(_SIZE, Image.LANCZOS)
    img.save(out_path, "PNG")


def make_tools(ctx):
    from yibao_brain import config

    if not config.image_api_key():
        # 未配置图像 API 时不注册：「注册了但一调就失败」的假能力会骗过能力预检（P0-05），
        # 没有 key 时 assets 段只剩占位卡（degraded），预检如实提示降级路径；
        # 配好 key 后经 capability_refresh 热加载即出现。
        return []
    return [VisualGenerate(os.path.dirname(ctx.db.path))]
