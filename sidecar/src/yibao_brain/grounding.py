"""Set-of-Marks 视觉 grounding：截图叠编号标记 → VLM 选号 → 确定性解析。

a11y 交互元素 frame 优先编号；稀疏区叠网格补齐；封顶保持 VLM 可读。
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
GRID_TRIGGER = 8      # a11y 交互元素 < 此数 → 叠网格补齐（疑似自绘 UI）
GRID_COLS, GRID_ROWS = 6, 4


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


def _grid_cells(logical_w: float, logical_h: float) -> list[dict]:
    cw, ch = logical_w / GRID_COLS, logical_h / GRID_ROWS
    out = []
    for r in range(GRID_ROWS):
        for c in range(GRID_COLS):
            x1, y1 = c * cw, r * ch
            x2, y2 = x1 + cw, y1 + ch
            out.append({"source": "grid",
                        "center": ((x1 + x2) / 2, (y1 + y2) / 2),
                        "rect": (x1, y1, x2, y2)})
    return out


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
            return None, []  # 截图打不开 → 回退 raw-bbox
        logical_w, logical_h = phys_w / scale, phys_h / scale
        if len(items) < GRID_TRIGGER:
            items.extend(_grid_cells(logical_w, logical_h))
        # 封顶：a11y 优先保序，网格在后
        a11y = [it for it in items if it["source"] == "a11y"]
        grid = [it for it in items if it["source"] == "grid"]
        if len(items) > self._max:
            grid_budget = max(0, self._max - len(a11y))
            a11y = a11y[: self._max]
            items = a11y + grid[:grid_budget]
        marks = [{**it, "id": i + 1} for i, it in enumerate(items[: self._max])]
        b64 = self._render(shot_path, marks, scale)
        return (b64, marks) if b64 else (None, [])

    def _render(self, shot_path: str, marks: list[dict], scale: float):
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
                for m in marks:
                    x1, y1, x2, y2 = (v * scale for v in m["rect"])  # 逻辑→物理
                    draw.rectangle([x1, y1, x2, y2], outline=(226, 32, 32), width=lw)
                    cx, cy = (v * scale for v in m["center"])
                    draw.text((cx + 2, cy + 2), str(m["id"]), fill=(226, 32, 32), font=font)
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
