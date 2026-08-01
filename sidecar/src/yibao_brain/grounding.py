"""Set-of-Marks 分层视觉 grounding：a11y 数字红框 + 区域字母灰框双轨；模型选元素直点，选区域裁切放大二次精化（zoom_ground）。坐标约定同前：marks/zones 存逻辑坐标，渲染叠加用物理像素。

a11y 交互元素 frame 优先编号；封顶保持 VLM 可读。
坐标约定：marks 存逻辑坐标（屏幕点，可直接 click / element_at）；渲染叠加用物理像素。
"""
from __future__ import annotations

import base64
import io

# AXRow：大纲/列表行（系统设置侧边栏、Finder 列表等可点行）
INTERACTIVE_ROLES = frozenset({
    "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXCheckBox",
    "AXPopUpButton", "AXMenuItem", "AXTab", "AXSlider", "AXRadioButton",
    "AXRow",
    "AXComboBox", "AXIncrementor", "AXDisclosureTriangle",
})
MAX_MARKS = 40
ZONE_COLS, ZONE_ROWS = 3, 2


def _physical_scale(shot_path: str) -> float:
    """截图像素物理宽 / 逻辑宽（Retina≈2）。失败退化 1.0。"""
    try:
        from PIL import Image
        import pyautogui
        with Image.open(shot_path) as _im:
            phys_w = _im.width
        logical_w = pyautogui.size().width
        return phys_w / logical_w if logical_w else 1.0
    except Exception:
        return 1.0


def _walk_interactive(node: dict, out: list[dict]) -> None:
    """递归收集 role 命中白名单、bbox 合法（x2>x1,y2>y1）的节点。"""
    if node.get("role") in INTERACTIVE_ROLES:
        bbox = node.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            x1, y1, x2, y2 = (float(v) for v in bbox)
            if x2 > x1 and y2 > y1:
                out.append({"source": "a11y",
                            "center": ((x1 + x2) / 2, (y1 + y2) / 2),
                            "rect": (x1, y1, x2, y2)})
    for c in node.get("children") or []:
        _walk_interactive(c, out)


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _dedupe(items: list[dict], iou_thresh: float = 0.8) -> list[dict]:
    kept: list[dict] = []
    for it in items:
        if not any(_iou(it["rect"], k["rect"]) > iou_thresh for k in kept):
            kept.append(it)
    return kept


def _zones(logical_w: float, logical_h: float) -> list[dict]:
    """字母轨区域：3 列 2 行行优先 A-F，逻辑坐标。"""
    zw, zh = logical_w / ZONE_COLS, logical_h / ZONE_ROWS
    out = []
    for r in range(ZONE_ROWS):
        for c in range(ZONE_COLS):
            x1, y1 = c * zw, r * zh
            x2, y2 = x1 + zw, y1 + zh
            out.append({"letter": chr(ord("A") + r * ZONE_COLS + c),
                        "rect": (x1, y1, x2, y2),
                        "center": ((x1 + x2) / 2, (y1 + y2) / 2)})
    return out


def _dashed_rect(draw, rect, color, width: int, dash: int = 10, gap: int = 7) -> None:
    """PIL 无原生虚线：四边按 dash/gap 分段画。"""
    x1, y1, x2, y2 = rect

    def segments(a, b):
        pos = a
        while pos < b:
            yield pos, min(pos + dash, b)
            pos += dash + gap

    for xa, xb in segments(x1, x2):
        draw.line([(xa, y1), (xb, y1)], fill=color, width=width)
        draw.line([(xa, y2), (xb, y2)], fill=color, width=width)
    for ya, yb in segments(y1, y2):
        draw.line([(x1, ya), (x1, yb)], fill=color, width=width)
        draw.line([(x2, ya), (x2, yb)], fill=color, width=width)


class SoMGrounding:
    def __init__(self, max_marks: int = MAX_MARKS):
        self._max = max_marks

    def build_marks(self, shot_path: str, tree: dict, scale: float | None = None):
        if scale is None:
            scale = _physical_scale(shot_path)
        items: list[dict] = []
        if isinstance(tree, dict):
            _walk_interactive(tree, items)
        items = _dedupe(items)
        try:
            from PIL import Image
            with Image.open(shot_path) as im:
                phys_w, phys_h = im.size
        except Exception:
            return None, [], []  # 截图打不开 → 回退 raw-bbox
        logical_w, logical_h = phys_w / scale, phys_h / scale
        zones = _zones(logical_w, logical_h)
        marks = [{**it, "id": i + 1} for i, it in enumerate(items[: self._max])]
        b64 = self._render(shot_path, marks, zones, scale)
        return (b64, marks, zones) if b64 else (None, [], [])

    def _render(self, shot_path: str, marks: list[dict], zones: list[dict], scale: float):
        try:
            from PIL import Image, ImageDraw, ImageFont
            buf = io.BytesIO()
            with Image.open(shot_path) as _raw:
                im = _raw.convert("RGB")
                draw = ImageDraw.Draw(im)
                try:
                    font = ImageFont.truetype(
                        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                        max(14, im.width // 40),
                    )
                except Exception:
                    font = None
                lw = max(2, im.width // 400)
                zfont = None
                try:
                    zfont = ImageFont.truetype(
                        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                        max(28, im.width // 24),
                    )
                except Exception:
                    zfont = font
                for z in zones:
                    zx1, zy1, zx2, zy2 = (v * scale for v in z["rect"])
                    _dashed_rect(draw, [zx1, zy1, zx2, zy2], (120, 120, 120), max(1, lw - 1))
                    draw.text((zx1 + 6, zy1 + 4), z["letter"], fill=(120, 120, 120), font=zfont)
                for m in marks:
                    x1, y1, x2, y2 = (v * scale for v in m["rect"])  # 逻辑→物理
                    draw.rectangle([x1, y1, x2, y2], outline=(226, 32, 32), width=lw)
                    # 标签放框内左上角 + 字号随标记缩放：不压控件字符（slice1 诊断证据）
                    w, h = x2 - x1, y2 - y1
                    fs = max(12, min(im.width // 40, int(min(w, h) * 0.45)))
                    try:
                        mfont = ImageFont.truetype(
                            "/System/Library/Fonts/Supplemental/Arial Bold.ttf", fs)
                    except Exception:
                        mfont = font
                    draw.text((x1 + 2, y1 + 2), str(m["id"]), fill=(226, 32, 32), font=mfont)
                im.save(buf, format="JPEG", quality=80)  # jpeg 省 token
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    def predict(self, mark_id, marks):
        """返回 mark_id(1-based) 的逻辑中心；越界/非法 → None。不动作。"""
        if not isinstance(mark_id, int) or mark_id < 1 or mark_id > len(marks):
            return None
        return marks[mark_id - 1]["center"]

    def resolve(self, mark_id, marks, host) -> dict:
        """mark_id → element_at 取 handle 做 AX-press（确定性），失败回退坐标点击。"""
        center = self.predict(mark_id, marks)
        if center is None:
            return {"method": "miss"}
        cx, cy = center
        handle = None
        element_at = getattr(host.a11y, "element_at", None)
        if callable(element_at):
            try:
                handle = element_at(cx, cy)
            except Exception:
                handle = None
        if handle is not None and host.a11y.press(handle):
            return {"method": "ax", "x": cx, "y": cy}
        host.input.click(cx, cy)
        return {"method": "coord", "x": cx, "y": cy}
