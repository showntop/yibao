import os

from yibao_brain.mac import host_mac
from yibao_brain.mac.host_mac import _select_window


def test_select_window_matches_visible_normal_window_by_owner_or_title():
    windows = [
        {"owner": "译宝", "title": "", "layer": 0, "bounds": (900, 200, 420, 500)},
        {"owner": "Calculator", "title": "计算器", "layer": 0,
         "bounds": (72, 354, 198, 350)},
        {"owner": "Calculator", "title": "悬浮提示", "layer": 3,
         "bounds": (80, 360, 50, 30)},
    ]

    by_title = _select_window(windows, "计算器")
    by_owner = _select_window(windows, "Calculator")

    assert by_title == windows[1]
    assert by_owner == windows[1]
    assert _select_window(windows, "Safari") is None


def test_capture_region_grabs_exact_rect(monkeypatch, tmp_path):
    """capture_region：region 原样传给 mss.grab，落盘 PNG 返回路径。"""
    calls = {}

    class FakeRaw:
        size = (10, 20)
        bgra = b"\x00" * 10 * 20 * 4

    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, region):
            calls["region"] = region
            return FakeRaw()

    monkeypatch.setattr(host_mac.mss, "mss", lambda: FakeSct())
    s = host_mac.MacScreenshotter(dir_=str(tmp_path))
    path = s.capture_region(5, 6, 10, 20)
    assert calls["region"] == {"left": 5, "top": 6, "width": 10, "height": 20}
    assert path.endswith(".png") and os.path.exists(path)


def test_capture_region_clamps_tiny_size(monkeypatch, tmp_path):
    """宽高超小（拖拽抖动）钳到 1px，不炸。"""
    calls = {}

    class FakeRaw:
        size = (1, 1)
        bgra = b"\x00" * 4

    class FakeSct:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, region):
            calls["region"] = region
            return FakeRaw()

    monkeypatch.setattr(host_mac.mss, "mss", lambda: FakeSct())
    s = host_mac.MacScreenshotter(dir_=str(tmp_path))
    s.capture_region(0, 0, 0, 0)
    assert calls["region"]["width"] == 1 and calls["region"]["height"] == 1
