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


class MacScreenshotter:
    def __init__(self, dir_: str = "/tmp") -> None:
        self.dir = dir_

    def capture(self) -> str:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"yibao-{int(time.time())}.png")
        with mss.mss() as sct:
            raw = sct.grab(sct.monitors[0])  # 主屏
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            img.save(path)
        return path


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
