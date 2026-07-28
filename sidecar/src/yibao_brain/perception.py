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
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext

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

    def _decode_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": int(row["id"]),
            "ts": float(row["ts"]),
            "source": row["source"],
            "kind": row["kind"],
            "payload": self._decrypt(row["payload"]),
            "sensitivity": row["sensitivity"],
        }

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
        return [self._decode_row(row) for row in rows]

    def query_window(
        self, start_ts: float, end_ts: float, limit: int = 2000
    ) -> list[dict]:
        """按时间正序读取 A/C 观察；损坏行保留为空 payload，交给上层计数跳过。"""
        bounded_limit = max(1, min(int(limit), 2001))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
                "WHERE ts >= ? AND ts <= ? AND source IN ('app', 'activity') "
                "ORDER BY ts ASC, id ASC LIMIT ?",
                (float(start_ts), float(end_ts), bounded_limit),
            ).fetchall()
        return [self._decode_row(row) for row in rows]

    def latest_before(self, source: str, ts: float) -> dict | None:
        """读取指定来源在窗口起点前最后一个可解密状态。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
                "WHERE source = ? AND ts < ? ORDER BY ts DESC, id DESC LIMIT 1",
                (source, float(ts)),
            ).fetchone()
        if row is None:
            return None
        item = self._decode_row(row)
        return item if item["payload"] else None

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


def _observation_state(item: dict) -> dict:
    """把单条 A/C 观察规范化为时间线状态增量；坏数据不产生状态。"""
    payload = item.get("payload")
    if not isinstance(payload, dict) or not payload:
        return {}
    if item.get("source") == "app" and payload.get("app"):
        return {
            "app": str(payload["app"]),
            "title": str(payload.get("title") or ""),
        }
    if item.get("source") == "activity" and item.get("kind") in ("active", "idle"):
        return {"activity": item["kind"]}
    return {}


def build_activity_segments(
    rows: list[dict],
    seeds: list[dict],
    start_ts: float,
    end_ts: float,
    *,
    max_segments: int = 120,
) -> tuple[list[dict], bool]:
    """把 app/title 与 active/idle 状态变化合并成有界的正序时间线。"""
    state: dict = {}
    for seed in seeds:
        state.update(_observation_state(seed))

    segments: list[dict] = []
    cursor = float(start_ts)
    end = float(end_ts)

    def append_segment(segment_end: float) -> None:
        nonlocal cursor
        if not state or segment_end <= cursor:
            return
        segment = {"start_ts": cursor, "end_ts": segment_end, **state}
        if (
            segments
            and segments[-1]["end_ts"] == segment["start_ts"]
            and {k: v for k, v in segments[-1].items() if k not in ("start_ts", "end_ts")}
            == {k: v for k, v in segment.items() if k not in ("start_ts", "end_ts")}
        ):
            segments[-1]["end_ts"] = segment_end
        else:
            segments.append(segment)

    for item in sorted(rows, key=lambda row: (float(row.get("ts", 0)), int(row.get("id", 0)))):
        event_ts = float(item.get("ts", start_ts))
        if event_ts < start_ts or event_ts > end_ts:
            continue
        delta = _observation_state(item)
        if not delta:
            continue
        changed = any(state.get(key) != value for key, value in delta.items())
        if not changed:
            continue
        if state:
            append_segment(event_ts)
        else:
            # 窗口内首次得知状态，不能把它倒推到窗口起点。
            cursor = event_ts
        state.update(delta)
        cursor = event_ts

    append_segment(end)
    max_segments = max(1, int(max_segments))
    truncated = len(segments) > max_segments
    if truncated:
        segments = segments[-max_segments:]
    return segments, truncated


class LoadUserActivitySkill(Skill):
    """按模型选择的时间窗口加载本机 A/C 感知记录。"""

    id = "load_user_activity"
    label = "加载活动记录"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True
    description = (
        "仅在用户询问过去活动、应用/窗口切换或刚才的工作上下文时，加载本机感知记录；"
        "不得为无关的个性化回答调用。参数必须是带时区的 ISO 8601 本地时间。"
        "用户说“刚才”通常查询最近 30 分钟，“最近”通常查询最近 60 分钟，"
        "“今天”查询本地当天 00:00 到当前时间；单次最多 24 小时。"
    )

    def __init__(
        self,
        store: PerceptionStore,
        settings: dict,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.settings = settings
        self.now_provider = now_provider or (lambda: datetime.now().astimezone())

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "start_at": {
                        "type": "string",
                        "description": (
                            "查询起点，带时区的本地时间 ISO 8601，"
                            "例如 2026-07-28T13:00:00+08:00"
                        ),
                    },
                    "end_at": {
                        "type": "string",
                        "description": (
                            "查询终点，带时区的本地时间 ISO 8601，"
                            "例如 2026-07-28T14:00:00+08:00"
                        ),
                    },
                },
                "required": ["start_at", "end_at"],
            },
        }

    def precheck(self, params: dict) -> str | None:
        if not self.settings.get("perception.model_access", False):
            return "模型读取感知记录未开启，请先在设置的感知区域开启"
        return None

    @staticmethod
    def _parse_datetime(value: object, label: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 必须是带时区的 ISO 8601 时间")
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValueError(f"{label} 不是有效的 ISO 8601 时间") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"{label} 必须包含时区")
        return parsed

    def _window(self, params: dict) -> tuple[datetime, datetime]:
        start = self._parse_datetime(params.get("start_at"), "start_at")
        end = self._parse_datetime(params.get("end_at"), "end_at")
        if start >= end:
            raise ValueError("start_at 必须早于 end_at")
        if end - start > timedelta(hours=24):
            raise ValueError("单次查询不能超过 24 小时，请缩小时间窗口")
        now = self.now_provider()
        if now.tzinfo is None or now.utcoffset() is None:
            now = now.astimezone()
        if end > now + timedelta(minutes=5):
            raise ValueError("end_at 不能位于未来 5 分钟以后")
        return start, end

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        blocked = self.precheck(params)
        if blocked:
            return ActionResult(success=False, error=blocked)
        try:
            start, end = self._window(params)
        except (TypeError, ValueError) as exc:
            return ActionResult(success=False, error=str(exc))

        try:
            rows = self.store.query_window(start.timestamp(), end.timestamp())
            seeds = [
                item
                for item in (
                    self.store.latest_before("app", start.timestamp()),
                    self.store.latest_before("activity", start.timestamp()),
                )
                if item is not None
            ]
        except Exception as exc:
            return ActionResult(success=False, error=f"无法读取感知记录：{exc}")

        valid_rows = [row for row in rows if row.get("payload")]
        skipped_count = len(rows) - len(valid_rows)
        segments, truncated = build_activity_segments(
            valid_rows,
            seeds,
            start.timestamp(),
            end.timestamp(),
        )
        tz = start.tzinfo
        formatted = [
            {
                "start_at": datetime.fromtimestamp(item["start_ts"], tz=tz).isoformat(),
                "end_at": datetime.fromtimestamp(item["end_ts"], tz=tz).isoformat(),
                **{
                    key: value
                    for key, value in item.items()
                    if key not in ("start_ts", "end_ts")
                },
            }
            for item in segments
        ]
        return ActionResult(
            success=True,
            data={
                "window": {"start_at": start.isoformat(), "end_at": end.isoformat()},
                "segments": formatted,
                "observation_count": len(valid_rows),
                "skipped_count": skipped_count,
                "truncated": truncated,
            },
        )

    def safe_result(self, result: ActionResult) -> ActionResult:
        if not result.success:
            return ActionResult(success=False, error=result.error)
        data = result.data or {}
        return ActionResult(
            success=True,
            data={
                "window": data.get("window", {}),
                "observation_count": int(data.get("observation_count", 0)),
                "segment_count": len(data.get("segments") or []),
                "truncated": bool(data.get("truncated", False)),
            },
        )

    def post_reply_notice(self, result: ActionResult) -> str | None:
        if result.success and (result.data or {}).get("segments"):
            return "已参考最近活动"
        return None


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
