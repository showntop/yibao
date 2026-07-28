"""本机感知 v1：加密观察存储。

原始观察是增强面：写失败只记录 stderr，不得拖垮对话主链路；但加密失败绝不
降级明文。payload 使用 Fernet 加密，macOS 运行时密钥由登录 Keychain 托管。
"""
from __future__ import annotations

import getpass
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable

from cryptography.fernet import Fernet, InvalidToken

_KEYCHAIN_SERVICE = "com.yibao.perception"
_DAY = 86400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  sensitivity TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source);
"""


class PerceptionKeyUnavailable(RuntimeError):
    """无法安全取得感知密钥；调用方必须保持感知关闭。"""


def _run_security(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise PerceptionKeyUnavailable("访问 macOS Keychain 超时") from exc


def key_from_macos_keychain() -> bytes:
    """读取或创建登录 Keychain 中的 Fernet key。"""
    if sys.platform != "darwin":
        raise PerceptionKeyUnavailable("感知密钥仅支持 macOS Keychain")
    account = getpass.getuser()
    find = _run_security(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            _KEYCHAIN_SERVICE,
            "-w",
        ],
    )
    if find.returncode == 0 and find.stdout.strip():
        key = find.stdout.strip().encode("ascii")
        try:
            Fernet(key)
        except (ValueError, TypeError) as exc:
            raise PerceptionKeyUnavailable("Keychain 中的感知密钥已损坏") from exc
        return key

    key = Fernet.generate_key()
    add = _run_security(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            account,
            "-s",
            _KEYCHAIN_SERVICE,
            "-w",
            key.decode("ascii"),
        ],
    )
    if add.returncode != 0:
        detail = add.stderr.strip() or "unknown error"
        raise PerceptionKeyUnavailable(f"无法把感知密钥写入 Keychain：{detail}")
    return key


class PerceptionStore:
    """线程安全的加密观察 SQLite store。"""

    def __init__(
        self,
        db_path: str,
        *,
        key: bytes | None = None,
        key_provider: Callable[[], bytes] | None = None,
    ):
        self.db_path = db_path
        parent = os.path.dirname(db_path) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.chmod(parent, 0o700)
        resolved_key = key if key is not None else (key_provider or key_from_macos_keychain)()
        try:
            self._fernet = Fernet(resolved_key)
        except (ValueError, TypeError) as exc:
            raise PerceptionKeyUnavailable("感知密钥格式无效") from exc
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
        os.chmod(db_path, 0o600)

    def _encrypt(self, payload: dict) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def _decrypt(self, token: str) -> dict:
        try:
            value = json.loads(self._fernet.decrypt(token.encode("ascii")).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError):
            return {}

    def append(
        self,
        source: str,
        kind: str,
        payload: dict,
        sensitivity: str,
        *,
        ts: float | None = None,
    ) -> int | None:
        """加密追加观察；失败只报 stderr 并返回 None。"""
        try:
            encrypted = self._encrypt(payload)
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO observations (ts, source, kind, payload, sensitivity) VALUES (?, ?, ?, ?, ?)",
                    (time.time() if ts is None else ts, source, kind, encrypted, sensitivity),
                )
                self._conn.commit()
                return int(cur.lastrowid)
        except Exception as exc:
            print(f"[yibao] 感知写入失败（已跳过）：{exc}", file=sys.stderr)
            return None

    def list(self, limit: int = 60, before_id: int | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        sql = "SELECT id, ts, source, kind, payload, sensitivity FROM observations"
        args: list[object] = []
        if before_id is not None:
            sql += " WHERE id < ?"
            args.append(int(before_id))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            {
                "id": int(row["id"]),
                "ts": float(row["ts"]),
                "source": row["source"],
                "kind": row["kind"],
                "payload": self._decrypt(row["payload"]),
                "sensitivity": row["sensitivity"],
            }
            for row in rows
        ]

    def delete(self, observation_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM observations WHERE id = ?", (int(observation_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def sources(self) -> list[str]:
        """返回整个日志中出现过的来源，不受当前分页范围影响。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT source FROM observations ORDER BY source"
            ).fetchall()
        return [str(row["source"]) for row in rows]

    def clear(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM observations")
            self._conn.commit()
            return max(0, cur.rowcount)

    def purge(self, *, now: float | None = None) -> int:
        """按来源清理：A/C 30 天、screen/environment 7 天、clipboard 1 天。"""
        current = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM observations
                WHERE (source IN ('app', 'activity') AND ts < ?)
                   OR (source IN ('screen', 'environment') AND ts < ?)
                   OR (source = 'clipboard' AND ts < ?)
                   OR (source NOT IN ('app', 'activity', 'screen', 'environment', 'clipboard') AND ts < ?)
                """,
                (current - 30 * _DAY, current - 7 * _DAY, current - _DAY, current - 7 * _DAY),
            )
            self._conn.commit()
            return max(0, cur.rowcount)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


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


def sample_frontmost() -> tuple[str, str] | None:
    """取实时前台应用：系统级 AX 优先，WindowServer 与 NSWorkspace 依次退化。"""
    if sys.platform != "darwin":
        return None

    # A 源本来就依赖辅助功能授权。系统级 focusedApplication 每次直读焦点，
    # 不会像后台线程里的 NSWorkspace.frontmostApplication 一样停在启动快照。
    focused = _ax_frontmost()
    if focused is not None:
        pid, title = focused
        return _localized_app_name(pid, "未知应用"), title

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
            return name, title
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
            return name, _ax_title_for_pid(pid)
    except Exception:
        pass
    return None


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
        app_sampler: Callable[[], tuple[str, str] | None] = sample_frontmost,
        idle_sampler: Callable[[], float] = sample_idle_seconds,
    ):
        self.store = store
        self.settings = settings
        self.app_sampler = app_sampler
        self.idle_sampler = idle_sampler
        self._last_app: tuple[str, str] | None = None
        self._last_activity: str | None = None

    def tick(self) -> None:
        if not self.settings.get("perception.master", False):
            self._last_app = None
            self._last_activity = None
            return

        if self.settings.get("perception.app", False):
            current = self.app_sampler()
            if current is not None and current != self._last_app:
                app, title = current
                self.store.append("app", "frontmost", {"app": app, "title": title}, "S1")
                self._last_app = current
        else:
            self._last_app = None

        if self.settings.get("perception.activity", False):
            idle_seconds = max(0, int(self.idle_sampler()))
            state = "idle" if idle_seconds >= 60 else "active"
            if state != self._last_activity:
                self.store.append("activity", state, {"idle_seconds": idle_seconds}, "S1")
                self._last_activity = state
        else:
            self._last_activity = None

    def run(self, stop_event: threading.Event, *, interval: float = 5.0) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"[yibao] 感知采样失败（已跳过）：{exc}", file=sys.stderr)
            stop_event.wait(interval)
