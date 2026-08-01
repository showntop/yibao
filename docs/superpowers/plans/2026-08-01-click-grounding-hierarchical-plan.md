# 点击 grounding 分层两阶段（2A′）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地双轨标记 + 区域放大的分层 SoM，复测对照 spec §3：可及桶 7/7（calc×3 + sysset×2 + safari×2），盲桶 ≥4/5（canvas×2 + vscode×3）且中心距中位数 ≤20px。

**Architecture:** `build_marks` v2 双轨（a11y 数字红框 + 区域字母灰虚线框，**网格格整体废除**）→ `choose_action` v2（双轨 prompt + zoom 解析）→ `zoom_ground`（裁切→原生 bbox→映射）→ eval 与生产 computer_use 同步适配。详见 spec：`docs/superpowers/specs/2026-08-01-click-grounding-hierarchical-design.md`。

**Tech Stack:** Python（sidecar），pytest，PIL，GLM 视觉 API（手动评测）。

## Global Constraints

- sidecar 测试：`cd sidecar && uv run --extra dev pytest -q`（必须带 `--extra dev`）。
- 提交信息：中文 conventional commit，每任务一提交。
- 契约变更：`build_marks` 返回从 `(b64, marks)` 变 `(b64, marks, zones)`；网格（`_grid_cells`/`GRID_COLS`/`GRID_ROWS`/`GRID_TRIGGER`）删除——相关旧测试须重写，不许为保旧测试妥协新行为。
- 评测脚本手动跑非 CI；GLM 真实调用预算 ≤4 轮全量。
- 场景 JSON/PNG 与 eval_reports/ 不入库。
- 失败防失控原则：解析范围外一律 None；zoom 任一步失败返回 None。

---

### Task 1: build_marks v2 双轨（废网格）+ 区域渲染

**Files:**
- Modify: `sidecar/src/yibao_brain/grounding.py`（常量区、_grid_cells 删除、新增 _zones/_dashed_rect、build_marks、_render）
- Test: `sidecar/tests/test_grounding.py`（重写 2 个旧契约测试 + 新增 2 个）

**Interfaces:**
- Produces:
  - `ZONE_COLS, ZONE_ROWS = 3, 2`；`build_marks(shot_path, tree, scale=None) -> (b64|None, marks, zones)`
  - marks：数字轨 a11y 标记（结构不变，`id` 1-based 连续）
  - zones：`[{"letter": "A".."F", "rect": (x1,y1,x2,y2), "center": (cx,cy)}]`（逻辑坐标，行优先 A B C / D E F），恒常 6 个
  - 渲染：先画区域（灰 `(120,120,120)` 虚线框 + 框角大字母），后画 a11y（红实线框 + 框角数字，Slice 2A 样式不变）
  - 渲染失败返回 `(None, [], [])`

- [ ] **Step 0: 切分支**

```bash
cd /Users/denny/Work/yibao && git checkout -b feat/click-hierarchical
```

- [ ] **Step 1: 重写旧契约测试 + 新测试**

`test_build_marks_collects_a11y_and_fills_grid`（test_grounding.py:14-24）**整体替换**为：

```python
def test_build_marks_collects_a11y_and_zones(tmp_path):
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
    ]}
    b64, marks, zones = SoMGrounding().build_marks(_shot(tmp_path), tree, scale=1.0)
    assert b64 is not None and b64.startswith("data:image/")
    assert all(m["source"] == "a11y" for m in marks)  # 网格已废除，数字轨只有 a11y
    assert [z["letter"] for z in zones] == ["A", "B", "C", "D", "E", "F"]
    assert marks[0]["id"] == 1
    assert marks[0]["center"] == (20.0, 20.0)  # bbox 中心，逻辑坐标
```

`test_build_marks_enough_a11y_skips_grid`（:37-43）**整体替换**为：

```python
def test_zones_geometry_row_major(tmp_path):
    """区域恒常 6 个、3 列 2 行行优先；rect/center 为逻辑坐标。"""
    _, _, zones = SoMGrounding().build_marks(_shot(tmp_path, 300, 200), {}, scale=1.0)
    assert len(zones) == 6
    assert zones[0]["rect"] == (0.0, 0.0, 100.0, 100.0)
    assert zones[0]["center"] == (50.0, 50.0)
    assert zones[3]["rect"] == (0.0, 100.0, 100.0, 200.0)
    assert zones[5]["letter"] == "F" and zones[5]["center"] == (250.0, 150.0)
```

`test_build_marks_render_failure_returns_none`（:63-65）断言改 3-tuple：

```python
def test_build_marks_render_failure_returns_none(tmp_path):
    # 截图路径不存在 → 渲染失败 → (None, [], [])
    b64, marks, zones = SoMGrounding().build_marks("/nope/missing.png", {"role": "AXApp"}, scale=1.0)
    assert b64 is None and marks == [] and zones == []
```

其余既有测试（hidpi/caps_total/dedupe/outline_rows）若因 3-tuple 解包失败，同步改为 `_, marks, _ = ...build_marks(...)` 或 `_, marks, _zones = ...`，断言语义不动。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_grounding.py -q`
Expected: FAIL（build_marks 仍返回 2-tuple）

- [ ] **Step 3: 实现**

`grounding.py` 改动：

1. 模块 docstring 前两行改为：`Set-of-Marks 分层视觉 grounding：a11y 数字红框 + 区域字母灰框双轨；模型选元素直点，选区域裁切放大二次精化（zoom_ground）。坐标约定同前：marks/zones 存逻辑坐标，渲染叠加用物理像素。`
2. 删除 `GRID_TRIGGER`/`GRID_COLS, GRID_ROWS` 常量与 `_grid_cells` 函数，加：

```python
ZONE_COLS, ZONE_ROWS = 3, 2


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
    """PIL 无原生源线：四边按 dash/gap 分段画。"""
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
```

3. build_marks：

```python
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
```

4. `_render(self, shot_path, marks, zones, scale)`：开头加载与 font/lw 逻辑不变；**先**画区域：

```python
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
```

区域绘制之后再画 a11y 红框数字（现有循环原样）。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest tests/test_grounding.py -q && uv run --extra dev pytest -q`
Expected: PASS（skills_real.py 的 2-tuple 调用此时会红——属预期，Task 4 修；若红仅限该处，先提交本任务）

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/grounding.py sidecar/tests/test_grounding.py
git commit -m "feat(click): build_marks 双轨——a11y 数字红框 + 区域字母灰虚线框（废网格）"
```

---

### Task 2: choose_action v2（双轨 prompt + zoom 解析）

**Files:**
- Modify: `sidecar/src/yibao_brain/llm.py`（MARK_SYSTEM_PROMPT、choose_action、_parse_marked_action）
- Test: `sidecar/tests/test_llm.py`

**Interfaces:**
- Produces: `choose_action(marked_image_b64, task, n_marks, history=None, n_zones=0)`；`_parse_marked_action(content, n_marks, n_zones=0)`；zoom 动作形状 `{"action": "zoom", "zone": "A"}`（letter 限定 A..chr(64+n_zones)）。fakes.py 的 choose_action 签名同步加 `n_zones=0`（Task 4 用到）。

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_llm.py` 末尾追加：

```python
def test_parse_marked_action_zoom_letter():
    from yibao_brain.llm import ComputerUseClient

    assert ComputerUseClient._parse_marked_action("B", 5, 6) == {"action": "zoom", "zone": "B"}
    assert ComputerUseClient._parse_marked_action("B.", 5, 6) == {"action": "zoom", "zone": "B"}
    assert ComputerUseClient._parse_marked_action('{"action":"zoom","zone":"C"}', 5, 6) == {"action": "zoom", "zone": "C"}
    assert ComputerUseClient._parse_marked_action("G", 5, 6) is None   # 超出 A-F
    assert ComputerUseClient._parse_marked_action("B", 5, 0) is None   # 无区域轨
    assert ComputerUseClient._parse_marked_action("3", 5, 6) == {"action": "click", "mark": 3}
    assert ComputerUseClient._parse_marked_action('{"action":"zoom","zone":"Z"}', 5, 6) is None


def test_choose_action_prompt_mentions_zones():
    captured = {}

    class FakeResp:
        choices = [type("C", (), {"message": type("M", (), {"content": "B"})()})()]

    class FakeClient:
        def __init__(self, **kw):
            pass

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured.update(kw)
                    return FakeResp()

    c = ComputerUseClient(api_key="x", model="glm-4.6v-flash",
                          base_url="https://open.bigmodel.cn/api/paas/v4/",
                          client_factory=FakeClient)
    action = c.choose_action("data:image/jpeg;base64,x", "点按钮", 5, [], n_zones=6)
    assert action == {"action": "zoom", "zone": "B"}
    user_text = captured["messages"][-1]["content"][-1]["text"]
    assert "字母区域" in user_text and "A-F" in user_text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_llm.py -q -k "zoom or zones"`
Expected: FAIL

- [ ] **Step 3: 实现**

`llm.py` 三处：

1. MARK_SYSTEM_PROMPT 改为：

```python
    MARK_SYSTEM_PROMPT = (
        "你是桌面 GUI 操作助手。屏幕上红色数字框是可交互元素(1..N)，灰色字母框是区域(A..F)。"
        "根据用户任务给出【下一个动作】：目标在某个红框元素上就输出它的数字编号（一个整数）；"
        "目标不在任何红框元素上（如网页、画布等自绘内容），输出它所在的字母区域（一个字母）；"
        "需要输入文字时输出 JSON {\"action\":\"type\",\"text\":\"...\"}；"
        "任务完成时输出 finish。只输出整数编号或一个字母，不要任何其他文字。"
    )
```

2. choose_action 签名与 prompt 文本：

```python
    def choose_action(self, marked_image_b64: str, task: str, n_marks: int,
                      history: list | None = None, n_zones: int = 0):
        messages: list[dict] = [{"role": "system", "content": self.MARK_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        zone_hint = f"和 {n_zones} 个灰框字母区域(A-{chr(64 + n_zones)})" if n_zones else ""
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": marked_image_b64}},
                {"type": "text", "text": f"任务：{task}\n共有 {n_marks} 个红框数字标记(1-{n_marks}){zone_hint}。给出下一个动作。"},
            ],
        })
        resp = _vision_create_with_retry(lambda: self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=CHOOSE_TEMPERATURE,
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        return self._parse_marked_action(content, n_marks, n_zones)
```

3. _parse_marked_action 加 n_zones 与字母轨：

```python
    @staticmethod
    def _parse_marked_action(content: str, n_marks: int, n_zones: int = 0) -> dict | None:
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
                if obj.get("action") == "zoom":
                    zone = str(obj.get("zone") or "").upper()
                    if n_zones and len(zone) == 1 and "A" <= zone < chr(65 + n_zones):
                        return {"action": "zoom", "zone": zone}
                    return None
            except json.JSONDecodeError:
                pass
        if "finish" in s.lower():
            return {"action": "finish"}
        lm = re.fullmatch(r"([A-Za-z])\.?", s)
        if lm and n_zones:
            zone = lm.group(1).upper()
            if "A" <= zone < chr(65 + n_zones):
                return {"action": "zoom", "zone": zone}
            return None
        m2 = re.search(r"\d+", s)
        if m2:
            val = int(m2.group(0))
            if 1 <= val <= n_marks:
                return {"action": "click", "mark": val}
        return None
```

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest tests/test_llm.py -q && uv run --extra dev pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/llm.py sidecar/tests/test_llm.py
git commit -m "feat(click): choose_action 双轨——数字选元素/字母选区域，zoom 解析"
```

---

### Task 3: zoom_ground + resolve_point

**Files:**
- Modify: `sidecar/src/yibao_brain/grounding.py`（新增 zoom_ground、resolve_point，resolve 重构）
- Test: `sidecar/tests/test_grounding.py`

**Interfaces:**
- Produces:
  - `zoom_ground(client, shot_path, zone_rect, scale, target) -> (x, y) | None`（屏幕逻辑坐标；任一步失败 None）
  - `SoMGrounding.resolve_point(cx, cy, host) -> dict`（element_at AX-press 优先，失败坐标点击；返回 {"method","x","y"}）
  - `resolve(mark_id, marks, host)` 行为不变（内部改走 resolve_point）

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_grounding.py` 末尾追加：

```python
class _FakeZoomClient:
    def __init__(self, action):
        self._action = action
        self.calls = []

    def next_action(self, b64, task, history):
        self.calls.append((b64, task))
        return self._action


def test_zoom_ground_maps_crop_box_to_screen(tmp_path):
    from yibao_brain.grounding import zoom_ground

    shot = _shot(tmp_path, 400, 200)  # 物理 400x200，scale=1.0
    client = _FakeZoomClient({"action": "click", "box": [10, 10, 30, 30]})
    point = zoom_ground(client, shot, (100.0, 50.0, 200.0, 100.0), 1.0, "目标")
    assert point == (120.0, 70.0)  # crop 内中心 (20,20) + 区域原点 (100,50)
    assert client.calls and client.calls[0][1] == "目标"


def test_zoom_ground_scales_physical_back_to_logical(tmp_path):
    from yibao_brain.grounding import zoom_ground

    shot = _shot(tmp_path, 400, 200)  # scale=2.0 → 逻辑 200x100
    client = _FakeZoomClient({"action": "click", "box": [20, 20, 60, 60]})
    point = zoom_ground(client, shot, (0.0, 0.0, 100.0, 50.0), 2.0, "t")
    assert point == (20.0, 20.0)  # crop 内物理中心 (40,40) / 2


def test_zoom_ground_failures_return_none(tmp_path):
    from yibao_brain.grounding import zoom_ground

    shot = _shot(tmp_path)
    assert zoom_ground(_FakeZoomClient({"action": "finish"}), shot, (0, 0, 50, 50), 1.0, "t") is None
    assert zoom_ground(_FakeZoomClient({"action": "click"}), shot, (0, 0, 50, 50), 1.0, "t") is None
    assert zoom_ground(_FakeZoomClient(None), shot, (0, 0, 50, 50), 1.0, "t") is None
    assert zoom_ground(_FakeZoomClient({"action": "click", "box": [0, 0, 1, 1]}),
                       "/nope/missing.png", (0, 0, 50, 50), 1.0, "t") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_grounding.py -q -k zoom`
Expected: FAIL（ImportError）

- [ ] **Step 3: 实现**

`grounding.py` 在 `_dashed_rect` 之后加：

```python
def zoom_ground(client, shot_path: str, zone_rect, scale: float, target: str):
    """Stage 2 区域放大：裁切区域 → client.next_action 原生 bbox → 映射回屏幕逻辑坐标。
    任一步失败（裁切/无 box/非 click）返回 None。"""
    try:
        from PIL import Image
        zx1, zy1 = float(zone_rect[0]), float(zone_rect[1])
        with Image.open(shot_path) as _raw:
            im = _raw.convert("RGB")
            box = [int(round(float(v) * scale)) for v in zone_rect]
            box[0], box[1] = max(0, box[0]), max(0, box[1])
            box[2], box[3] = min(im.width, box[2]), min(im.height, box[3])
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                return None
            buf = io.BytesIO()
            im.crop(box).save(buf, format="PNG")
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        action = client.next_action(b64, target, [])
        if not action or action.get("action") != "click":
            return None
        bbox = action.get("box") or []
        if len(bbox) != 4:
            return None
        x1, y1, x2, y2 = (float(v) for v in bbox)
        return ((x1 + x2) / 2 / scale + zx1, (y1 + y2) / 2 / scale + zy1)
    except Exception:
        return None
```

`resolve` 重构 + 新增 `resolve_point`（行为不变）：

```python
    def resolve(self, mark_id, marks, host) -> dict:
        """mark_id(1-based) → 逻辑中心 → resolve_point。非法 → miss。不动作。"""
        center = self.predict(mark_id, marks)
        if center is None:
            return {"method": "miss"}
        return self.resolve_point(center[0], center[1], host)

    def resolve_point(self, cx, cy, host) -> dict:
        """逻辑坐标点 → element_at 取 handle 做 AX-press（确定性），失败回退坐标点击。"""
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

（resolve 原 docstring 语义并入；predict 不动。）

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest tests/test_grounding.py -q && uv run --extra dev pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/grounding.py sidecar/tests/test_grounding.py
git commit -m "feat(click): zoom_ground 区域放大（裁切→原生 bbox→映射）+ resolve_point 抽取"
```

---

### Task 4: 生产与 eval 适配（3-tuple + zoom 循环）

**Files:**
- Modify: `sidecar/src/yibao_brain/skills_real.py`（:281 调用点、_apply_marked、import）
- Modify: `sidecar/scripts/eval_click.py`（run_som）
- Modify: `sidecar/tests/fakes.py`（choose_action 签名加 n_zones）
- Test: `sidecar/tests/test_real_skills.py`

**Interfaces:**
- Consumes: Task 1 的 3-tuple、Task 2 的 zoom 动作、Task 3 的 zoom_ground/resolve_point。
- Produces: 生产 computer_use SoM 路径处理 zoom（同帧裁切精化后 resolve_point 点击，交互租约在 _apply_marked 前已查）；eval run_som 输出 zoom 精化点。

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_real_skills.py` 末尾追加：

```python
def test_computer_use_zoom_action_grounds_and_clicks(tmp_path, monkeypatch):
    """SoM 路径模型选字母区域 → zoom_ground 精化 → resolve_point 点击。"""
    from yibao_brain import skills_real
    from yibao_brain.skills_real import ComputerUseSkill

    class FakeClient:
        prefers_raw_bbox = False

        def choose_action(self, marked, task, n_marks, history=None, n_zones=0):
            return {"action": "zoom", "zone": "A"}

    monkeypatch.setattr(skills_real, "zoom_ground", lambda client, shot, rect, scale, task: (50.0, 60.0))
    host = FakeHost()  # element_at 返回 None → 走坐标点击
    shot = tmp_path / "s.png"
    Image.new("RGB", (100, 80), "white").save(shot)
    host.screenshotter = type("S", (), {"capture": lambda self: str(shot)})()
    host.a11y.frontmost_tree = lambda: {"role": "AXApp", "children": []}
    ctx = SkillContext(host=host)
    skill = ComputerUseSkill(FakeClient())
    r = skill.run({"task": "点按钮", "app": "x"}, ctx)
    assert r.success, r.error
    assert host.input.clicks == [(50.0, 60.0)]
```

（若 FakeHost 的记录属性名不同，按 fakes.py 实际结构对齐；其余 3-tuple 解包适配同步改。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_real_skills.py -q -k zoom`
Expected: FAIL（zoom 动作未被处理 / 3-tuple 解包错误）

- [ ] **Step 3: 实现**

`skills_real.py`：

1. import 行加 zoom_ground：`from .grounding import SoMGrounding, _physical_scale, zoom_ground`
2. SoM 调用点（:281-296）：

```python
                tree = ctx.host.a11y.frontmost_tree()
                marked, marks, zones = self._som.build_marks(shot, tree, scale)
                if marked is None:
                    action = self._raw_bbox_step(shot, task, history, ctx.host, scale)  # 回退
                else:
                    action = self._client.choose_action(marked, task, len(marks), history,
                                                        n_zones=len(zones))
                    if cancelled():
                        return ActionResult(success=False, error="操作已中断")
                    if action is None:
                        break  # 模型输出非法 → 停，防失控
                    if action.get("action") == "finish":
                        break
                    allowed, reason = _permit_interaction(ctx, interaction_lease)
                    if not allowed:
                        action = {"action": "interrupted", "reason": reason}
                    else:
                        self._apply_marked(action, marks, ctx.host,
                                           zones=zones, shot=shot, scale=scale, task=task)
```

3. `_apply_marked`：

```python
    def _apply_marked(self, action, marks, host, *, zones=(), shot=None, scale=1.0, task=""):
        kind = action.get("action")
        if kind == "click":
            self._som.resolve(action.get("mark"), marks, host)
        elif kind == "type":
            host.input.type_text(str(action.get("text", "")))
        elif kind == "zoom" and shot is not None:
            zone = next((z for z in zones if z["letter"] == action.get("zone")), None)
            if zone is None:
                return
            point = zoom_ground(self._client, shot, zone["rect"], scale, task)
            if point is not None:
                self._som.resolve_point(point[0], point[1], host)
```

4. `fakes.py` 的 choose_action 签名加 `n_zones=0`（:129 附近）。

`eval_click.py` 的 run_som 改为：

```python
def run_som(client, som, sc, scale):
    marked, marks, zones = som.build_marks(sc["screenshot"], sc.get("tree") or {}, scale)
    if not marked:
        return None
    action = client.choose_action(marked, sc["target"], len(marks), [], n_zones=len(zones))
    if not action:
        return None
    if action.get("action") == "zoom":
        from yibao_brain.grounding import zoom_ground
        zone = next((z for z in zones if z["letter"] == action.get("zone")), None)
        if zone is None:
            return None
        return zoom_ground(client, sc["screenshot"], zone["rect"], scale, sc["target"])
    if action.get("action") != "click":
        return None
    return som.predict(action.get("mark"), marks)
```

5. 全仓搜索 `build_marks(` 与 `choose_action(` 残留的 2-tuple/旧签名调用点（含 tests），全部对齐新契约。

- [ ] **Step 4: 跑测试确认通过 + 全量**

Run: `cd sidecar && uv run --extra dev pytest tests/test_real_skills.py -q && uv run --extra dev pytest -q`
Expected: PASS（全量绿，无 2-tuple 残留）

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/skills_real.py sidecar/scripts/eval_click.py sidecar/tests/
git commit -m "feat(click): computer_use/eval 接双轨——zoom 动作同帧裁切精化后 resolve_point"
```

---

### Task 5: 复测对照 + 达标判定（手动评测，真实 API）

**Files:**
- Modify: `docs/reports/2026-08-01-v1.1-slice1-baseline.md`（追加 §8）

- [ ] **Step 1: 复测轮 1（当前模型 glm-4.1v-thinking-flashx）**

```bash
cd sidecar && uv run python scripts/eval_click.py --scenarios scripts/eval_scenarios 2>&1 | tee /Users/denny/Work/yibao/.superpowers/sdd/slice2a2-eval-round1.txt
```

- [ ] **Step 2: 复测轮 2（对照 glm-4.6v-flash）**

```bash
cd sidecar && uv run python scripts/eval_click.py --scenarios scripts/eval_scenarios --model glm-4.6v-flash 2>&1 | tee /Users/denny/Work/yibao/.superpowers/sdd/slice2a2-eval-round2.txt
```

- [ ] **Step 3: 达标判定（spec §3）**

- 可及桶 7/7（calc×3 + sysset×2 + safari×2）；盲桶 ≥4/5 且中心距中位数 ≤20px
- 某轮双绿 → 该模型建议配置；两轮双红 → **停止不跑第三轮**，数据报用户（spec 回退线）
- zoom 精化点若明显落在正确区域但偏离目标（>20px），逐场景记录是 stage1 选错还是 stage2 偏了（用 scripts/debug_som.py 取证，API 预算内）

- [ ] **Step 4: 报告回写**

`docs/reports/2026-08-01-v1.1-slice1-baseline.md` 末尾追加 `## 8. 2A′ 分层两阶段复测对照`：两轮逐场景表 + 两桶判定 + stage1/stage2 失败归因 + 模型建议。

- [ ] **Step 5: Commit**

```bash
git add docs/reports/2026-08-01-v1.1-slice1-baseline.md
git commit -m "docs(reports): 2A′ 分层两阶段复测对照与达标判定"
```

---

## Self-Review 记录

- **Spec 覆盖**：spec §2 双轨 → Task 1/2；§3 zoom/resolve_point → Task 3；§4 生产+eval 适配 → Task 4；§4 验证与回退线 → Task 5。无遗漏。
- **占位符扫描**：无 TBD；所有代码步骤含完整代码（Task 4 Step 1 测试注明按 fakes.py 实际属性名对齐——这是有意的适配点而非占位）。
- **类型一致性**：`(b64, marks, zones)` 3-tuple 贯穿 grounding/llm/skills_real/eval/tests；`{"action":"zoom","zone":letter}` 在 llm 解析 ↔ skills_real._apply_marked ↔ eval run_som 三处一致；zoom_ground 签名五处一致。
- **风险**：Task 1 Step 4 已声明 skills_real 2-tuple 调用会先红（Task 4 修）——任务间中间态不破 main（分支上）。
