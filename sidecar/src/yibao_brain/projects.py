"""ProjectStore：Workspace 的兼容 façade（2026-09 Agent OS Work Graph）。

存在 WorkGraphStore 时，Workspace/Mission/Artifact/WorkflowRun 的权威数据来自
work_graph.db；projects.json 只保留旧版本可读的名称/目录锚点和一次迁移来源，
新对象关系不再双写回 JSON。未注入 WorkGraphStore 的测试/旧调用仍走 V1a JSON。

写失败抛异常给调用方（项目是主链路，与 Feed 的"只 print"不同）；
读损坏时降级为空库并保留坏文件（.bak），不静默丢数据。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

from .config import data_dir, load_settings, save_settings
from .log import log

# 立项时自动创建的目录骨架（视频文档 §8.1）
SKELETON_DIRS = ("01_素材", "02_工程", "03_导出", "04_文档")


def _slug(name: str) -> str:
    """目录名：保留中英文数字，路径不友好字符折成 -；空则回退随机段。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", name).strip("-.")
    return s or f"project-{uuid.uuid4().hex[:6]}"


class ProjectStore:
    def __init__(self, path: str, session_contexts=None, work_graph=None):
        self._path = path
        self._session_contexts = session_contexts
        self._work_graph = work_graph
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()
        if self._work_graph is not None:
            self._work_graph.migrate_projects(self._data["projects"])

    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("projects"), list):
                return raw
            raise ValueError("projects.json 结构非法")
        except FileNotFoundError:
            return {"projects": []}
        except Exception as e:
            # 损坏不覆盖：留 .bak 供人工恢复，本次起空库
            try:
                os.replace(self._path, self._path + ".bak")
            except OSError:
                pass
            log(f"projects.json 损坏（已备份 .bak，起空库）：{e}")
            return {"projects": []}

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path)

    # ---------- 查询 ----------

    def list(self) -> list[dict]:
        if self._work_graph is not None:
            return self._work_graph.list_workspace_views()
        with self._lock:
            return [dict(p) for p in self._data["projects"]]

    def get(self, pid: str) -> dict | None:
        if self._work_graph is not None:
            return self._work_graph.workspace_view(pid)
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    return dict(p)
        return None

    def find_by_name(self, name: str) -> dict | None:
        if self._work_graph is not None:
            for workspace in self._work_graph.list_workspace_views():
                if workspace["name"] == name:
                    return workspace
            return None
        with self._lock:
            for p in self._data["projects"]:
                if p["name"] == name:
                    return dict(p)
        return None

    def current_id(self, conversation_id: str = "") -> str:
        if conversation_id and self._session_contexts is not None:
            pid = self._session_contexts.workspace_id(conversation_id)
            return pid if pid and self.get(pid) is not None else ""
        return str(load_settings().get("current_project_id") or "")

    def current(self, conversation_id: str = "") -> dict | None:
        cid = self.current_id(conversation_id)
        return self.get(cid) if cid else None

    # ---------- 变更 ----------

    def create(
        self, name: str, objects: list[dict] | None = None, conversation_id: str = "",
        mission_title: str | None = None,
    ) -> dict:
        """立项：建 Workspace/Mission/WorkflowRun + 目录骨架，并绑定当前 Session。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名不能为空")
        if self._work_graph is not None and self.find_by_name(name) is not None:
            raise ValueError(f"项目名已存在：{name}")
        with self._lock:
            if any(p["name"] == name for p in self._data["projects"]):
                raise ValueError(f"项目名已存在：{name}")
            pid = f"proj_{uuid.uuid4().hex[:12]}"
            base = os.path.join(data_dir(), "projects")
            dirname = _slug(name)
            path = os.path.join(base, dirname)
            if os.path.exists(path):
                path = os.path.join(base, f"{dirname}-{pid[5:9]}")
            for sub in SKELETON_DIRS:
                os.makedirs(os.path.join(path, sub), exist_ok=True)
            now = time.time()
            proj = {
                "id": pid,
                "name": name,
                "created_at": now,
                "touched_at": now,
                "dir": path,
                # Work Graph 模式下关系只写 SQLite；JSON 不再承担对象权威。
                "objects": [] if self._work_graph is not None else list(objects or []),
            }
            self._data["projects"].append(proj)
            self._save()
        if self._work_graph is not None:
            try:
                self._work_graph.create_workspace(
                    pid, name, path, created_at=now, objects=list(objects or []),
                    mission_title=mission_title,
                )
            except Exception:
                # SQLite 事务已回滚；兼容 JSON 也撤回，避免出现只有半边的 Workspace。
                with self._lock:
                    self._data["projects"] = [p for p in self._data["projects"] if p["id"] != pid]
                    self._save()
                raise
        self.switch(pid, conversation_id)
        return self.get(pid) or dict(proj)

    def switch(self, pid: str, conversation_id: str = "") -> bool:
        """切换工作语境：有 conversation_id 时只绑定该 Session；旧调用回退全局 setting。"""
        if self.get(pid) is None:
            return False
        if conversation_id and self._session_contexts is not None:
            self._session_contexts.bind(conversation_id, pid)
        else:
            save_settings({"current_project_id": pid})
        self.touch(pid)
        return True

    def touch(self, pid: str) -> None:
        if self._work_graph is not None:
            self._work_graph.touch_workspace(pid)
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    p["touched_at"] = time.time()
                    self._save()
                    return

    def add_object(self, pid: str, obj_type: str, ref: str) -> bool:
        """挂载引用（同一 type+ref 不重复挂）。"""
        if self._work_graph is not None:
            attached = self._work_graph.attach_external_artifact(pid, obj_type, ref)
            if attached is None:
                return False
            self._touch_legacy(pid)
            return True
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    entry = {"type": obj_type, "ref": ref}
                    if entry not in p["objects"]:
                        p["objects"].append(entry)
                        p["touched_at"] = time.time()
                        self._save()
                    return True
        return False

    def remove_object(self, pid: str, obj_type: str, ref: str) -> bool:
        if self._work_graph is not None:
            ok = self._work_graph.detach_external_artifact(pid, obj_type, ref)
            if ok:
                self._touch_legacy(pid)
            return ok
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    before = len(p["objects"])
                    p["objects"] = [
                        o for o in p["objects"]
                        if not (o.get("type") == obj_type and o.get("ref") == ref)
                    ]
                    if len(p["objects"]) != before:
                        p["touched_at"] = time.time()
                        self._save()
                    return True
        return False

    def _touch_legacy(self, pid: str) -> None:
        """兼容 JSON 只更新时间，不再写 objects。"""
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    p["touched_at"] = time.time()
                    self._save()
                    return

    # ---------- 展示 ----------

    def view(self, conversation_id: str = "") -> dict:
        """给前端/地平线的读模型：列表 + 当前 Session 的 Workspace id。"""
        cid = self.current_id(conversation_id)
        return {
            "current": cid,
            "conversation_id": conversation_id,
            "projects": sorted(
                (dict(p) for p in self.list()),
                key=lambda p: p.get("touched_at", 0),
                reverse=True,
            ),
        }
