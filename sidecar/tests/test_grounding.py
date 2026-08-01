"""SoMGrounding 单测：标记生成/网格补齐/封顶/坐标系/渲染。"""
from PIL import Image

from yibao_brain.grounding import SoMGrounding, MAX_MARKS
from fakes import FakeHost, _FakeHandle


def _shot(tmp_path, w=100, h=100):
    p = tmp_path / "shot.png"
    Image.new("RGB", (w, h), "white").save(p)
    return str(p)


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


def test_build_marks_stores_logical_coords_under_hidpi(tmp_path):
    # 物理图 200px、scale 2.0；a11y bbox 是逻辑坐标 → marks 仍存逻辑
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
    ]}
    _, marks, _ = SoMGrounding().build_marks(_shot(tmp_path, 200, 200), tree, scale=2.0)
    a11y = [m for m in marks if m["source"] == "a11y"][0]
    assert a11y["center"] == (20.0, 20.0)


def test_zones_geometry_row_major(tmp_path):
    """区域恒常 6 个、3 列 2 行行优先；rect/center 为逻辑坐标。"""
    _, _, zones = SoMGrounding().build_marks(_shot(tmp_path, 300, 200), {}, scale=1.0)
    assert len(zones) == 6
    assert zones[0]["rect"] == (0.0, 0.0, 100.0, 100.0)
    assert zones[0]["center"] == (50.0, 50.0)
    assert zones[3]["rect"] == (0.0, 100.0, 100.0, 200.0)
    assert zones[5]["letter"] == "F" and zones[5]["center"] == (250.0, 150.0)


def test_build_marks_caps_total(tmp_path):
    nodes = [{"role": "AXButton", "bbox": [i * 3, 0, i * 3 + 2, 2], "children": []}
             for i in range(MAX_MARKS + 5)]
    tree = {"role": "AXApp", "children": nodes}
    _, marks, _ = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    assert len(marks) <= MAX_MARKS


def test_build_marks_dedupes_overlap(tmp_path):
    tree = {"role": "AXApp", "children": [
        {"role": "AXButton", "bbox": [10, 10, 30, 30], "children": []},
        {"role": "AXButton", "bbox": [11, 11, 31, 31], "children": []},  # IoU>0.8 → 合并
    ]}
    _, marks, _ = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    assert len([m for m in marks if m["source"] == "a11y"]) == 1


def test_build_marks_render_failure_returns_none(tmp_path):
    # 截图路径不存在 → 渲染失败 → (None, [], [])
    b64, marks, zones = SoMGrounding().build_marks("/nope/missing.png", {"role": "AXApp"}, scale=1.0)
    assert b64 is None and marks == [] and zones == []


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
    _, marks, _ = SoMGrounding().build_marks(_shot(tmp_path, 400, 400), tree, scale=1.0)
    rows = [m for m in marks if m["source"] == "a11y" and m["rect"] == (0.0, 100.0, 215.0, 128.0)]
    assert len(rows) == 1


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
