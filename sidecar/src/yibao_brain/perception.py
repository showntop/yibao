"""本机感知 v1 的编排域：感知技能（load_user_activity / load_screen_content）+ 时间线纯函数。

R-32c（2026-08-22）三分：存储层 → perception_store.py（PerceptionStore + Keychain 密钥
托管 + PerceptionKeyUnavailable）；传感器域 → perception_sensors.py（窗口/AX 采样 +
sample_* + serialize_tree_text + PerceptionSensors）；本文件保留技能与时间线纯函数，
并 re-export 两域符号——server/测试的 `from .perception import PerceptionStore` 等
路径不变（引用面多为函数内延迟 import）。
"""
from __future__ import annotations

from .log import log
from collections.abc import Callable
from datetime import datetime, timedelta

from .ipc import ActionResult, RiskLevel
from .tools import Tool, ToolContext
from .perception_store import PerceptionKeyUnavailable, PerceptionStore, key_from_macos_keychain  # noqa: F401  re-export：存储层已拆出，路径兼容
from .perception_sensors import (  # noqa: F401  re-export：传感器域已拆出，路径兼容
    PerceptionSensors,
    sample_frontmost,
    sample_frontmost_bundle_id,
    sample_frontmost_details,
    serialize_tree_text,
)


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


class LoadUserActivityTool(Tool):
    """按模型选择的时间窗口加载本机 A/C 感知记录。"""

    id = "load_user_activity"
    label = "加载活动记录"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True
    description = (
        "仅在用户询问自己过去做了什么、何时在电脑前、屏幕状态、或需要时间线上下文时调用；"
        "不得为无关的个性化回答调用。start_at/end_at 为带时区的 ISO 8601 本地时间，"
        "单次查询不超过 24 小时。"
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

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
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


class LoadScreenContentTool(Tool):
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

    def run(self, params: dict, ctx: ToolContext) -> ActionResult:
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
