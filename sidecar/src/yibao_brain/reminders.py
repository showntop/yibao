"""主动提醒：存储（JSON 落盘）+ reminder.set/list/cancel 三个底座技能 + 时间解析。

设计要点：
- 存储是 data_dir/reminders.json（原子写：tmp + rename），大脑重启后未到期的提醒不丢；
- 触发由 server 的调度循环 pop_due 取走（标 fired 落盘），事件经 stdio 推到壳；
- LLM 负责把「1 小时后 / 明早 9 点」翻成 delay_minutes 或 at（system 消息里注入了当前时间），
  本模块只做最朴素的时间校验与解析，不做自然语言理解。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from .ipc import ActionResult, RiskLevel
from .skills import Skill

_MIN_DELAY_S = 10  # 太短没意义（调度 10s 一拍），也防 LLM 给出 0/负数
_MAX_DELAY_S = 366 * 24 * 3600  # 一年以上不收


class ReminderStore:
    """提醒存取：[{id, text, fire_at(epoch), created_at, fired}]。线程安全，落盘原子。"""

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

    def add(self, text: str, fire_at: float) -> dict:
        with self._lock:
            item = {
                "id": uuid.uuid4().hex[:8],
                "text": text,
                "fire_at": fire_at,
                "created_at": time.time(),
                "fired": False,
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
        """取走到期项并标 fired 落盘；调度循环定期调用。"""
        with self._lock:
            due = [r for r in self._items if not r.get("fired") and float(r.get("fire_at", 0)) <= now]
            if not due:
                return []
            for r in due:
                r["fired"] = True
            self._save()
            return [dict(r) for r in due]


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m月%d日 %H:%M")


def _fmt_item(r: dict) -> str:
    return f"{r['id']}：{_fmt_ts(float(r['fire_at']))} · {r['text']}"


def _parse_at(raw: str) -> float | None:
    """解析 LLM 给的绝对时间：接受 ISO8601 或「YYYY-MM-DD HH:MM」/「HH:MM」（今天，过了算明天）。"""
    s = raw.strip()
    try:
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if m:
        now = datetime.now()
        dt = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if dt.timestamp() <= time.time():  # 今天的点已过 → 明天
            dt = datetime.fromtimestamp(dt.timestamp() + 86400)
        return dt.timestamp()
    return None


class ReminderSetSkill(Skill):
    id = "reminder.set"
    description = (
        "设置定时提醒：到点后译宝会主动找用户说话（气泡 + 语音）。"
        "用户说「X 分钟/小时后提醒我…」「明天 X 点叫我…」「每天…（不支持重复，说明即可）」时用。"
        "二选一给时间：delay_minutes（相对，如「1 小时后」→60）或 at（绝对，ISO8601 或 HH:MM）。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self, store: ReminderStore) -> None:
        self._store = store

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "提醒内容（到点原样说给用户）"},
                        "delay_minutes": {"type": "number", "description": "多少分钟后触发"},
                        "at": {"type": "string", "description": "绝对触发时间（ISO8601 或 HH:MM）"},
                    },
                    "required": ["text"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        text = str(params.get("text") or "").strip()
        if not text:
            return ActionResult(success=False, error="提醒内容为空")
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
            return ActionResult(success=False, error="没说什么时候提醒（给 delay_minutes 或 at）")
        if fire_at - now > _MAX_DELAY_S:
            return ActionResult(success=False, error="时间太远（超过一年）")
        item = self._store.add(text, fire_at)
        return ActionResult(
            success=True,
            data={"id": item["id"], "fire_at": fire_at,
                  "human": f"好的，{_fmt_ts(fire_at)} 提醒你：{text}"},
        )


class ReminderListSkill(Skill):
    id = "reminder.list"
    description = "列出还没触发的提醒（用户问「我有什么提醒/闹钟」时用）。"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self, store: ReminderStore) -> None:
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
                  "human": "没有待触发的提醒" if not items else
                           "待触发提醒：\n" + "\n".join(_fmt_item(r) for r in items)},
        )


class ReminderCancelSkill(Skill):
    id = "reminder.cancel"
    description = "取消一个待触发的提醒（先 reminder.list 拿 id；用户说「取消那个提醒」时用）。"
    default_risk = RiskLevel.L1_LOW

    def __init__(self, store: ReminderStore) -> None:
        self._store = store

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "提醒 id（或前几位）"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        rid = str(params.get("id") or "").strip()
        if not rid:
            return ActionResult(success=False, error="没给要取消的提醒 id")
        item = self._store.cancel(rid)
        if item is None:
            return ActionResult(success=False, error=f"没找到待触发的提醒：{rid}")
        return ActionResult(success=True, data={"id": item["id"],
                                                "human": f"已取消：{_fmt_item(item)}"})


def make_skills(store: ReminderStore) -> list[Skill]:
    return [ReminderSetSkill(store), ReminderListSkill(store), ReminderCancelSkill(store)]
