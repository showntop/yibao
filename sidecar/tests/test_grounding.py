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
