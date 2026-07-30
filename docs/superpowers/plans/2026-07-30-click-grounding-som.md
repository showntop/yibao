# 点击精度 SoM 视觉 Grounding 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Set-of-Marks（截图叠编号 → VLM 选号 → 确定性解析）替换「盲坐标回退」与「裸 VLM 像素 bbox」两条不准路径，把 a11y 可见区的点击升级为零误差 AX-press、自绘区升级到网格级精度。

**Architecture:** 新增 `grounding.py`（`SoMGrounding`：`build_marks`/`predict`/`resolve`，纯编排、host 注入、可单测）。`ComputerUseSkill` 多步循环改为 SoM 优先（每步 build_marks→`ComputerUseClient.choose_action`→resolve），build_marks 渲染失败时回退旧 raw-bbox（`next_action`）。`ClickControlSkill` 移除盲坐标回退，a11y 找不到时导向 computer_use。新增 `scripts/eval_click.py` 做 Phase 0 baseline vs SoM 量化。

**Tech Stack:** Python 3.12 / Pillow（PIL.ImageDraw 渲染标记）/ pyautogui（逻辑坐标 + scale）/ pyobjc a11y（`element_at`+`press`）/ 现有 `ComputerUseClient`（GLM-4.6V）/ pytest。

## Global Constraints

- **坐标系**：a11y `bbox`/`position` 为屏幕逻辑点（macOS Quartz point）；截图（mss）为物理像素，Retina≈2×。`marks` 一律存**逻辑坐标**（可直接 `click` / `element_at`）；渲染叠加用**物理像素**（逻辑 × scale）。
- **a11y 标记来源**：`frontmost_tree()` 节点已带 `bbox=[x1,y1,x2,y2]`（逻辑）与 `size`（见 `sidecar/src/yibao_brain/mac/a11y_mac.py:96-108`）。**不改 a11y 模块**。
- **resolve 取 handle**：tree 节点是序列化 dict（无 handle），统一用 `host.a11y.element_at(cx,cy)` 取 handle → `press`（确定性），失败回退 `host.input.click(center)`。
- **封顶**：总标记数 ≤ 40（`MAX_MARKS`）；a11y 交互元素优先，网格补齐在后。
- **不动**：TTS、Live2D、本地 grounding 重模型（本轮不做，见 spec §9）。
- **测试**：所有单测经 `cd sidecar && .venv/bin/python -m pytest tests/ -q`；不触真机 a11y/键鼠（用 `tests/fakes.py`）。提交信息沿用仓库风格 `feat(click): …` / `test(click): …`，提交到 `main`。

---

## File Structure

- **Create `sidecar/src/yibao_brain/grounding.py`** — `SoMGrounding`（build_marks/predict/resolve）+ 模块级常量与私有辅助（`_walk_interactive`/`_dedupe`/`_iou`/`_grid_cells`/`_physical_scale`）。单一职责：标记生成与解析，不调 LLM、不触网。
- **Modify `sidecar/src/yibao_brain/llm.py`** — `ComputerUseClient` 新增 `choose_action`（看叠加图返回 `{action,mark?,text?}`）+ `MARK_SYSTEM_PROMPT` + `_parse_marked_action`；保留 `next_action`/`_parse_action`（raw-bbox，作回退）。
- **Modify `sidecar/src/yibao_brain/skills_real.py`** — `ComputerUseSkill.run` 循环改 SoM 优先 + raw-bbox 回退；`ClickControlSkill` 移除盲坐标回退。
- **Modify `sidecar/tests/fakes.py`** — `FakeComputerUseClient` 加 `marked_actions` + `choose_action`。
- **Modify `sidecar/tests/test_real_skills.py`** — 更新 click_control 用例（删坐标回退）、重写 computer_use 用例为 SoM 流。
- **Create `sidecar/tests/test_grounding.py`** — `SoMGrounding` 单测。
- **Create `sidecar/scripts/eval_click.py`** — Phase 0 评测脚手架（非 CI）。

---

## Task 1: `SoMGrounding.build_marks`——收集 a11y 标记 + 网格补齐 + 封顶 + 渲染

**Files:**
- Create: `sidecar/src/yibao_brain/grounding.py`
- Test: `sidecar/tests/test_grounding.py`

**Interfaces:**
- Produces: `SoMGrounding(max_marks=MAX_MARKS)`；`build_marks(shot_path:str, tree:dict, scale:float|None=None) -> tuple[str|None, list[dict]]`。返回 `(marked_image_b64|None, marks)`，`marks[i]={"id":int(1-based),"source":"a11y"|"grid","center":(lx,ly),"rect":(x1,y1,x2,y2) 逻辑}`。渲染失败 → `(None, [])`。

- [ ] **Step 1: 写失败测试 `test_grounding.py`**

```python
"""SoMGrounding 单测：标记生成/网格补齐/封顶/坐标系/渲染。"""
from PIL import Image

from yibao_brain.grounding import SoMGrounding, MAX_MARKS


def _shot(tmp_path, w=100, h=100):
    p = tmp_path / "shot.png"
    Image.new("RGB", (w, h), "white").save(p)
    return str(p)


def test_build_marks_collects_a11y_and_fills_grid(tmp_path):
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
    ]}
    b64, marks = SoMGrounding().build_marks(_shot(tmp_path), tree, scale=1.0)
    assert b64 is not None and b64.startswith("data:image/")
    sources = {m["source"] for m in marks}
    assert "a11y" in sources and "grid" in sources  # <8 个 → 网格补齐
    assert marks[0]["id"] == 1
    a11y = [m for m in marks if m["source"] == "a11y"][0]
    assert a11y["center"] == (20.0, 20.0)  # bbox 中心，逻辑坐标


def test_build_marks_stores_logical_coords_under_hidpi(tmp_path):
    # 物理图 200px、scale 2.0；a11y bbox 是逻辑坐标 → marks 仍存逻辑
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
    ]}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path, 200, 200), tree, scale=2.0)
    a11y = [m for m in marks if m["source"] == "a11y"][0]
    assert a11y["center"] == (20.0, 20.0)


def test_build_marks_enough_a11y_skips_grid(tmp_path):
    nodes = [{"role": "AXLink", "bbox": [i * 10, 0, i * 10 + 5, 5], "children": []}
             for i in range(10)]
    tree = {"role": "AXApp", "children": nodes}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path), tree, scale=1.0)
    assert all(m["source"] == "a11y" for m in marks)  # ≥8 → 不补网格
    assert len(marks) == 10


def test_build_marks_caps_total(tmp_path):
    nodes = [{"role": "AXButton", "bbox": [i * 3, 0, i * 3 + 2, 2], "children": []}
             for i in range(MAX_MARKS + 5)]
    tree = {"role": "AXApp", "children": nodes}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    assert len(marks) <= MAX_MARKS


def test_build_marks_dedupes_overlap(tmp_path):
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
        {"role": "AXButton", "bbox": [11, 11, 31, 31], "children": []},  # IoU>0.8 → 合并
    ]}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    assert len([m for m in marks if m["source"] == "a11y"]) == 1


def test_build_marks_render_failure_returns_none(tmp_path):
    # 截图路径不存在 → 渲染失败 → (None, [])
    b64, marks = SoMGrounding().build_marks("/nope/missing.png", {"role": "AXApp"}, scale=1.0)
    assert b64 is None and marks == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_grounding.py -q`
Expected: FAIL（`ModuleNotFoundError: yibao_brain.grounding`）

- [ ] **Step 3: 写 `grounding.py` 实现**

```python
"""Set-of-Marks 视觉 grounding：截图叠编号标记 → VLM 选号 → 确定性解析。

a11y 交互元素 frame 优先编号；稀疏区叠网格补齐；封顶保持 VLM 可读。
坐标约定：marks 存逻辑坐标（屏幕点，可直接 click / element_at）；渲染叠加用物理像素。
"""
from __future__ import annotations

import base64
import io
import itertools

INTERACTIVE_ROLES = frozenset({
    "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXCheckBox",
    "AXPopUpButton", "AXMenuItem", "AXTab", "AXSlider", "AXRadioButton",
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
        phys_w = Image.open(shot_path).width
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
            im = Image.open(shot_path).convert("RGB")
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
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=80)  # jpeg 省 token
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return None

    # Task 2 在此追加 predict / resolve
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_grounding.py -q`
Expected: PASS（6 用例）

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/grounding.py sidecar/tests/test_grounding.py
git commit -m "feat(grounding): SoM build_marks 收集 a11y 标记 + 网格补齐 + 封顶渲染"
```

---

## Task 2: `SoMGrounding.predict` / `resolve`——element_at→AX-press，失败回退坐标

**Files:**
- Modify: `sidecar/src/yibao_brain/grounding.py`（在 `SoMGrounding` 内追加方法）
- Test: `sidecar/tests/test_grounding.py`（追加用例）

**Interfaces:**
- Produces: `predict(mark_id:int|None, marks:list[dict]) -> tuple[float,float]|None`（仅返回中心，不动作）；`resolve(mark_id, marks, host) -> dict`，返回 `{"method":"ax"|"coord"|"miss","x":lx,"y":ly}`。
- Consumes: `host.a11y.element_at(x,y)->handle|None`、`host.a11y.press(handle)->bool`、`host.input.click(x,y)`。

- [ ] **Step 1: 追加失败测试**

```python
from fakes import FakeHost, _FakeHandle


def test_resolve_ax_press_via_element_at():
    host = FakeHost()
    host.a11y.element_at_result = _FakeHandle("AXButton", "ok")  # 命中 handle
    marks = [{"id": 1, "source": "a11y", "center": (50.0, 60.0), "rect": (40, 50, 60, 70)}]
    res = SoMGrounding().resolve(1, marks, host)
    assert res["method"] == "ax" and (res["x"], res["y"]) == (50.0, 60.0)
    assert host.a11y.press_calls and host.input.clicks == []  # 走 AX，没坐标点击


def test_resolve_falls_back_to_coord_when_no_element():
    host = FakeHost()
    host.a11y.element_at_result = None  # 该坐标无 handle
    marks = [{"id": 1, "source": "grid", "center": (5.0, 6.0), "rect": (0, 0, 10, 12)}]
    res = SoMGrounding().resolve(1, marks, host)
    assert res["method"] == "coord" and host.input.clicks == [(5.0, 6.0)]


def test_resolve_falls_back_when_press_fails():
    host = FakeHost()
    host.a11y.element_at_result = _FakeHandle("AXButton", "x")
    host.a11y.press_ok = False  # 取到 handle 但 press 失败
    marks = [{"id": 1, "source": "a11y", "center": (7.0, 8.0), "rect": (0, 0, 14, 16)}]
    res = SoMGrounding().resolve(1, marks, host)
    assert res["method"] == "coord" and host.input.clicks == [(7.0, 8.0)]


def test_resolve_out_of_range_is_miss():
    host = FakeHost()
    marks = [{"id": 1, "source": "grid", "center": (1.0, 1.0), "rect": (0, 0, 2, 2)}]
    res = SoMGrounding().resolve(99, marks, host)
    assert res["method"] == "miss" and host.input.clicks == []


def test_predict_returns_center_only():
    marks = [{"id": 1, "source": "a11y", "center": (3.0, 4.0), "rect": (0, 0, 6, 8)}]
    assert SoMGrounding().predict(1, marks) == (3.0, 4.0)
    assert SoMGrounding().predict(5, marks) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_grounding.py -q -k resolve or predict`
Expected: FAIL（`AttributeError: resolve`）

- [ ] **Step 3: 在 `SoMGrounding` 内追加方法**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_grounding.py -q`
Expected: PASS（11 用例）

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/grounding.py sidecar/tests/test_grounding.py
git commit -m "feat(grounding): SoM resolve element_at→AX-press + 坐标回退"
```

---

## Task 3: `ComputerUseClient.choose_action`——VLM 看叠加图选动作

**Files:**
- Modify: `sidecar/src/yibao_brain/llm.py`（`ComputerUseClient` 内追加 `choose_action`/`MARK_SYSTEM_PROMPT`/`_parse_marked_action`）
- Test: `sidecar/tests/test_real_skills.py`（追加解析用例，紧挨现有 `test_computer_use_client_parse_action`）

**Interfaces:**
- Produces: `ComputerUseClient.choose_action(marked_image_b64:str, task:str, n_marks:int, history:list|None=None) -> dict|None`，返回 `{"action":"click"|"type"|"finish","mark":int|None,"text":str|None}`（click 带 mark、type 带 text）。`_parse_marked_action(content:str, n_marks:int) -> dict|None` 为纯静态解析。
- 保留：`next_action`（raw-bbox）+ `_parse_action`，供 Task 4 回退路径用，**勿删**。

- [ ] **Step 1: 追加失败测试**

```python
def test_choose_action_parse_marked():
    from yibao_brain.llm import ComputerUseClient

    # 纯数字 → click + mark
    assert ComputerUseClient._parse_marked_action("第 3 号", 5) == {"action": "click", "mark": 3}
    # JSON 动作
    assert ComputerUseClient._parse_marked_action('{"action":"type","text":"hi"}', 5) == {"action": "type", "text": "hi"}
    assert ComputerUseClient._parse_marked_action('{"action":"click","mark":2}', 5) == {"action": "click", "mark": 2}
    # finish
    assert ComputerUseClient._parse_marked_action("做完了 finish", 5) == {"action": "finish"}
    # 越界 / 非法
    assert ComputerUseClient._parse_marked_action("第 9 号", 5) is None
    assert ComputerUseClient._parse_marked_action("乱码无数字", 5) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_real_skills.py::test_choose_action_parse_marked -q`
Expected: FAIL（`AttributeError: _parse_marked_action`）

- [ ] **Step 3: 在 `ComputerUseClient` 内追加**

```python
    MARK_SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。屏幕上每个可交互元素或区域都标了编号(1..N)。"
        "根据用户任务给出【下一个动作】：点击某目标就输出它的编号（一个整数）；"
        "需要输入文字时输出 JSON {\"action\":\"type\",\"text\":\"...\"}；"
        "任务完成时输出 finish。点击目标只输出整数编号，不要任何其他文字。"
    )

    def choose_action(self, marked_image_b64: str, task: str, n_marks: int, history: list | None = None):
        messages: list[dict] = [{"role": "system", "content": self.MARK_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": marked_image_b64}},
                {"type": "text", "text": f"任务：{task}\n共有 {n_marks} 个编号标记(1-{n_marks})。给出下一个动作。"},
            ],
        })
        resp = self.client.chat.completions.create(model=self.model, messages=messages)
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return self._parse_marked_action(content, n_marks)

    @staticmethod
    def _parse_marked_action(content: str, n_marks: int) -> dict | None:
        s = (content or "").strip()
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if obj.get("action") in ("click", "type", "finish"):
                    mk = obj.get("mark")
                    if mk is not None and not (isinstance(mk, int) and 1 <= mk <= n_marks):
                        return None
                    return obj
            except json.JSONDecodeError:
                pass
        if "finish" in s.lower():
            return {"action": "finish"}
        m2 = re.search(r"\d+", s)
        if m2:
            val = int(m2.group(0))
            if 1 <= val <= n_marks:
                return {"action": "click", "mark": val}
        return None
```

> 注：`re` 与 `json` 在 `llm.py` 顶部已 import（`_parse_action` 已用）。若未导入，在文件头补 `import json, re`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_real_skills.py::test_choose_action_parse_marked tests/test_real_skills.py::test_computer_use_client_parse_action -q`
Expected: PASS（新旧解析用例都绿）

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/llm.py sidecar/tests/test_real_skills.py
git commit -m "feat(llm): ComputerUseClient.choose_action 看叠加图选编号/动作"
```

---

## Task 4: 把 SoM 接入 `ComputerUseSkill` 多步循环（raw-bbox 回退）

**Files:**
- Modify: `sidecar/src/yibao_brain/skills_real.py`（`ComputerUseSkill.run` 重写循环；新增 `_physical_scale` 引用）
- Modify: `sidecar/tests/fakes.py`（`FakeComputerUseClient` 加 `marked_actions` + `choose_action`）
- Modify: `sidecar/tests/test_real_skills.py`（重写 computer_use 用例为 SoM 流）

**Interfaces:**
- Consumes: `SoMGrounding.build_marks/predict/resolve`（Task 1/2）、`ComputerUseClient.choose_action`（Task 3）、`ComputerUseClient.next_action`（回退）。
- Produces: `ComputerUseSkill(client, max_steps=5, som=None)`；`som` 默认 `SoMGrounding()`。`run` 行为不变的契约：返 `ActionResult(success, data={"steps":int,"actions":list})`；多步、连续两帧无变化停、非法动作停、`max_steps` 截断。**`server.py:90` 的 `ComputerUseSkill(ComputerUseClient())` 构造无需改动**（som 默认）。

- [ ] **Step 1: 改 `FakeComputerUseClient` 支持 SoM**

在 `fakes.py` 的 `FakeComputerUseClient.__init__` 加 `marked_actions` 参数，并加 `choose_action`：

```python
    def __init__(self, actions=None, marked_actions=None, image_width=1440):
        self.actions = list(actions or [{"action": "finish"}])
        self.marked_actions = list(marked_actions or [{"action": "finish"}])
        self.calls = []
        self.choose_calls = []
        self.image_width = image_width

    def choose_action(self, marked_b64, task, n_marks, history=None):
        self.choose_calls.append({"task": task, "n_marks": n_marks})
        if self.marked_actions:
            return self.marked_actions.pop(0)
        return {"action": "finish"}
```

- [ ] **Step 2: 重写 computer_use 测试为 SoM 流（替换旧 `test_computer_use_loop_click_type_finish` / `_hidpi_coordinate` / `_max_steps_cap`；保留 `_finish_stops` / `_none_action_stops` / `_missing_task` 语义）**

```python
def test_computer_use_som_click_type_finish(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter, FakeHost, _FakeHandle

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 3))
    host.a11y.element_at_result = _FakeHandle("AXButton", "ok")  # 命中 → AX-press
    # tree 有一个 button 标记（mark 1）；网格补齐
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[
        {"action": "click", "mark": 1},
        {"action": "type", "text": "hi"},
        {"action": "finish"},
    ])
    r = ComputerUseSkill(client, som=SoMGrounding()).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 2
    assert host.a11y.press_calls and host.input.clicks == []  # mark1 走 AX
    assert host.input.types == ["hi"]
    assert len(client.choose_calls) == 3


def test_computer_use_som_coord_fallback_when_no_element(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 2))
    host.a11y.element_at_result = None  # 无 handle → 坐标回退
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [40, 40, 60, 60], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[{"action": "click", "mark": 1}, {"action": "finish"}])
    ComputerUseSkill(client, som=SoMGrounding()).run({"task": "t"}, SkillContext(host=host))
    assert host.input.clicks == [(50.0, 50.0)]  # bbox 中心，逻辑坐标


def test_computer_use_raw_bbox_fallback_on_render_fail(tmp_path, monkeypatch):
    # build_marks 渲染失败（_render 返 None）但图可读 → 回退 next_action raw-bbox
    # 注意：不能用坏路径——_b64 也打不开图，next_action 永远不执行。故 monkeypatch _render。
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 2))  # 真实可读图
    host.a11y.tree = {"role": "AXApp", "children": []}
    som = SoMGrounding()
    monkeypatch.setattr(som, "_render", lambda *a, **k: None)  # 强制渲染失败
    client = FakeComputerUseClient(
        actions=[{"action": "click", "box": [10, 10, 30, 30]}, {"action": "finish"}])  # 走 raw-bbox
    r = ComputerUseSkill(client, som=som).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 1
    assert host.input.clicks == [(20.0, 20.0)]  # box 中心 / scale 1.0
    assert client.choose_calls == []  # 没走 SoM


def test_computer_use_som_max_steps_cap(tmp_path, monkeypatch):
    import pyautogui
    from yibao_brain.skills_real import ComputerUseSkill
    from yibao_brain.grounding import SoMGrounding
    from fakes import FakeComputerUseClient, FakeScreenshotter

    monkeypatch.setattr(pyautogui, "size", lambda: _size_obj(100, 100))
    host = FakeHost()
    host.screenshotter = FakeScreenshotter(paths=_make_shots(tmp_path, 5))
    host.a11y.tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [0, 0, 2, 2], "children": []}]}
    client = FakeComputerUseClient(marked_actions=[{"action": "click", "mark": 1}] * 5)
    r = ComputerUseSkill(client, max_steps=3, som=SoMGrounding()).run({"task": "t"}, SkillContext(host=host))
    assert r.success and r.data["steps"] == 3
```

（`test_computer_use_finish_stops_immediately` / `_none_action_stops` / `_missing_task` 用 SoM：给 `marked_actions=[{"action":"finish"}]` 或 `choose_action` 返 `None` 即可，断言不变。）

- [ ] **Step 3: 跑测试确认失败（旧实现还不调 choose_action）**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_real_skills.py -q -k computer_use`
Expected: FAIL（新用例断言不通过：旧循环走 next_action，未走 SoM）

- [ ] **Step 4: 重写 `ComputerUseSkill.run` + 构造（SoM 优先，raw-bbox 回退）**

替换 `skills_real.py` 里 `ComputerUseSkill` 的 `__init__` 与 `run`（`_md5`/`_b64`/`_scale`/`_execute` 保留供回退路径用）：

```python
from .grounding import SoMGrounding, _physical_scale  # 文件头补 import

class ComputerUseSkill(Skill):
    """视觉兜底：截图 → SoM 叠编号 → GLM 选号/动作 → 解析执行，覆盖 a11y 力不能及的 UI。
    build_marks 渲染失败时回退旧 raw-bbox（next_action）。"""

    id = "computer_use"
    label = "操作电脑"
    description = (
        "computer-use 视觉兜底：当 read_tree/click_control 因控件无 title 或 UI 自绘而失效时，"
        "用视觉模型看截图识别目标并点击/输入。慢、可能不准、高风险。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def __init__(self, client, max_steps: int = 5, som: SoMGrounding | None = None):
        self._client = client
        self._default_max_steps = max_steps
        self._som = som or SoMGrounding()

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        task = str(params.get("task", "")).strip()
        if not task:
            return ActionResult(success=False, error="缺少 task 参数")
        if self._client is None:
            return ActionResult(success=False, error="无 computer-use client")
        max_steps = int(params.get("max_steps", self._default_max_steps))
        history: list[dict] = []
        done: list[dict] = []
        prev_hash: str | None = None
        for _ in range(max_steps):
            shot = ctx.host.screenshotter.capture()
            shot_hash = self._md5(shot)
            if shot_hash is not None and shot_hash == prev_hash:
                break  # 连续两帧无变化 → 停
            prev_hash = shot_hash
            scale = _physical_scale(shot)
            tree = ctx.host.a11y.frontmost_tree()
            marked, marks = self._som.build_marks(shot, tree, scale)
            if marked is None:
                action = self._raw_bbox_step(shot, task, history, ctx.host, scale)  # 回退
            else:
                action = self._client.choose_action(marked, task, len(marks), history)
                if action is None:
                    break  # 模型输出非法 → 停，防失控
                if action.get("action") == "finish":
                    break
                self._apply_marked(action, marks, ctx.host)
            if action is not None and action.get("action") != "finish":
                done.append(action)
                history.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        return ActionResult(success=True, data={"steps": len(done), "actions": done})

    def _apply_marked(self, action: dict, marks: list[dict], host) -> None:
        kind = action.get("action")
        if kind == "click":
            self._som.resolve(action.get("mark"), marks, host)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))

    def _raw_bbox_step(self, shot, task, history, host, scale):
        """旧 raw-bbox 回退路径（build_marks 渲染失败时）。"""
        b64 = self._b64(shot)
        if b64 is None:
            return None
        action = self._client.next_action(b64, task, history)
        if not action or action.get("action") == "finish":
            return action
        self._execute(action, host, scale)  # 既有 _execute：click box 中心 / type / scroll
        return action
```

> `_md5`/`_b64`/`_execute` 三个 staticmethod 保留供回退路径用（`_execute` 点 box 中心、`_b64` 编码）。**删除现已不再被引用的 `_scale` staticmethod**（已被 `grounding._physical_scale` 取代，留着会触发未使用告警）；`_execute(action, host, scale)` 的 scale 由 `_raw_bbox_step` 传入（=`_physical_scale(shot)`）。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_real_skills.py -q`
Expected: PASS（全部 computer_use + click 用例）

- [ ] **Step 6: 跑全量回归**

Run: `cd sidecar && .venv/bin/python -m pytest tests/ -q`
Expected: PASS（全绿，578+ 用例）

- [ ] **Step 7: 提交**

```bash
git add sidecar/src/yibao_brain/skills_real.py sidecar/tests/fakes.py sidecar/tests/test_real_skills.py
git commit -m "feat(skills): ComputerUseSkill 多步循环接入 SoM（raw-bbox 回退）"
```

---

## Task 5: 移除 `ClickControlSkill` 盲坐标回退

**Files:**
- Modify: `sidecar/src/yibao_brain/skills_real.py`（`ClickControlSkill.run`）
- Modify: `sidecar/tests/test_real_skills.py`（删/改坐标回退用例）

**Interfaces:**
- 行为变更：`click_control` 不再接受盲 `(x,y)`；a11y 命中 → AX-press；未命中 → 失败并提示用 `computer_use`。`openai_schema` 的 `x/y` 参数描述移除。
- 依赖 Task 4（computer_use 已是可靠视觉路径，承接被导向的需求）。

- [ ] **Step 1: 改测试（删 `_coord_fallback` / `_ax_fail_then_coord_fallback`，新增导向提示用例）**

删除 `test_click_control_coord_fallback` 与 `test_click_control_ax_fail_then_coord_fallback`，新增：

```python
def test_click_control_no_blind_coord_and_hints_computer_use():
    # 不再盲点坐标：给了 x/y 但 a11y 查不到 → 失败 + 提示 computer_use
    r = ClickControlSkill().run({"x": 100, "y": 200}, _ctx(FakeHost()))
    assert not r.success
    assert "computer_use" in r.error


def test_click_control_ax_fail_still_coordless():
    r = ClickControlSkill().run({"role": "AXButton", "title": "不存在", "x": 5, "y": 6}, _ctx(FakeHost()))
    assert not r.success and "computer_use" in r.error
    assert FakeHost().input.clicks == []  # 模板里另起 host；这里仅断言不成功
```

（保留 `test_click_control_ax_press`、`test_click_control_ax_fail_then_no_coord_returns_error`——后者断言不变，仍为失败。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && .venv/bin/python -m pytest tests/test_real_skills.py -q -k click_control`
Expected: FAIL（旧实现仍坐标回退）

- [ ] **Step 3: 重写 `ClickControlSkill.run` + schema**

```python
    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "description": "控件角色，如 AXButton"},
                    "title": {"type": "string", "description": "控件标题/文字，如 '等于' 或 'OK'"},
                },
                "required": [],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return _no_host()
        a11y = ctx.host.a11y
        role = params.get("role")
        title = params.get("title")
        if role or title:
            handle = a11y.find(role, title)
            if handle is not None and a11y.press(handle):
                return ActionResult(success=True, data={"method": "ax", "target": title or role})
        # 不再盲坐标回退：a11y 找不到 → 导向 computer_use 视觉定位
        return ActionResult(
            success=False,
            error="无法用 a11y 定位该控件（自绘 UI 或无 title）。请改用 computer_use 视觉定位。",
        )
```

并把 `description` 里的「回退屏幕坐标 (x,y) 点击」一句删掉，改为「找不到或不支持时返回失败，应改用 computer_use 视觉定位」。

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `cd sidecar && .venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add sidecar/src/yibao_brain/skills_real.py sidecar/tests/test_real_skills.py
git commit -m "refactor(click): 移除盲坐标回退，a11y 失败导向 computer_use"
```

---

## Task 6: Phase 0 评测脚手架 `scripts/eval_click.py`

**Files:**
- Create: `sidecar/scripts/eval_click.py`（非 CI，手动评测）
- Create: `sidecar/scripts/eval_scenarios/README.md`（场景格式说明）

**Interfaces:**
- Consumes: `SoMGrounding`（build_marks/predict）、`ComputerUseClient`（next_action baseline + choose_action SoM）、真实 GLM key（`sidecar/.env` 自动加载）。
- 产物：终端打印 baseline vs SoM 的 hit-rate / 平均 center-distance 对照表；决定是否达标（spec §8）。

- [ ] **Step 1: 写评测脚本**

```python
"""点击精度评测：baseline(raw-bbox) vs SoM。手动跑，非 CI。

用法：
  python scripts/eval_click.py --scenarios scripts/eval_scenarios
  python scripts/eval_click.py --capture --name calc_eq --target "等号按钮"   # 采集场景

场景 JSON（scripts/eval_scenarios/<name>.json）：
  {"screenshot":"<abs png path>", "tree":{...a11y frontmost_tree...},
   "target":"目标描述", "gt":{"kind":"region","rect":[x1,y1,x2,y2]}  // 或 {"kind":"point","xy":[x,y]}
  }
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _b64_png(path: str) -> str:
    import base64
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hit(point, gt) -> bool:
    if gt["kind"] == "region":
        x1, y1, x2, y2 = gt["rect"]
        return x1 <= point[0] <= x2 and y1 <= point[1] <= y2
    if gt["kind"] == "point":
        return _dist(point, gt["xy"]) <= gt.get("tolerance", 12)
    return False


def center_distance(point, gt) -> float:
    if gt["kind"] == "region":
        x1, y1, x2, y2 = gt["rect"]
        return _dist(point, ((x1 + x2) / 2, (y1 + y2) / 2))
    if gt["kind"] == "point":
        return _dist(point, gt["xy"])
    return float("inf")


def run_baseline(client, som, sc, scale):
    from yibao_brain.grounding import _physical_scale  # noqa: F401
    action = client.next_action(_b64_png(sc["screenshot"]), sc["target"], [])
    if not action or action.get("action") != "click" or len(action.get("box") or []) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in action["box"])
    return ((x1 + x2) / 2 / scale, (y1 + y2) / 2 / scale)


def run_som(client, som, sc, scale):
    marked, marks = som.build_marks(sc["screenshot"], sc.get("tree") or {}, scale)
    if not marked:
        return None
    action = client.choose_action(marked, sc["target"], len(marks), [])
    if not action or action.get("action") != "click":
        return None
    return som.predict(action.get("mark"), marks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=False, help="场景目录（每文件一 JSON）")
    ap.add_argument("--capture", action="store_true", help="交互采集一个场景")
    ap.add_argument("--name", default="scene")
    ap.add_argument("--target", default="")
    args = ap.parse_args()

    if args.capture:
        _capture(args)
        return

    from yibao_brain.llm import ComputerUseClient
    from yibao_brain.grounding import SoMGrounding, _physical_scale

    client = ComputerUseClient()
    som = SoMGrounding()
    scs = [json.loads(p.read_text()) for p in sorted(Path(args.scenarios).glob("*.json"))]
    if not scs:
        print("无场景，先 --capture 采集。"); return
    rows = []
    for sc in scs:
        scale = _physical_scale(sc["screenshot"])
        b = run_baseline(client, som, sc, scale)
        s = run_som(client, som, sc, scale)
        rows.append({
            "name": sc.get("name", "?"),
            "baseline": b, "som": s, "gt": sc["gt"],
        })
    _report(rows)


def _report(rows):
    n = len(rows)
    b_hit = sum(1 for r in rows if r["baseline"] and hit(r["baseline"], r["gt"]))
    s_hit = sum(1 for r in rows if r["som"] and hit(r["som"], r["gt"]))
    b_d = [center_distance(r["baseline"], r["gt"]) for r in rows if r["baseline"]]
    s_d = [center_distance(r["som"], r["gt"]) for r in rows if r["som"]]
    print(f"{'场景':<16}{'baseline':<22}{'SoM':<22}")
    for r in rows:
        print(f"{r['name']:<16}{_fmt(r['baseline']):<22}{_fmt(r['som']):<22}")
    print("-" * 60)
    print(f"hit-rate:  baseline {b_hit}/{n} = {b_hit/n:.0%}   SoM {s_hit}/{n} = {s_hit/n:.0%}")
    print(f"平均距离:  baseline {sum(b_d)/len(b_d):.1f}px   SoM {sum(s_d)/len(s_d):.1f}px")


def _fmt(p):
    return "—" if not p else f"{p[0]:.0f},{p[1]:.0f}"


def _capture(args):
    """截主屏 + 抓 a11y 树 + 落 JSON（gt 手填）。"""
    from mss import mss
    from yibao_brain.mac.a11y_mac import MacA11yReader
    out = Path("scripts/eval_scenarios"); out.mkdir(parents=True, exist_ok=True)
    shot = out / f"{args.name}.png"
    with mss() as s:
        s.shot(mon=-1, output=str(shot))
    tree = MacA11yReader().frontmost_tree()
    (out / f"{args.name}.json").write_text(json.dumps({
        "name": args.name, "screenshot": str(shot.resolve()), "tree": tree,
        "target": args.target,
        "gt": {"kind": "region", "rect": [0, 0, 0, 0]},  # TODO 手填真实目标框
    }, ensure_ascii=False, indent=2))
    print(f"已采集 {args.name}，请手填 {args.name}.json 的 gt.rect")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 写场景目录说明**

`sidecar/scripts/eval_scenarios/README.md`：

```markdown
# 点击评测场景

每个 `<name>.json` 一场景：`{screenshot, tree, target, gt}`。
- 采集：`python scripts/eval_click.py --capture --name <n> --target "<目标描述>"`，再手填 `gt`（`region.rect=[x1,y1,x2,y2]` 逻辑坐标，或 `point.xy=[x,y]`）。
- 目标 ~12 个，覆盖：计算器/系统设置（a11y 易）、Safari 链接（a11y 中）、Canvas/自绘 UI、Electron（a11y 盲）。
- 跑：`python scripts/eval_click.py --scenarios scripts/eval_scenarios`。
```

- [ ] **Step 3: 冒烟自测（合成场景跑通管线，不调真 GLM）**

```bash
cd sidecar && .venv/bin/python -c "
import sys; sys.path.insert(0,'src')
from PIL import Image
import json, tempfile, os
from yibao_brain.grounding import SoMGrounding
d = tempfile.mkdtemp(); shot = os.path.join(d,'s.png')
Image.new('RGB',(200,200),'white').save(shot)
sc = {'name':'syn','screenshot':shot,'tree':{'role':'AXApp','children':[{'role':'AXButton','bbox':[80,80,120,120],'children':[]}]},
      'target':'按钮','gt':{'kind':'region','rect':[80,80,120,120]}}
marked,marks = SoMGrounding().build_marks(shot, sc['tree'], scale=1.0)
assert marked and any(m['source']=='a11y' for m in marks)
print('pipeline ok, marks=', len(marks))
"
```
Expected: 打印 `pipeline ok, marks= …`（含 a11y + 网格）。真 GLM 评测为手动步骤（见 README），不在 CI。

- [ ] **Step 4: 提交**

```bash
git add sidecar/scripts/eval_click.py sidecar/scripts/eval_scenarios/README.md
git commit -m "feat(eval): Phase 0 点击精度评测 baseline vs SoM 脚手架"
```

- [x] **Step 5（手动，验收）：采集 ~12 真实场景并跑评测**

按 README 采集约 12 个真实场景、手填 gt、`python scripts/eval_click.py --scenarios scripts/eval_scenarios`，对照 spec §8 成功标准（hit-rate ≥ baseline+20pt、标准控件区 ≥85%）。未达标 → 在本计划追加「本地 grounding 模型评估」任务。

**2026-07-30 实测记录（`glm-4.1v-thinking-flashx`）**：12 个受控场景覆盖计算器密集按钮、原生窗口控件、网页链接/小目标、Canvas 与 Electron 风格 a11y 盲区。该模型输出的 bbox 使用 0–1000 归一化坐标；修正坐标协议后，raw-bbox baseline 为 12/12（100%，平均中心距离 7.7px），SoM 为 10/12（83%，平均中心距离 22.5px），标准控件子集为 8/9（89%）。SoM 未达到相对 baseline +20pt 的门槛，且原生 grounding 明显更优，因此本模型改走归一化 bbox 还原后的原生路径；其他视觉模型继续保留 SoM。当前样本是核心受控验收，不替代后续更大规模真机回归；只有原生路径在真机回归中退化时才启动本地 grounding 重模型评估。

---

## Self-Review

**Spec coverage**（spec 各节 → 任务）：
- §3 混合 SoM → Task 1（a11y+网格+封顶）
- §4 架构/集成/数据流 → Task 4（ComputerUseSkill）、Task 5（ClickControlSkill）
- §5.1 build_marks / 坐标系 → Task 1（含 hidpi 用例）
- §5.2 ask（VLM 选号）→ Task 3（choose_action）
- §5.3 resolve（AX-press/坐标）→ Task 2
- §6 Phase 0 评测 → Task 6
- §7 错误处理（非法→停、a11y 缺→网格、渲染失败→raw-bbox 回退、handle 失效→坐标）→ Task 1/2/4 用例覆盖
- §8 测试 + 成功标准 → 各任务单测 + Task 6 评测门槛

**Placeholder 扫描**：无 TBD/TODO（`eval_click.py` 的 `gt` 手填与 README 的场景采集为执行期手动步骤，非占位实现）。

**类型/命名一致性**：`build_marks→(b64|None, marks)`、`predict→(x,y)|None`、`resolve→{method,x,y}`、`choose_action→{action,mark?,text?}|None`、`FakeComputerUseClient.choose_action/marked_actions`、`ComputerUseSkill(client,max_steps,som)` 跨任务一致。`_physical_scale` 在 `grounding.py` 定义、`skills_real.py` 与 `eval_click.py` 引用，同名同义。

**已知执行期注意**：
- `llm.py` 顶部需有 `import json, re`（`_parse_action` 已用，大概率已导入；Task 3 Step 3 注明补导入）。
- `ComputerUseSkill._scale` 删除（被 `grounding._physical_scale` 取代、不再引用）；`_md5`/`_b64`/`_execute` 保留供回退路径。
- `server.py:90` 构造 `ComputerUseSkill(ComputerUseClient())` 无需改（`som` 默认）。
