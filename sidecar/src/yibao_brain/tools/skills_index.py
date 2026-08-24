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

# 插件包内技能（spec §对象模型 bundled_skills）：pid → {skill_id: entry}。
# 独立注册表而非混进 _INDEX——refresh_index() 重建根目录扫描时会整表替换，
#  bundled 注册发生在插件加载期，两条生命周期不能互相覆盖。
_BUNDLED: dict[str, dict[str, dict]] = {}


def _scan_skills_dir(skills_dir: Path, key_of) -> dict[str, dict]:
    """扫一个技能目录（含 SKILL.md 的子目录树）→ {skill_id: entry}；key_of(rel) 定 id。"""
    skills: dict[str, dict] = {}
    if not skills_dir.is_dir():
        return skills
    for md in sorted(skills_dir.rglob("SKILL.md")):
        skill_dir = md.parent
        rel = skill_dir.relative_to(skills_dir).as_posix()
        if rel == "." or any(part.startswith("_") for part in skill_dir.relative_to(skills_dir).parts):
            continue  # 根层直放 SKILL.md 不收（无目录名可命名）；_staging/ 等暂存段跳过
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = frontmatter(text)
        skills[key_of(rel)] = {
            "path": skill_dir,
            "text": text,
            "name": fm.get("name") or skill_dir.name,
            "description": fm.get("description") or "",
            "refs": _collect_refs(skill_dir),
            "scripts": _collect_scripts(skill_dir),
        }
    return skills


def register_bundled(pid: str, plugin_dir) -> list[str]:
    """扫插件包内 skills/**/SKILL.md，以 <pid>:<rel> 命名空间注册（插件自带技能）。
    返回注册的 skill id 列表（台账 bundled_skills 用）；插件目录无 skills/ 则登记空集。"""
    found = _scan_skills_dir(Path(plugin_dir) / "skills", lambda rel: f"{pid}:{rel}")
    _BUNDLED[pid] = found
    return list(found)


def unregister_bundled(pid: str) -> None:
    _BUNDLED.pop(pid, None)


def bundled_for(pid: str) -> list[str]:
    return sorted(_BUNDLED.get(pid, {}))


def deactivate(key: str) -> None:
    """单形态激活（skill_import 固化后桥条目停用）：从所在注册表摘除该技能条目。"""
    _INDEX.pop(key, None)
    for entries in _BUNDLED.values():
        entries.pop(key, None)


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
    return _scan_skills_dir(root, lambda rel: f"skill:{rel}")


def refresh_index(root: Path | None = None) -> dict[str, dict]:
    """重建根目录扫描缓存（skills.refresh / 启动时调用）；返回合并 bundled 后的全量视图。"""
    global _INDEX
    _INDEX = scan(root or skills_root())
    return index()


def index() -> dict[str, dict]:
    """全量技能视图：根目录扫描（skill:* 命名空间）+ 插件包内（<pid>:* 命名空间）。"""
    out = dict(_INDEX)
    for entries in _BUNDLED.values():
        out.update(entries)
    return out


def resolve(name: str) -> tuple[str, dict] | None:
    """解析技能名：完整 id（"skill:slides/skills/pptx" / 裸路径 / owner 前缀 "zimeiti:write"）
    直接命中；短名（如 "pptx" / "write"）唯一命中（spec：owner 可省略，多命中报歧义即不命中）。"""
    idx = index()
    clean = name[6:] if name.startswith("skill:") else name
    key = f"skill:{clean}"
    if key in idx:
        return key, idx[key]
    if name in idx:  # owner 前缀完整 id（插件自带技能）
        return name, idx[name]
    hits = [k for k, v in idx.items() if v["path"].name == clean]
    if len(hits) == 1:
        return hits[0], idx[hits[0]]
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
