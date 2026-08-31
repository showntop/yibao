"""ProjectStore：项目实体的底座存储（2026-08-30-project-entity-design.md）。

项目 = 一组引用的聚合 + 一个名字，视图不是容器：对象只存 {type, ref} 指针，
实体内容仍在各自域（zimeiti 选题、文件等）里，这里不搬数据。

存储：data_dir()/projects.json，原子写（tmp + replace）。当前项目 id 在
settings.json 的 current_project_id（config 层已知键）。项目目录骨架
（01_素材/02_工程/03_导出/04_文档）在 create 时落盘，dir 存绝对路径。

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
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._data = self._load()

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
        with self._lock:
            return [dict(p) for p in self._data["projects"]]

    def get(self, pid: str) -> dict | None:
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    return dict(p)
        return None

    def find_by_name(self, name: str) -> dict | None:
        with self._lock:
            for p in self._data["projects"]:
                if p["name"] == name:
                    return dict(p)
        return None

    def current_id(self) -> str:
        return str(load_settings().get("current_project_id") or "")

    def current(self) -> dict | None:
        cid = self.current_id()
        return self.get(cid) if cid else None

    # ---------- 变更 ----------

    def create(self, name: str, objects: list[dict] | None = None) -> dict:
        """立项：建实体 + 落目录骨架 + 切为当前项目。name 唯一（重复名报错）。"""
        name = (name or "").strip()
        if not name:
            raise ValueError("项目名不能为空")
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
                "objects": list(objects or []),
            }
            self._data["projects"].append(proj)
            self._save()
        self.switch(pid)
        return dict(proj)

    def switch(self, pid: str) -> bool:
        """切换当前项目（settings.json 的 current_project_id）。"""
        if self.get(pid) is None:
            return False
        save_settings({"current_project_id": pid})
        self.touch(pid)
        return True

    def touch(self, pid: str) -> None:
        with self._lock:
            for p in self._data["projects"]:
                if p["id"] == pid:
                    p["touched_at"] = time.time()
                    self._save()
                    return

    def add_object(self, pid: str, obj_type: str, ref: str) -> bool:
        """挂载引用（同一 type+ref 不重复挂）。"""
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

    # ---------- 展示 ----------

    def view(self) -> dict:
        """给前端/地平线的读模型：列表 + 当前 id。"""
        cid = self.current_id()
        return {
            "current": cid,
            "projects": sorted(
                (dict(p) for p in self.list()),
                key=lambda p: p.get("touched_at", 0),
                reverse=True,
            ),
        }
