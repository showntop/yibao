"""本机感知 v1：加密观察存储。

原始观察是增强面：写失败只记录 stderr，不得拖垮对话主链路；但加密失败绝不
降级明文。payload 使用 Fernet 加密，macOS 运行时密钥由登录 Keychain 托管。
"""
from __future__ import annotations

import getpass
import json
import os
import re
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
        self,
        start_ts: float,
        end_ts: float,
        limit: int = 2000,
        sources: tuple[str, ...] | None = None,
    ) -> list[dict]:
        """按时间正序读取观察；默认只取 A/C 源，损坏行保留为空 payload，交给上层计数跳过。"""
        bounded_limit = max(1, min(int(limit), 2001))
        source_set = tuple(sources) if sources else ("app", "activity")
        placeholders = ", ".join("?" for _ in source_set)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, source, kind, payload, sensitivity FROM ("
                "SELECT id, ts, source, kind, payload, sensitivity FROM observations "
                f"WHERE ts >= ? AND ts <= ? AND source IN ({placeholders}) "
                "ORDER BY ts DESC, id DESC LIMIT ?"
                ") ORDER BY ts ASC, id ASC",
                (float(start_ts), float(end_ts), *source_set, bounded_limit),
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
            rows = self.store.query_window(start.timestamp(), end.timestamp(), limit=2001)
            input_truncated = len(rows) > 2000
            if input_truncated:
                rows = rows[-2000:]
            timeline_start = float(rows[0]["ts"]) if input_truncated else start.timestamp()
            seeds = [
                item
                for item in (
                    self.store.latest_before("app", timeline_start),
                    self.store.latest_before("activity", timeline_start),
                )
                if item is not None
            ]
        except Exception as exc:
            return ActionResult(success=False, error=f"无法读取感知记录：{exc}")

        valid_rows = [row for row in rows if row.get("payload")]
        skipped_count = len(rows) - len(valid_rows)
        segments, segment_truncated = build_activity_segments(
            valid_rows,
            seeds,
            timeline_start,
            end.timestamp(),
        )
        truncated = input_truncated or segment_truncated
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


class LoadScreenContentSkill(Skill):
    """按模型选择的回看分钟数加载本机屏幕内容记录（B 源：tree/vision 文本）。"""

    id = "load_screen_content"
    label = "加载屏幕内容"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True
    description = (
        "仅在用户询问屏幕上看到的内容、当前或刚才页面/窗口上的文字时，加载本机屏幕内容记录；"
        "不得为无关的个性化回答调用。minutes 为向前回看的分钟数（默认 30，最多 1440），"
        "limit 为返回条数（默认 10，最多 20），按时间倒序返回最新条目。"
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
                    "minutes": {
                        "type": "integer",
                        "description": "向前回看的分钟数，默认 30，最大 1440（24 小时）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回条数，默认 10，最大 20",
                    },
                },
            },
        }

    def precheck(self, params: dict) -> str | None:
        if not self.settings.get("perception.model_access", False):
            return "模型读取感知记录未开启，请先在设置的感知区域开启"
        return None

    @staticmethod
    def _bounded_int(value: object, default: int, lo: int, hi: int) -> int:
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, parsed))

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        blocked = self.precheck(params)
        if blocked:
            return ActionResult(success=False, error=blocked)
        minutes = self._bounded_int(params.get("minutes", 30), 30, 1, 1440)
        limit = self._bounded_int(params.get("limit", 10), 10, 1, 20)

        end = self.now_provider().timestamp()
        start = end - minutes * 60
        try:
            rows = self.store.query_window(start, end, limit=limit + 1, sources=("screen",))
        except Exception as exc:
            return ActionResult(success=False, error=f"无法读取感知记录：{exc}")

        truncated = len(rows) > limit
        if truncated:
            rows = rows[-limit:]
        items = [
            {
                "ts": float(row.get("ts", 0)),
                "app": str(row["payload"].get("app") or ""),
                "kind": str(row.get("kind") or ""),
                "text": str(row["payload"].get("text") or ""),
            }
            for row in reversed(rows)  # 查询返回正序，对模型按时间倒序呈现最新内容
            if row.get("payload")
        ]
        return ActionResult(
            success=True,
            data={
                "minutes": minutes,
                "items": items,
                "count": len(items),
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
                "minutes": int(data.get("minutes", 0)),
                "count": int(data.get("count", 0)),
                "truncated": bool(data.get("truncated", False)),
            },
        )

    def post_reply_notice(self, result: ActionResult) -> str | None:
        if result.success and (result.data or {}).get("items"):
            return "已参考屏幕内容"
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
            print(f"[yibao] B 源截图帧清理失败（已跳过）：{exc}", file=sys.stderr)

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
                print(f"[yibao] 感知采样失败（已跳过）：{exc}", file=sys.stderr)
            stop_event.wait(interval)
