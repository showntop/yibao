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
