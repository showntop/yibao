"""长期记忆：接口 + Fake（测试）+ Mem0（生产）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Memory(ABC):
    @abstractmethod
    def add(self, text: str, user_id: str) -> None: ...

    @abstractmethod
    def recall(self, query: str, user_id: str) -> list[str]: ...


class FakeMemory(Memory):
    """简单子串匹配；按 user_id 隔离。"""

    def __init__(self) -> None:
        self._by_user: dict[str, list[str]] = {}

    def add(self, text: str, user_id: str) -> None:
        self._by_user.setdefault(user_id, []).append(text)

    def recall(self, query: str, user_id: str) -> list[str]:
        items = self._by_user.get(user_id, [])
        q = query.lower()
        return [it for it in items if q and (q in it.lower() or it.lower() in q)]


class Mem0Memory(Memory):
    """mem0 封装；失败时优雅降级为空召回（不阻断回路）。"""

    def __init__(self) -> None:
        from mem0 import Memory as _Mem0

        self._m = _Mem0()

    def add(self, text: str, user_id: str) -> None:
        self._m.add(messages=[{"role": "user", "content": text}], user_id=user_id)

    def recall(self, query: str, user_id: str) -> list[str]:
        try:
            res = self._m.search(query=query, user_id=user_id)
        except Exception:
            return []
        out: list[str] = []
        items = res if isinstance(res, list) else (res.get("results", []) if isinstance(res, dict) else [])
        for item in items:
            mem = item.get("memory") if isinstance(item, dict) else str(item)
            if mem:
                out.append(mem)
        return out
