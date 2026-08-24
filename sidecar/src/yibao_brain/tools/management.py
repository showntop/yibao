"""能力来源管理（spec B/E 收尾）：SourceRecord 台账 + SourceStore 持久化 + SourceManager 统一接口。

分层：
- SourceRecord：台账行（登记单位）——plugin / mcp.<server> / skill:<name> / core 组
- SourceStore：持久化（data_dir()/sources.json）+ 重启对账（disabled 状态跨重启保留）
- SourceManager：统一生命周期接口 discover / install / uninstall / update / status
  - PluginManager：包装 plugins.py（增量加载 / unload_plugin）+ 插件目录操作
  - SkillManager：技能根目录扫描 / 安装 / 卸载 / 更新
  - McpManager（mcp.py）：连接生命周期已实现，本模块负责对齐接口（discover/update）

台账语义（spec §对象模型）：登记单位 = 来源，展开单位 = 工具；status 只作
active/disabled/error；privileged 不可卸、不可 disable。
"""
from __future__ import annotations

import json
import os
import shutil
import tomllib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from .core import ToolRegistry


def skills_root() -> Path:
    """技能根目录：$YIBAO_SKILLS_DIR 优先 → data_dir()/skills（与 skills 桥插件一致）。"""
    env = os.environ.get("YIBAO_SKILLS_DIR")
    return Path(env).expanduser() if env else (Path(config.data_dir()) / "skills")


# ---------- 台账行 ----------


@dataclass
class SourceRecord:
    """台账行：一个登记单位（来源）及其展开的工具。"""

    id: str
    source_type: str  # core | plugin | mcp | skill
    status: str = "active"  # active | disabled | error
    privileged: bool = False
    source: dict = field(default_factory=dict)  # {path|server|url, installed_at}
    tools: list[str] = field(default_factory=list)
    bundled_skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceRecord":
        keys = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in keys})


# ---------- 持久化 ----------


class SourceStore:
    """台账持久化（JSON）。重启后 discover 重建记录，存储里的 status（如 disabled）合并回来。"""

    def __init__(self, path: str | Path) -> None:
        self._file = Path(path)

    def load(self) -> dict[str, dict]:
        if not self._file.is_file():
            return {}
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, records: dict[str, SourceRecord]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps({k: v.to_dict() for k, v in records.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def merge_status(self, discovered: dict[str, SourceRecord]) -> dict[str, SourceRecord]:
        """对账：discover 结果为准，存储里的 status（disabled）合并回同 id 记录。"""
        stored = self.load()
        for rid, rec in discovered.items():
            saved = stored.get(rid)
            if saved and saved.get("status") in ("disabled", "error"):
                rec.status = saved["status"]
        return discovered


# ---------- 统一接口 ----------


class SourceManager(ABC):
    """来源管理器统一接口：discover / install / uninstall / update / status。"""

    source_type: str = ""

    @abstractmethod
    def discover(self) -> list[SourceRecord]: ...

    @abstractmethod
    def install(self, source: str, name: str | None = None) -> SourceRecord: ...

    @abstractmethod
    def uninstall(self, rid: str) -> int: ...

    @abstractmethod
    def update(self, rid: str) -> str: ...

    @abstractmethod
    def status(self, rid: str) -> str: ...


class PluginManager(SourceManager):
    """插件来源：目录扫描 + 增量加载 + 卸载（privileged 保护由 ledger 负责）。"""

    source_type = "plugin"

    def __init__(self, registry: ToolRegistry, plugins_dir: str | Path,
                 loader: Any = None) -> None:
        self._reg = registry
        self._plugins_dir = Path(plugins_dir)
        self._loader = loader  # callable(existing=None) -> dict[pid, status]；None=不自动加载

    def _read_manifest(self, child: Path) -> tuple[str, bool]:
        try:
            m = tomllib.loads((child / "manifest.toml").read_text(encoding="utf-8"))
            return str(m.get("id", child.name)), bool(m.get("privileged", False))
        except (OSError, tomllib.TOMLDecodeError):
            return child.name, False

    def discover(self) -> list[SourceRecord]:
        from ..tools import skills_index

        out: list[SourceRecord] = []
        if not self._plugins_dir.is_dir():
            return out
        for child in sorted(self._plugins_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if not (child / "manifest.toml").is_file():
                continue
            pid, privileged = self._read_manifest(child)
            out.append(SourceRecord(
                id=pid, source_type="plugin",
                source={"path": str(child), "installed_at": int(child.stat().st_mtime)},
                privileged=privileged,
                tools=list(self._reg.plugin_tools().get(pid, [])),
                bundled_skills=skills_index.bundled_for(pid),
            ))
        return out

    def install(self, source: str, name: str | None = None) -> SourceRecord:
        src = Path(source).expanduser()
        if not src.is_dir():
            raise ValueError(f"插件目录不存在：{src}")
        target = self._plugins_dir / (name or src.name)
        shutil.copytree(src, target, dirs_exist_ok=True)
        if self._loader is not None:
            results = self._loader(existing=self._reg.plugin_ids())
            pid = target.name
            if results.get(pid) not in ("ok", None) and pid in results:
                raise ValueError(f"插件加载失败：{results[pid]}")
        pid, privileged = self._read_manifest(target)
        return SourceRecord(id=pid, source_type="plugin",
                            source={"path": str(target)},
                            privileged=privileged)

    def uninstall(self, rid: str) -> int:
        from ..plugins import unload_plugin

        removed = unload_plugin(self._reg, rid)
        target = self._plugins_dir / rid
        if target.is_dir():
            shutil.rmtree(target)
        return removed

    def update(self, rid: str) -> str:
        if self._loader is None:
            return "（无加载器，需重启生效）"
        self._loader(existing=set())  # 增量跳过已注册，等价 reload
        return "ok"

    def status(self, rid: str) -> str:
        target = self._plugins_dir / rid
        return "active" if (target / "manifest.toml").is_file() else "missing"


class SkillManager(SourceManager):
    """技能来源：技能根目录扫描 / 安装 / 卸载 / 更新（纯文件操作，与桥插件解耦）。"""

    source_type = "skill"

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else skills_root()

    def discover(self) -> list[SourceRecord]:
        out: list[SourceRecord] = []
        if not self._root.is_dir():
            return out
        for child in sorted(self._root.iterdir()):
            if not child.is_dir() or child.name.startswith("_"):
                continue
            if not (child / "SKILL.md").is_file():
                continue
            out.append(SourceRecord(
                id=f"skill:{child.name}", source_type="skill",
                source={"path": str(child), "installed_at": int(child.stat().st_mtime)},
            ))
        return out

    def install(self, source: str, name: str | None = None) -> SourceRecord:
        src = Path(source).expanduser()
        if not (src / "SKILL.md").is_file():
            raise ValueError(f"技能目录需含 SKILL.md：{src}")
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / (name or src.name)
        shutil.copytree(src, target, dirs_exist_ok=True)
        return SourceRecord(id=f"skill:{target.name}", source_type="skill",
                            source={"path": str(target)})

    def uninstall(self, rid: str) -> int:
        name = rid[6:] if rid.startswith("skill:") else rid
        target = self._root / name
        if not target.is_dir():
            return 0
        shutil.rmtree(target)
        return 1

    def update(self, rid: str) -> str:
        return "（技能无版本概念；重新 skills.install 覆盖即更新）"

    def status(self, rid: str) -> str:
        name = rid[6:] if rid.startswith("skill:") else rid
        return "active" if (self._root / name / "SKILL.md").is_file() else "missing"
