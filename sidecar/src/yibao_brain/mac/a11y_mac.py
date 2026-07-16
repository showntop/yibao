"""macOS a11y 实现：pyobjc 调 HIServices 的 AX C API。

关键坑（pyobjc 官方承认）：AXValueGetValue 未被包装，这里用 ctypes 手动补，
才能把 kAXPositionAttribute / kAXSizeAttribute 的 AXValueRef 解成 CGPoint/CGSize。

handle = AXUIElementRef（不透明）。仅在 macOS + pyobjc 已装时可用，
故 build_loop 用延迟 import。
"""
from __future__ import annotations

import ctypes
import subprocess
import time

import objc
from AppKit import NSWorkspace
from ApplicationServices import (
    AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyElementAtPosition,
    AXUIElementCreateApplication,
    AXUIElementCreateSystemWide,
    AXUIElementPerformAction,
    AXUIElementSetAttributeValue,
    AXUIElementSetMessagingTimeout,
    AXValueGetTypeID,
    kAXChildrenAttribute,
    kAXEnabledAttribute,
    kAXFocusedAttribute,
    kAXPickAction,
    kAXPositionAttribute,
    kAXPressAction,
    kAXRoleAttribute,
    kAXSizeAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXValueCGPointType,
    kAXValueCGSizeType,
    kAXErrorSuccess,
)
from CoreFoundation import CFGetTypeID

# ---- ctypes 补 AXValueGetValue（pyobjc 未包装）----
_lib = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
)
_lib.AXValueGetValue.restype = ctypes.c_bool
_lib.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("w", ctypes.c_double), ("h", ctypes.c_double)]


def _is_axvalue(v) -> bool:
    try:
        return v is not None and CFGetTypeID(v) == AXValueGetTypeID()
    except Exception:
        return False


def _ax_point(value):
    if not _is_axvalue(value):
        return None
    p = _CGPoint()
    if _lib.AXValueGetValue(objc.pyobjc_id(value), kAXValueCGPointType, ctypes.byref(p)):
        return (p.x, p.y)
    return None


def _ax_size(value):
    if not _is_axvalue(value):
        return None
    s = _CGSize()
    if _lib.AXValueGetValue(objc.pyobjc_id(value), kAXValueCGSizeType, ctypes.byref(s)):
        return (s.w, s.h)
    return None


def _get_attr(el, attr):
    err, val = AXUIElementCopyAttributeValue(el, attr, None)
    if err != kAXErrorSuccess:
        return None
    return val


def _get_children(el):
    v = _get_attr(el, kAXChildrenAttribute)
    return list(v) if v else []


def _summarize(el) -> dict:
    pos = _ax_point(_get_attr(el, kAXPositionAttribute))
    size = _ax_size(_get_attr(el, kAXSizeAttribute))
    return {
        "title": _get_attr(el, kAXTitleAttribute),
        "role": _get_attr(el, kAXRoleAttribute),
        "value": _get_attr(el, kAXValueAttribute),
        "enabled": _get_attr(el, kAXEnabledAttribute),
        "focused": _get_attr(el, kAXFocusedAttribute),
        "position": pos,
        "size": size,
        "bbox": (pos[0], pos[1], pos[0] + size[0], pos[1] + size[1]) if pos and size else None,
    }


def _frontmost_element():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    pid = app.processIdentifier()
    el = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(el, 1.5)  # 防无响应 app 卡死 agent
    return el, pid


class MacA11yReader:
    """A11yReader Protocol 的 macOS 实现。handle 即 AXUIElementRef。"""

    def frontmost_tree(self, max_depth: int = 8) -> dict:
        el, _ = _frontmost_element()
        return self._walk(el, 0, max_depth)

    def _walk(self, el, depth, max_depth, max_children=200):
        node = _summarize(el)
        if depth < max_depth:
            node["children"] = [
                self._walk(c, depth + 1, max_depth, max_children)
                for c in _get_children(el)[:max_children]
            ]
        return node

    def find(self, role: str | None = None, title: str | None = None):
        el, _ = _frontmost_element()
        return self._find_in(el, role, title, 0, 8)

    def _find_in(self, el, role, title, depth, max_depth):
        node = _summarize(el)
        role_ok = role is None or node["role"] == role
        title_ok = title is None or (node["title"] and title in str(node["title"]))
        if role_ok and title_ok:
            return el
        if depth < max_depth:
            for c in _get_children(el):
                found = self._find_in(c, role, title, depth + 1, max_depth)
                if found is not None:
                    return found
        return None

    def bbox(self, handle):
        pos = _ax_point(_get_attr(handle, kAXPositionAttribute))
        size = _ax_size(_get_attr(handle, kAXSizeAttribute))
        if pos and size:
            return (pos[0], pos[1], size[0], size[1])  # x, y, w, h
        return None

    def press(self, handle) -> bool:
        err, actions = AXUIElementCopyActionNames(handle, None)
        acts = list(actions) if err == kAXErrorSuccess and actions else []
        if kAXPressAction in acts:
            return AXUIElementPerformAction(handle, kAXPressAction) == kAXErrorSuccess
        if kAXPickAction in acts:
            return AXUIElementPerformAction(handle, kAXPickAction) == kAXErrorSuccess
        return False

    def set_value(self, handle, text: str) -> bool:
        return AXUIElementSetAttributeValue(handle, kAXValueAttribute, text) == kAXErrorSuccess

    def element_at(self, x: float, y: float):
        sys_el = AXUIElementCreateSystemWide()
        err, hit = AXUIElementCopyElementAtPosition(sys_el, float(x), float(y), None)
        return hit if err == kAXErrorSuccess else None

    def launch_app(self, app: str):
        ws = NSWorkspace.sharedWorkspace()
        before = {a.processIdentifier() for a in ws.runningApplications()}
        subprocess.run(["open", "-a", app], check=False)
        time.sleep(1.0)  # 等进程起来
        # 优先取「新启动」的进程 pid（不依赖本地化名）
        for a in ws.runningApplications():
            if a.processIdentifier() not in before:
                return a.processIdentifier()
        # 兜底：名字模糊匹配
        for a in ws.runningApplications():
            name = a.localizedName() or ""
            if app.lower() in name.lower() or name.lower() in app.lower():
                return a.processIdentifier()
        return None
