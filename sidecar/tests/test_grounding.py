"""SoMGrounding 单测：标记生成/网格补齐/封顶/坐标系/渲染。"""
from PIL import Image

from yibao_brain.grounding import SoMGrounding, MAX_MARKS
from fakes import FakeHost, _FakeHandle


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


def test_build_marks_rich_a11y_full_cover_drops_grid(tmp_path):
    """a11y 铺满全图（每格覆盖≥0.5）→ 无网格兜底（不产生冗余标记）。"""
    nodes = []
    for r in range(4):
        for c in range(3):
            x1, y1 = c * 34, r * 25
            nodes.append({"role": "AXButton", "bbox": [x1, y1, x1 + 34, y1 + 25], "children": []})
    tree = {"role": "AXApp", "children": nodes}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path), tree, scale=1.0)
    assert all(m["source"] == "a11y" for m in marks)


def test_build_marks_rich_a11y_keeps_blind_grid(tmp_path):
    """a11y 密集但只铺满顶行（≥8 个）→ 盲区格子仍兜底（新契约核心）。"""
    nodes = [{"role": "AXLink", "bbox": [i * 10, 0, i * 10 + 10, 13], "children": []}
             for i in range(10)]  # 10 个铺满顶行（x 0..100, y 0..13）
    tree = {"role": "AXApp", "children": nodes}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path), tree, scale=1.0)
    sources = {m["source"] for m in marks}
    assert "a11y" in sources and "grid" in sources  # 旧契约（≥8 不叠网格）已废除
    grid = [m for m in marks if m["source"] == "grid"]
    assert all(m["center"][1] > 12.5 for m in grid)  # 含 a11y 的顶行格不兜底


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


def test_build_marks_collects_outline_rows(tmp_path):
    """AXOutline 的 AXRow（系统设置侧边栏行实证角色）应入标记。"""
    tree = {"role": "AXApp", "children": [
        {"role": "AXOutline", "bbox": [0, 0, 200, 400], "children": [
            {"role": "AXRow", "bbox": [0, 100, 215, 128], "children": [
                {"role": "AXCell", "bbox": [10, 100, 195, 128], "children": []},
            ]},
        ]},
    ]}
    _, marks = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    rows = [m for m in marks if m["source"] == "a11y" and m["rect"] == (0.0, 100.0, 215.0, 128.0)]
    assert len(rows) == 1


def test_covered_ratio_full_none_partial():
    from yibao_brain.grounding import _covered_ratio

    cell = (0.0, 0.0, 100.0, 100.0)
    assert _covered_ratio(cell, [(0.0, 0.0, 100.0, 100.0)]) == 1.0
    assert _covered_ratio(cell, [(200.0, 200.0, 300.0, 300.0)]) == 0.0
    assert _covered_ratio(cell, [(0.0, 0.0, 50.0, 100.0)]) == 0.5
    assert _covered_ratio((0.0, 0.0, 0.0, 0.0), []) == 1.0  # 零面积格视为已覆盖
