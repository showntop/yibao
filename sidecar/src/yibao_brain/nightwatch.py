"""守夜人：隔夜任务运行器——NightStore（JSON 落盘）+ night_set/list/cancel 三个底座技能。

设计要点（与 reminders 对称，存储/触发/人话回执照抄）：
- 存储是 data_dir/nightwatch.json（原子写：tmp + rename），大脑重启后未触发的任务不丢；
- 触发由 server 的 _night_loop 每 10s pop_due 取走执行，结果作为晨报落对话历史；
- 任务目标是插件工具（id 必须含 "."）且风险 ≤ L1：夜间无人值守不能弹确认，
  确认发生在「布置」这一刻（night_set 本身 L1，由用户睡前一句话触发）；
- 重复规则只收 daily（隔夜场景没有 weekly 诉求）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from typing import Any

from .ipc import ActionResult, RiskLevel
from .reminders import _MAX_DELAY_S, _MIN_DELAY_S, _fmt_when, _parse_at
from .tools import Tool

_RRULE_STEP = {"daily": 86400}  # 只收 daily；隔夜任务没有每周诉求


class NightStore:
    """隔夜任务存取：[{id, name, tool, params, fire_at, created_at, fired, rrule,
    last_run_at, last_status, last_error}]。线程安全，落盘原子。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._items: list[dict] = []
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                self._items = [r for r in raw if isinstance(r, dict) and r.get("id")]
        except (OSError, json.JSONDecodeError):
            self._items = []  # 缺文件/坏文件都从空开始，不阻断启动

    def _save(self) -> None:
        d = os.path.dirname(self._path)
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._items, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def add(self, tool: str, params: dict, name: str, fire_at: float,
            rrule: str | None = None) -> dict:
        if rrule is not None and rrule not in _RRULE_STEP:
            raise ValueError(f"未知重复规则：{rrule!r}")
        with self._lock:
            item = {
                "id": uuid.uuid4().hex[:8],
                "name": name,
                "tool": tool,
                "params": params,
                "fire_at": fire_at,
                "created_at": time.time(),
                "fired": False,
                "rrule": rrule,
                "last_run_at": None,
                "last_status": None,  # "ok" / "error" / None（还没跑过）
                "last_error": None,
            }
            self._items.append(item)
            self._save()
            return dict(item)

    def list_pending(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._items if not r.get("fired")]

    def cancel(self, rid: str) -> dict | None:
        """按 id（或 id 前缀）取消；返回被取消项，没找到返回 None。"""
        with self._lock:
            for r in self._items:
                if not r.get("fired") and str(r.get("id", "")).startswith(rid):
                    r["fired"] = True  # 复用 fired 标记（不再触发），历史留痕
                    self._save()
                    return dict(r)
            return None

    def pop_due(self, now: float) -> list[dict]:
        """取走到期项：一次性项标 fired 落盘；daily 重排到下一个未来时点再触发。"""
        with self._lock:
            due = [r for r in self._items if not r.get("fired") and float(r.get("fire_at", 0)) <= now]
            if not due:
                return []
            for r in due:
                step = _RRULE_STEP.get(r.get("rrule") or "")
                if step is None:
                    r["fired"] = True
                else:
                    # 重排到下一个未来时点（关机错过好几天也只补到将来，不补刷屏）
                    fire_at = float(r["fire_at"]) + step
                    while fire_at <= now:
                        fire_at += step
                    r["fire_at"] = fire_at
            self._save()
            return [dict(r) for r in due]

    def mark_result(self, job_id: str, ok: bool, error: str | None = None) -> None:
        """记一次执行结果（fired 的一次性任务也照记——留痕供 night_list 之外排查）。"""
        with self._lock:
            for r in self._items:
                if r.get("id") == job_id:
                    r["last_run_at"] = time.time()
                    r["last_status"] = "ok" if ok else "error"
                    r["last_error"] = None if ok else (error or "未知错误")
                    self._save()
                    return


def _fmt_item(r: dict) -> str:
    line = f"{r['id']}：{_fmt_when(float(r['fire_at']), r.get('rrule'))} · {r.get('name') or r['tool']}（{r['tool']}）"
    if r.get("last_run_at"):  # daily 任务跑过会留在待触发里，带上次结果供排障
        if r.get("last_status") == "ok":
            line += "，上次：成功"
        else:
            line += f"，上次：失败（{r.get('last_error') or '未知错误'}）"
    return line


class NightSetTool(Tool):
    id = "night_set"
    label = "布置夜间任务"
    description = (
        "布置一个隔夜任务：到点后无人值守地执行一个插件工具，早上结果作为晨报发到对话里。"
        "用户说「每晚 X 点跑…」「睡前布置…明早给我…」「明早 X 点给我晨报」时用。"
        "tool 必须是插件工具（id 形如 插件.工具，如 zimeiti.night_brief）且风险不高于 L1"
        "（夜里不能弹确认，确认就是现在这句话）。"
        "二选一给时间：delay_minutes（相对）或 at（绝对，ISO8601 或 HH:MM）；「每天」再给 repeat=daily。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self, store: NightStore, registry: Any) -> None:
        self._store = store
        self._registry = registry  # 校验目标工具已注册且风险够低

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "要执行的插件工具 id（如 zimeiti.night_brief）"},
                        "params": {"type": "object", "description": "传给工具的参数（可选）"},
                        "name": {"type": "string", "description": "任务显示名（可选，缺省用工具名）"},
                        "delay_minutes": {"type": "number", "description": "多少分钟后触发"},
                        "at": {"type": "string", "description": "绝对触发时间（ISO8601 或 HH:MM）"},
                        "repeat": {"type": "string", "enum": ["daily"],
                                   "description": "重复规则：daily=每天；不填=一次性"},
                    },
                    "required": ["tool"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        tool_id = str(params.get("tool") or "").strip()
        if not tool_id:
            return ActionResult(success=False, error="没说要跑哪个工具（给 tool）")
        if "." not in tool_id:
            return ActionResult(success=False, error="夜间任务只能调度插件工具（id 形如 插件.工具）")
        try:
            target = self._registry.get(tool_id)
        except KeyError:
            return ActionResult(success=False, error=f"没有这个工具：{tool_id}（先确认插件已加载）")
        if target.default_risk > RiskLevel.L1_LOW:
            return ActionResult(
                success=False,
                error=f"「{tool_id}」风险较高（{target.default_risk.name}），夜里无人值守跑不了；换个 L1 以下的工具")
        job_params = params.get("params")
        if job_params is None:
            job_params = {}
        if not isinstance(job_params, dict):
            return ActionResult(success=False, error="params 必须是一个对象")
        now = time.time()
        fire_at: float | None = None
        if params.get("delay_minutes") is not None:
            try:
                delay = float(params["delay_minutes"]) * 60
            except (TypeError, ValueError):
                return ActionResult(success=False, error="delay_minutes 不是数字")
            if delay < _MIN_DELAY_S:
                return ActionResult(success=False, error="间隔太短（少于 10 秒），说个久一点的时间")
            fire_at = now + delay
        elif params.get("at"):
            fire_at = _parse_at(str(params["at"]))
            if fire_at is None:
                return ActionResult(success=False, error=f"看不懂这个时间：{params['at']}")
            if fire_at <= now:
                return ActionResult(success=False, error="这个时间已经过了")
        else:
            return ActionResult(success=False, error="没说什么时候跑（给 delay_minutes 或 at）")
        if fire_at - now > _MAX_DELAY_S:
            return ActionResult(success=False, error="时间太远（超过一年）")
        repeat = params.get("repeat")
        if repeat is not None:
            repeat = str(repeat)
            if repeat not in _RRULE_STEP:
                return ActionResult(success=False, error=f"未知重复规则：{repeat}（只支持 daily）")
        name = str(params.get("name") or "").strip() or (target.label or tool_id)
        item = self._store.add(tool_id, job_params, name, fire_at, rrule=repeat)
        return ActionResult(
            success=True,
            data={"id": item["id"], "fire_at": fire_at, "rrule": repeat,
                  "human": f"好的，{_fmt_when(fire_at, repeat)} 跑 {name}"},
        )


class NightListTool(Tool):
    id = "night_list"
    label = "查夜间任务"
    description = "列出还没触发的夜间任务（用户问「我布置了什么夜间任务/今晚跑什么」时用）。"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, store: NightStore) -> None:
        self._store = store

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.id, "description": self.description,
                         "parameters": {"type": "object", "properties": {}}},
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        items = sorted(self._store.list_pending(), key=lambda r: r["fire_at"])
        return ActionResult(
            success=True,
            data={"count": len(items), "items": [_fmt_item(r) for r in items],
                  "human": "没有待触发的夜间任务" if not items else
                           "待触发夜间任务：\n" + "\n".join(_fmt_item(r) for r in items)},
        )


class NightCancelTool(Tool):
    id = "night_cancel"
    label = "取消夜间任务"
    description = "取消一个待触发的夜间任务（先 night_list 拿 id；用户说「取消那个夜间任务」时用）。"
    default_risk = RiskLevel.L1_LOW

    def __init__(self, store: NightStore) -> None:
        self._store = store

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "任务 id（或前几位）"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        rid = str(params.get("id") or "").strip()
        if not rid:
            return ActionResult(success=False, error="没给要取消的任务 id")
        item = self._store.cancel(rid)
        if item is None:
            return ActionResult(success=False, error=f"没找到待触发的夜间任务：{rid}")
        return ActionResult(success=True, data={"id": item["id"],
                                                "human": f"已取消：{_fmt_item(item)}"})


def make_skills(store: NightStore, registry: Any) -> list[Tool]:
    return [NightSetTool(store, registry), NightListTool(store), NightCancelTool(store)]
