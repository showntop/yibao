"""toolbox.img2pdf：PNG / JPEG / WebP 等图片合并转 PDF（L0 只读，纯函数转换）。

输入两种形态：
  - 面板直传：pages=[{data: <base64>, name: "a.png"}]（base64 不带 data: 前缀）；
  - LLM 调用：pages=[{path: "/abs/a.png"}]（本地绝对路径，读后即转）。
输出 PDF base64（经面板 native:save_file 弹框落盘，工具自身不写文件系统）。

依赖 Pillow（sidecar 已依赖 pillow>=10.0）。多页用 save_all + append_images，
页面尺寸由「目标 pt × dpi」统一换算（auto 模式每页按各自图片比例）。
"""
from __future__ import annotations

import base64
import io
import re
from datetime import datetime
from typing import Any

from PIL import Image, ImageOps

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MM_TO_PT = 72.0 / 25.4  # 1mm = 72/25.4 pt
_PAPER_PT = {  # 纸型 → (宽, 高) pt
    "a4": (210.0 * _MM_TO_PT, 297.0 * _MM_TO_PT),      # ≈ 595.3 × 841.9
    "letter": (215.9 * _MM_TO_PT, 279.4 * _MM_TO_PT),  # 612 × 792
}
_MAX_PAGES = 100          # 单次最多图片数
_MAX_IMG_B64 = 15 * 1024 * 1024  # 单张 base64 上限（≈11MB 原图）
_SAFE_NAME = re.compile(r"[^\w.\-\u4e00-\u9fff]")


def _load_image(raw: bytes, name: str) -> Image.Image:
    """解码图片：EXIF 转正、RGBA/P/LA 贴白底转 RGB。解压炸弹/损坏图抛 ValueError。"""
    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            if img.mode == "P" and "transparency" not in img.info:
                img = img.convert("RGB")
            else:
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Image.DecompressionBombError:
        raise ValueError(f"{name or '图片'} 像素过大，超出安全限制")
    except Exception as e:  # 非图片文件 / 损坏 → 归为输入错误
        raise ValueError(f"{name or '图片'} 无法解析：{e}")


def _page_pt(img: Image.Image, page_size: str, orientation: str) -> tuple[float, float]:
    """目标页面尺寸（pt）。auto=按图片比例（长边上限≈A4 长边，防超大 PDF 页）。"""
    w, h = img.size
    if page_size != "auto":
        pw, ph = _PAPER_PT[page_size]
    else:
        pw, ph = float(w), float(h)
        scale = min(1.0, 841.9 / max(pw, ph))  # 长边封顶到 A4 长边
        pw, ph = pw * scale, ph * scale
    # 方向：auto 按图宽高比；portrait=高≥宽，landscape=宽>高
    if orientation == "portrait":
        if pw > ph:
            pw, ph = ph, pw
    elif orientation == "landscape":
        if ph > pw:
            pw, ph = ph, pw
    elif pw <= ph:  # auto：竖图给竖版、横图给横版（当前已是 px 比，直接判断）
        pass
    return pw, ph


def _page_image(img: Image.Image, pt_w: float, pt_h: float, dpi: int,
                margin_mm: int) -> Image.Image:
    """把图 contain 缩放进「目标页 pt - 边距」的画布并居中，返回 RGB 画布（白底）。"""
    px_w = max(1, round(pt_w * dpi / 72.0))
    px_h = max(1, round(pt_h * dpi / 72.0))
    margin_px = round(margin_mm * _MM_TO_PT * dpi / 72.0)
    avail_w = max(1, px_w - 2 * margin_px)
    avail_h = max(1, px_h - 2 * margin_px)
    ratio = min(avail_w / img.width, avail_h / img.height)
    tw = max(1, round(img.width * ratio))
    th = max(1, round(img.height * ratio))
    scaled = img.resize((tw, th), Image.LANCZOS) if (tw, th) != img.size else img
    canvas = Image.new("RGB", (px_w, px_h), (255, 255, 255))
    canvas.paste(scaled, ((px_w - tw) // 2, (px_h - th) // 2))
    return canvas


class Img2PdfTool(Tool):
    id = "toolbox.img2pdf"
    label = "图片转 PDF"
    description = (
        "把 PNG / JPEG / WebP / GIF / BMP / TIFF 等图片合并转成一个 PDF。"
        "用户给出图片文件路径说「转成 PDF / 导出 PDF」时调用；"
        "返回 PDF base64 并打开工具箱面板展示（面板可重新设置页面与保存位置）。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {
                                        "type": "string",
                                        "description": "图片本地绝对路径（LLM/对话场景传这个）",
                                    },
                                    "data": {
                                        "type": "string",
                                        "description": "图片 base64（面板直传，不含 data: 前缀）",
                                    },
                                    "name": {
                                        "type": "string",
                                        "description": "文件名，用于错误提示与默认输出名",
                                    },
                                },
                            },
                            "description": "要转成 PDF 的图片列表，至少 1 张、至多 100 张",
                        },
                        "page_size": {
                            "type": "string",
                            "enum": ["auto", "a4", "letter"],
                            "description": "auto=每页按图片比例（默认）；a4 / letter=统一页面尺寸",
                        },
                        "orientation": {
                            "type": "string",
                            "enum": ["auto", "portrait", "landscape"],
                            "description": "页面方向：auto 按图片宽高比（默认）",
                        },
                        "margin_mm": {
                            "type": "integer",
                            "description": "页边距（毫米）0-25，默认 0（图片贴边铺满）",
                        },
                        "dpi": {
                            "type": "integer",
                            "description": "输出分辨率 72-300，默认 144",
                        },
                    },
                    "required": ["pages"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        pages = params.get("pages") or []
        if not isinstance(pages, list) or not pages:
            return ActionResult(success=False, error="缺少图片：请提供至少一张图片")
        if len(pages) > _MAX_PAGES:
            return ActionResult(success=False, error=f"图片过多：单次最多 {_MAX_PAGES} 张")

        page_size = params.get("page_size", "auto")
        if page_size not in _PAPER_PT and page_size != "auto":
            page_size = "auto"
        orientation = params.get("orientation", "auto")
        if orientation not in ("auto", "portrait", "landscape"):
            orientation = "auto"
        try:
            dpi = max(72, min(300, int(params.get("dpi", 144))))
        except (TypeError, ValueError):
            dpi = 144
        try:
            margin_mm = max(0, min(25, int(params.get("margin_mm", 0))))
        except (TypeError, ValueError):
            margin_mm = 0

        # ---- 解码全部图片 ----
        imgs: list[Image.Image] = []
        names: list[str] = []
        for i, p in enumerate(pages):
            if not isinstance(p, dict):
                return ActionResult(success=False, error=f"第 {i + 1} 张图片参数无效")
            name = str(p.get("name") or f"img{i + 1}")
            data = p.get("data")
            path = p.get("path")
            try:
                if data:
                    if not isinstance(data, str) or len(data) > _MAX_IMG_B64:
                        return ActionResult(
                            success=False, error=f"{name} 过大（base64 超过 15MB），请换小图")
                    raw = base64.b64decode(data.strip(), validate=True)
                elif path:
                    with open(str(path), "rb") as f:
                        raw = f.read()
                else:
                    return ActionResult(
                        success=False, error=f"第 {i + 1} 张（{name}）：缺少 data 或 path")
                imgs.append(_load_image(raw, name))
                names.append(name)
            except ValueError as e:
                return ActionResult(success=False, error=str(e))
            except OSError as e:
                return ActionResult(success=False, error=f"{name} 读取失败：{e}")

        # ---- 逐页生成 ----
        out_pages: list[Image.Image] = []
        page_infos: list[dict] = []
        for img, name in zip(imgs, names):
            pt_w, pt_h = _page_pt(img, page_size, orientation)
            out_pages.append(_page_image(img, pt_w, pt_h, dpi, margin_mm))
            page_infos.append({
                "name": name,
                "src_size": list(img.size),
                "page_pt": [round(pt_w, 1), round(pt_h, 1)],
            })

        # ---- 保存 PDF ----
        buf = io.BytesIO()
        first = out_pages[0]
        if len(out_pages) > 1:
            first.save(buf, "PDF", save_all=True, append_images=out_pages[1:],
                       resolution=dpi)
        else:
            first.save(buf, "PDF", resolution=dpi)
        pdf_bytes = buf.getvalue()
        if not pdf_bytes:
            return ActionResult(success=False, error="PDF 生成失败：输出为空")

        base = _SAFE_NAME.sub("_", names[0] or "images")
        base = base[:60] or "images"
        if base.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
            base = base.rsplit(".", 1)[0]
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_name = f"{base}_to_pdf_{stamp}.pdf"

        return ActionResult(
            success=True,
            data={
                "tool": "img2pdf",
                "name": pdf_name,
                "pages": page_infos,
                "count": len(page_infos),
                "page_size": page_size,
                "orientation": orientation,
                "dpi": dpi,
                "margin_mm": margin_mm,
                "size_bytes": len(pdf_bytes),
                "pdf_data": base64.b64encode(pdf_bytes).decode("ascii"),
            },
            panel="toolbox:main",
        )


def make_tools(ctx: Any) -> list[Tool]:
    return [Img2PdfTool()]
