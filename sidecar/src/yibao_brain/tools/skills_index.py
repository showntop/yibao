"""技能索引（底座）：技能根目录扫描 / 解析 / 缓存，供 use_skill（底座展开）
与 skills 插件（管理：list/refresh/install/import）共享。

- 根目录：$YIBAO_SKILLS_DIR 优先 → data_dir()/skills
- 递归发现：所有含 SKILL.md 的目录（含嵌套集合，如 anthropics/skills 的
  slides/skills/pptx）→ 技能 id = skill:<相对路径>（段用 / 分隔）
- 模块级缓存 _INDEX：refresh_index() 重建；use_skill 与 skills.list 读同一份
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .. import config

_PROMPT_LIMIT = 64 * 1024  # SKILL.md + references 合计上限
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

_INDEX: dict[str, dict] = {}


def skills_root() -> Path:
    """技能根目录：$YIBAO_SKILLS_DIR 优先 → data_dir()/skills（每次读 env，避免缓存污染）。"""
    env = os.environ.get("YIBAO_SKILLS_DIR")
    return Path(env).expanduser() if env else (Path(config.data_dir()) / "skills")


def frontmatter(text: str) -> dict:
    """SKILL.md 头部 YAML frontmatter → dict（name/description/argument-hint…）。"""
    m = _FRONT.match(text)
    out: dict = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"\'')
    return out


def _collect_refs(skill_dir: Path) -> list[str]:
    refs = skill_dir / "references"
    return [] if not refs.is_dir() else [str(p.relative_to(skill_dir)) for p in sorted(refs.glob("*.md"))]


def _collect_scripts(skill_dir: Path) -> list[str]:
    scripts = skill_dir / "scripts"
    return [] if not scripts.is_dir() else [str(p.relative_to(skill_dir)) for p in sorted(scripts.glob("*")) if p.is_file()]


def scan(root: Path) -> dict[str, dict]:
    """递归扫描：所有含 SKILL.md 的目录 → 技能（id=skill:<相对路径>）。`_` 开头路径段跳过。"""
    skills: dict[str, dict] = {}
    if not root.is_dir():
        return skills
    for md in sorted(root.rglob("SKILL.md")):
        skill_dir = md.parent
        if any(part.startswith("_") for part in skill_dir.relative_to(root).parts):
            continue  # _staging/ 等暂存段跳过
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = frontmatter(text)
        rel = skill_dir.relative_to(root).as_posix()  # "ppt" 或 "slides/skills/pptx"
        skills[f"skill:{rel}"] = {
            "path": skill_dir,
            "text": text,
            "name": fm.get("name") or skill_dir.name,
            "description": fm.get("description") or "",
            "refs": _collect_refs(skill_dir),
            "scripts": _collect_scripts(skill_dir),
        }
    return skills


def refresh_index(root: Path | None = None) -> dict[str, dict]:
    """重建缓存索引（skills.refresh / 启动时调用）。"""
    global _INDEX
    _INDEX = scan(root or skills_root())
    return _INDEX


def index() -> dict[str, dict]:
    return _INDEX


def resolve(name: str) -> tuple[str, dict] | None:
    """解析技能名：完整相对路径（"skill:slides/skills/pptx" 或裸路径）直接命中；
    短名（如 "pptx"）唯一命中（嵌套集合场景避免歧义）。"""
    clean = name[6:] if name.startswith("skill:") else name
    key = f"skill:{clean}"
    if key in _INDEX:
        return key, _INDEX[key]
    hits = [k for k, v in _INDEX.items() if v["path"].name == clean]
    if len(hits) == 1:
        return hits[0], _INDEX[hits[0]]
    return None


def build_body(entry: dict) -> str:
    """拼技能说明书全文（SKILL.md + references，限 _PROMPT_LIMIT）+ scripts 清单。
    use_skill 展开到主上下文 / skill_import 固化的共享文本。"""
    parts = [
        f"你正在使用外部技能「{entry['name']}」。严格按下面的技能说明完成用户任务。",
        "=== SKILL.md ===",
        entry["text"],
    ]
    budget = len(entry["text"])
    for ref in entry["refs"]:
        try:
            body = (entry["path"] / ref).read_text(encoding="utf-8")
        except OSError:
            continue
        if budget + len(body) > _PROMPT_LIMIT:
            parts.append(f"[references/{ref} 内容过大未随附，如需请用读取工具获取]")
            continue
        budget += len(body)
        parts.append(f"=== references/{ref} ===")
        parts.append(body)
    if entry["scripts"]:
        parts.append("=== scripts/ 清单（需要跑脚本时用沙箱执行工具 code_exec，按说明传入脚本与参数）===")
        parts.append("\n".join(f"- {s}" for s in entry["scripts"]))
    return "\n\n".join(parts)
