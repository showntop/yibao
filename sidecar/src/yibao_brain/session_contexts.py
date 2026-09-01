"""会话工作语境绑定：Session 与 Workspace(Project V1a) 的显式关联。

目标架构里 Workspace 与 Session 是正交实体；V1 仍复用现有 Project 作为
Workspace 兼容 façade，但当前工作语境不再来自进程全局 setting，而是按
conversation_id 独立持久化。空 conversation_id 保留旧路径，由 ProjectStore
回退 current_project_id，兼容宠物窗/旧调用方。
"""
from __future__ import annotations

import json
import os
import threading
import time

from .log import log


class SessionContextStore:
    """持久化 `{conversation_id -> workspace_id}`；不拥有 Workspace 数据。"""

    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("contexts"), dict):
                return raw
            raise ValueError("session_contexts.json 结构非法")
        except FileNotFoundError:
            return {"contexts": {}}
        except Exception as e:
            try:
                os.replace(self._path, self._path + ".bak")
            except OSError:
                pass
            log(f"session_contexts.json 损坏（已备份 .bak，起空库）：{e}")
            return {"contexts": {}}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    def workspace_id(self, conversation_id: str) -> str:
        if not conversation_id:
            return ""
        with self._lock:
            item = self._data["contexts"].get(conversation_id)
            return str(item.get("workspace_id") or "") if isinstance(item, dict) else ""

    def bind(self, conversation_id: str, workspace_id: str) -> None:
        if not conversation_id:
            raise ValueError("conversation_id 不能为空")
        with self._lock:
            self._data["contexts"][conversation_id] = {
                "workspace_id": workspace_id,
                "updated_at": time.time(),
            }
            self._save()

    def clear(self, conversation_id: str) -> None:
        if not conversation_id:
            return
        with self._lock:
            if self._data["contexts"].pop(conversation_id, None) is not None:
                self._save()

    def view(self, conversation_id: str) -> dict:
        return {
            "conversation_id": conversation_id,
            "workspace_id": self.workspace_id(conversation_id),
        }
