"""macOS 权限检查/引导：辅助功能、屏幕录制、输入监控。

这些权限都按「二进制」授权（TCC.db），换构建产物要重授。
AXIsProcessTrustedWithOptions 仅弹一次引导、不能强制授权；
CGPreflightScreenCaptureAccess 需 Team ID 签名二进制才准，dev 期可能恒 false
（用「抓图判黑」启发式兜底）。
"""
from __future__ import annotations

import sys


def check_ax() -> bool:
    if sys.platform != "darwin":
        return True
    from ApplicationServices import AXIsProcessTrusted

    return bool(AXIsProcessTrusted())


def prompt_ax() -> bool:
    """触发系统授权引导弹窗（异步，不影响返回值）。返回当前是否已授权。"""
    if sys.platform != "darwin":
        return True
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))


def check_screen() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGPreflightScreenCaptureAccess

        return bool(CGPreflightScreenCaptureAccess())
    except Exception:
        return True  # 无法判定时乐观（dev 期可能恒 false）


def prompt_screen() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGRequestScreenCaptureAccess

        return bool(CGRequestScreenCaptureAccess())
    except Exception:
        return False


def check_input() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGPreflightListenEventAccess

        return bool(CGPreflightListenEventAccess())
    except Exception:
        return False


def prompt_input() -> bool:
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import CGRequestListenEventAccess

        return bool(CGRequestListenEventAccess())
    except Exception:
        return False


def ensure_permissions() -> dict:
    """启动时调用：返回各权限状态，未授权的弹一次引导。"""
    ax = check_ax()
    if not ax:
        prompt_ax()
    screen = check_screen()
    if not screen:
        prompt_screen()
    input_ok = check_input()
    return {"ax": ax, "screen": screen, "input": input_ok}
