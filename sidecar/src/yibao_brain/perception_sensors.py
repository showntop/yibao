"""本机感知 v1 的传感器域：macOS 窗口/AX 采样、B 源截图帧处理与感知轮询协调器。

R-32c 从 perception.py 拆出（2026-08-22）：传感器与编排分离。perception.py 保留
技能/时间线纯函数并 re-export 本域符号（server/测试的
`from .perception import PerceptionSensors` 路径不变；测试 patch
`perception_sensors._ax_frontmost` 等以模拟前台读取）。
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections.abc import Callable

from .log import log
from .perception_store import PerceptionStore


SCREEN_HEARTBEAT_SECONDS = 300.0
SCREEN_DAILY_EVENT_CAP = 120
SCREEN_DAILY_VISION_CAP = 30
_BUILTIN_BLACKLIST = frozenset({
    "com.1password.1password", "com.apple.keychainaccess",
})
_PRIVATE_TITLE_MARKERS = ("无痕", "隐私浏览", "incognito", "inprivate", "private browsing")
_BROWSER_BUNDLES = frozenset({
    "com.google.Chrome", "com.apple.Safari", "com.microsoft.edgemac",
    "company.thebrowser.Browser",
})
_SENSITIVE_RES = (
    re.compile(r"\b\d{15,19}\b"),                     # 卡号
    re.compile(r"\b\d{17}[\dXx]\b"),                  # 身份证
    re.compile(r"(?i)(password|passwd|密码)[:：=]\s*\S+"),
)


def _sensitive_text(text: str) -> bool:
    text = text or ""
    if any(p.search(text) for p in _SENSITIVE_RES):
        return True
    # 卡号/身份证常按 4 位一组带空格或短横分隔；去掉数字间分隔符后再判一次
    compact = re.sub(r"(?<=\d)[\s-]+(?=\d)", "", text)
    return compact != text and any(p.search(compact) for p in _SENSITIVE_RES[:2])


def serialize_tree_text(tree: dict, max_chars: int = 4096) -> str:
    """a11y 树 → 紧凑文本（B 源存储用）：DFS 缩进行 role: 标题或值。
    预算：300 行 / 每父 50 子 / 缩进 ≤8 层 / 单值 ≤80 字 / 总 ≤max_chars。空→""。"""
    lines: list[str] = []

    def walk(node: dict, depth: int) -> None:
        if len(lines) >= 300 or not isinstance(node, dict):
            return
        role = str(node.get("role") or "")
        label = str(node.get("title") or node.get("value") or "").strip()
        if role and label:
            lines.append("  " * min(depth, 8) + f"{role}: {label[:80]}")
        for child in (node.get("children") or [])[:50]:
            walk(child, depth + 1)

    walk(tree, 0)
    text = "\n".join(lines)
    return text[:max_chars] + ("…" if len(text) > max_chars else "") if text else ""


def _window_snapshot() -> list[dict]:
    """实时窗口层级快照；每次调用直读 WindowServer，不依赖 Cocoa 通知缓存。"""
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListExcludeDesktopElements,
        kCGWindowListOptionOnScreenOnly,
    )

    return list(
        CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )
        or []
    )


def _localized_app_name(pid: int, fallback: str) -> str:
    """按 pid 补本地化应用名；失败时保留 WindowServer owner name。"""
    try:
        from AppKit import NSRunningApplication

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            return str(app.localizedName() or app.bundleIdentifier() or fallback)
    except Exception:
        pass
    return fallback or "未知应用"


def _bundle_id_for_pid(pid: int) -> str:
    """Resolve a stable macOS bundle identity for a running process."""
    try:
        from AppKit import NSRunningApplication

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is not None:
            return str(app.bundleIdentifier() or "")
    except Exception:
        pass
    return ""


def _ax_frontmost() -> tuple[int, str] | None:
    """从系统级 AX 焦点读取真实前台 pid/title；不依赖 NSWorkspace 通知缓存。"""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            AXUIElementGetPid,
            AXUIElementSetMessagingTimeout,
            kAXErrorSuccess,
            kAXFocusedApplicationAttribute,
            kAXFocusedWindowAttribute,
            kAXTitleAttribute,
        )

        system = AXUIElementCreateSystemWide()
        err, app = AXUIElementCopyAttributeValue(system, kAXFocusedApplicationAttribute, None)
        if err != kAXErrorSuccess or app is None:
            return None
        err, pid = AXUIElementGetPid(app, None)
        if err != kAXErrorSuccess or int(pid) <= 0:
            return None
        AXUIElementSetMessagingTimeout(app, 0.5)
        title = ""
        err, window = AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute, None)
        if err == kAXErrorSuccess and window is not None:
            err, value = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
            if err == kAXErrorSuccess and value is not None:
                title = str(value)
        return int(pid), title
    except Exception:
        return None


def _ax_title_for_pid(pid: int) -> str:
    """读取该应用的聚焦窗口标题；AX 不可用/超时返回空。"""
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateApplication,
            AXUIElementSetMessagingTimeout,
            kAXErrorSuccess,
            kAXFocusedWindowAttribute,
            kAXTitleAttribute,
        )

        element = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(element, 0.5)
        err, window = AXUIElementCopyAttributeValue(element, kAXFocusedWindowAttribute, None)
        if err == kAXErrorSuccess and window is not None:
            err, value = AXUIElementCopyAttributeValue(window, kAXTitleAttribute, None)
            if err == kAXErrorSuccess and value is not None:
                return str(value)
    except Exception:
        pass
    return ""


def sample_frontmost_details() -> tuple[str, str, str] | None:
    """Return the live app name, bundle id and title from one foreground read."""
    if sys.platform != "darwin":
        return None

    # A 源本来就依赖辅助功能授权。系统级 focusedApplication 每次直读焦点，
    # 不会像后台线程里的 NSWorkspace.frontmostApplication 一样停在启动快照。
    focused = _ax_frontmost()
    if focused is not None:
        pid, title = focused
        return _localized_app_name(pid, "未知应用"), _bundle_id_for_pid(pid), title

    # AX 未授权时，若用户已有屏幕录制权限，WindowServer 仍能给实时前后层级。
    try:
        windows = _window_snapshot()
    except Exception:
        windows = []
    # CGWindowListCopyWindowInfo 已按前→后排序；首个 layer=0 窗口就是当前普通前台窗。
    for window in windows:
        try:
            if int(window.get("kCGWindowLayer", -1)) != 0:
                continue
            pid = int(window.get("kCGWindowOwnerPID", -1))
            owner = str(window.get("kCGWindowOwnerName") or "")
            if pid <= 0 or not owner:
                continue
            name = _localized_app_name(pid, owner)
            title = _ax_title_for_pid(pid) or str(window.get("kCGWindowName") or "")
            return name, _bundle_id_for_pid(pid), title
        except (TypeError, ValueError):
            continue

    # 最后才用 NSWorkspace，保证缺 AX/录屏时至少有退化结果；此路径可能受后台
    # Cocoa 通知缓存影响，所以不会再作为正常授权状态下的主路径。
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is not None:
            pid = int(app.processIdentifier())
            name = str(app.localizedName() or app.bundleIdentifier() or "未知应用")
            return name, str(app.bundleIdentifier() or ""), _ax_title_for_pid(pid)
    except Exception:
        pass
    return None


def sample_frontmost() -> tuple[str, str] | None:
    """取实时前台应用：系统级 AX 优先，WindowServer 与 NSWorkspace 依次退化。"""
    details = sample_frontmost_details()
    if details is None:
        return None
    name, _bundle_id, title = details
    return name, title


def sample_frontmost_bundle_id() -> str:
    """Read the current foreground bundle id; an empty value fails closed."""
    details = sample_frontmost_details()
    return details[1] if details is not None else ""


def sample_idle_seconds() -> float:
    """距任意用户输入的秒数；不安装 event tap，也不读取按键内容。"""
    if sys.platform != "darwin":
        return 0.0
    from Quartz import (

        CGEventSourceSecondsSinceLastEventType,
        kCGAnyInputEventType,
        kCGEventSourceStateCombinedSessionState,
    )

    return float(
        CGEventSourceSecondsSinceLastEventType(
            kCGEventSourceStateCombinedSessionState,
            kCGAnyInputEventType,
        )
    )


class PerceptionSensors:
    """A/C 轮询协调器；每轮读共享 settings，开关即时生效。"""

    def __init__(
        self,
        store: PerceptionStore,
        settings: dict,
        *,
        app_sampler: Callable[[], tuple[str, str] | tuple[str, str, str] | None] = sample_frontmost_details,
        idle_sampler: Callable[[], float] = sample_idle_seconds,
        screen_sampler: Callable[[], tuple | None] | None = None,
        vision_summarizer: Callable[[str], str | None] | None = None,
        secure_input_checker: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.settings = settings
        self.app_sampler = app_sampler
        self.idle_sampler = idle_sampler
        self.screen_sampler = screen_sampler
        self.vision_summarizer = vision_summarizer
        self.secure_input_checker = secure_input_checker
        self.clock = clock
        self._last_app: tuple[str, ...] | None = None
        self._last_activity: str | None = None
        self._activity_started_at: float | None = None
        self._last_sample_at: float | None = None
        self._sampling = False
        self._state_lock = threading.Lock()
        self._last_screen_key: tuple[str, str] | None = None
        self._last_screen_ts = 0.0
        self._screen_day = ""
        self._screen_events = 0
        self._screen_visions = 0

    def tick(self) -> None:
        if not self.settings.get("perception.master", False):
            with self._state_lock:
                self._last_app = None
                self._last_activity = None
                self._activity_started_at = None
                self._last_sample_at = None
                self._sampling = False
            return

        now = self.clock()
        with self._state_lock:
            self._sampling = True

        if self.settings.get("perception.app", False):
            current = self.app_sampler()
            if current is not None and current != self._last_app:
                if len(current) == 3:
                    app, bundle_id, title = current
                else:
                    app, title = current
                    bundle_id = ""
                payload = {"app": app, "title": title}
                if bundle_id:
                    payload["bundle_id"] = bundle_id
                self.store.append("app", "frontmost", payload, "S1", ts=now)
                self._last_app = current
        else:
            self._last_app = None

        if self.settings.get("perception.activity", False):
            idle_seconds = max(0, int(self.idle_sampler()))
            state = "idle" if idle_seconds >= 60 else "active"
            changed = state != self._last_activity
            if changed:
                self._activity_started_at = now
            if changed:
                self.store.append(
                    "activity",
                    state,
                    {
                        "idle_seconds": idle_seconds,
                        "segment_started_at": self._activity_started_at or now,
                    },
                    "S1",
                    ts=now,
                )
                self._last_activity = state
        else:
            self._last_activity = None
            self._activity_started_at = None
        with self._state_lock:
            self._last_sample_at = now
            self._sampling = False

        # ---- B 源（屏幕内容，S3）：变化/心跳触发，三层过滤，树优先截图兜底 ----
        if not self.settings.get("perception.master") or not self.settings.get("perception.screen"):
            self._last_screen_key = None
            return
        self._roll_screen_day(now)
        if self._screen_events >= SCREEN_DAILY_EVENT_CAP:
            return
        sample = self.screen_sampler() if self.screen_sampler else None
        if not sample:
            return
        status, tree, shot, app, bundle_id, title = sample
        key = (app, title)
        if key == self._last_screen_key and now - self._last_screen_ts < SCREEN_HEARTBEAT_SECONDS:
            self._discard_shot(shot)  # 去抖跳过的帧同样即清，不留明文
            return
        if self._is_screen_filtered(bundle_id, title):
            self._discard_shot(shot)
            return
        if self.secure_input_checker and self.secure_input_checker():
            self._discard_shot(shot)
            return
        if status == "tree" and tree:
            text = serialize_tree_text(tree)
            if not text:
                return
            self.store.append("screen", "tree", {"app": app, "title": title, "text": text}, "S3", ts=now)
            self._screen_events += 1
            self._last_screen_key, self._last_screen_ts = key, now
        elif shot and self.vision_summarizer and self._screen_visions < SCREEN_DAILY_VISION_CAP:
            summary = self.vision_summarizer(shot)
            # 概括后即删（生命周期表口径）：入库/敏感丢弃/概括失败都删，只留概括文本；
            # 失败帧删除后下一轮重新截图，重试语义不变（不去抖）。
            self._discard_shot(shot)
            if summary:
                # 拿到概括即去抖：敏感丢弃的帧不得每 tick 重复外发截图；
                # 概括失败（None）不更新去抖，下一轮允许重试。
                self._last_screen_key, self._last_screen_ts = key, now
                if not _sensitive_text(summary):
                    self.store.append("screen", "vision",
                                      {"app": app, "title": title, "text": summary, "path": shot}, "S3", ts=now)
                    self._screen_events += 1
                    self._screen_visions += 1
        elif shot:
            # vision 预算耗尽或无 summarizer：帧不入库也不残留
            self._discard_shot(shot)

    @staticmethod
    def _discard_shot(path: str | None) -> None:
        """B 源截图帧即清：过滤/概括后删除原图，堵「过滤前落盘」的明文残留。只 print 不抛。"""
        if not path:
            return
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass  # 已不存在（测试假路径/重复清理）属正常
        except OSError as exc:
            log(f"B 源截图帧清理失败（已跳过）：{exc}")

    def _roll_screen_day(self, now: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day != self._screen_day:
            self._screen_day, self._screen_events, self._screen_visions = day, 0, 0

    def _is_screen_filtered(self, bundle_id: str, title: str) -> bool:
        blocked = set(_BUILTIN_BLACKLIST) | set(self.settings.get("perception.blacklist") or [])
        if bundle_id in blocked:
            return True
        if bundle_id in _BROWSER_BUNDLES:
            t = (title or "").lower()
            return any(m in t for m in _PRIVATE_TITLE_MARKERS)
        return False

    def watch_state(self) -> dict:
        """Return a fresh in-memory state for watch without persisting heartbeat rows."""
        with self._state_lock:
            if self._sampling:
                return {"sampled_at": None}
            app = self._last_app
            if app is not None and len(app) == 3:
                app_name, app_id, _title = app
            elif app is not None:
                app_name, _title = app
                app_id = ""
            else:
                app_name = app_id = ""
            return {
                "sampled_at": self._last_sample_at,
                "app": app_name,
                "app_id": app_id,
                "activity": self._last_activity,
                "activity_started_at": self._activity_started_at,
            }

    def run(self, stop_event: threading.Event, *, interval: float = 5.0) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                log(f"感知采样失败（已跳过）：{exc}")
            stop_event.wait(interval)
