"""macOS Host：组合 a11y + mss 截图 + pyautogui/pyperclip 输入。仅在 macOS 用。"""
from __future__ import annotations

import os
import time

import mss
import pyautogui
import pyperclip
from PIL import Image

from .a11y_mac import MacA11yReader

pyautogui.FAILSAFE = False  # agent 场景关掉角落熔断


def _select_window(windows: list[dict], query: str) -> dict | None:
    needle = str(query or "").strip().casefold()
    if not needle:
        return None
    matches: list[tuple[int, float, dict]] = []
    for window in windows:
        if int(window.get("layer", 0)) != 0:
            continue
        bounds = window.get("bounds") or ()
        if len(bounds) != 4:
            continue
        _x, _y, width, height = (float(v) for v in bounds)
        if width < 80 or height < 80:
            continue
        owner = str(window.get("owner") or "").strip().casefold()
        title = str(window.get("title") or "").strip().casefold()
        if needle == title:
            score = 4
        elif needle == owner:
            score = 3
        elif needle in title or title and title in needle:
            score = 2
        elif needle in owner or owner and owner in needle:
            score = 1
        else:
            continue
        matches.append((score, width * height, window))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1]))[2]


def _visible_windows() -> list[dict]:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowBounds,
        kCGWindowLayer,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionOnScreenOnly,
        kCGWindowName,
        kCGWindowOwnerName,
    )

    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    rows = CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []
    windows = []
    for row in rows:
        bounds = row.get(kCGWindowBounds) or {}
        try:
            rect = (
                float(bounds["X"]),
                float(bounds["Y"]),
                float(bounds["Width"]),
                float(bounds["Height"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        windows.append({
            "owner": row.get(kCGWindowOwnerName) or "",
            "title": row.get(kCGWindowName) or "",
            "layer": int(row.get(kCGWindowLayer) or 0),
            "bounds": rect,
        })
    return windows


class MacScreenshotter:
    def __init__(self, dir_: str = "/tmp") -> None:
        self.dir = dir_

    def capture(self) -> str:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"yibao-{time.time_ns()}.png")
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])  # 虚拟桌面（所有显示器并集）
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(path)
        return path

    def capture_window(self, app: str):
        """按明确应用/窗口名裁剪；匹配不到返回 None，调用方必须停止而非退整屏。"""
        window = _select_window(_visible_windows(), app)
        if window is None:
            return None
        left, top, logical_w, logical_h = window["bounds"]
        region = {
            "left": round(left),
            "top": round(top),
            "width": max(1, round(logical_w)),
            "height": max(1, round(logical_h)),
        }
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"yibao-window-{time.time_ns()}.png")
        with mss.mss() as sct:
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(path)
        scale = raw.size.width / logical_w if logical_w else 1.0
        return path, (left, top), scale


class MacInputInjector:
    def click(self, x: float, y: float) -> None:
        pyautogui.click(x, y)

    def type_text(self, text: str) -> None:
        if text.isascii():
            pyautogui.write(text, interval=0.01)
        else:
            # 中文等非 ASCII：走剪贴板粘贴（pyautogui.write 只支持可见 ASCII）
            pyperclip.copy(text)
            pyautogui.hotkey("command", "v")


class MacHost:
    """Host Protocol 的 macOS 实现。"""

    def __init__(self, screenshot_dir: str = "/tmp") -> None:
        self.screenshotter = MacScreenshotter(screenshot_dir)
        self.a11y = MacA11yReader()
        self.input = MacInputInjector()
