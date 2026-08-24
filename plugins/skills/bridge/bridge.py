"""外部技能桥（skill bridge）：SKILL.md 开放技能库（Skill Hub / GitHub / 本地目录）的管理入口。

架构（capability-unified-design spec §D Skill 线 + 2026-08-23 use_skill 重构）：
- **展开由底座 use_skill 负责**（与 use_plugin 对称）：use_skill 把 SKILL.md + references
  说明书注入主上下文，agent 在工具循环里读说明、调 code_exec 等完成——不再单轮执行；
- **本插件只做技能库管理**：skills.list（清单）/ skills.refresh（重扫热加载）/
  skills.install（git clone 或本地复制）/ skills.import（固化为声明式插件）；
- 技能索引（扫描/frontmatter/resolve/build_body）共享底座 skills_index 模块。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools.skills_index import build_body, frontmatter, index, refresh_index, resolve
from yibao_brain.tools import Tool

_CLONE_TIMEOUT = 300  # git clone 超时（秒）
_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG.sub("-", (name or "").lower()).strip("-").strip()
    return s or "skill"


# ---------- 工具 ----------


class SkillsListTool(Tool):
    id = "skills.list"
    label = "列出外部技能"
    default_risk = RiskLevel.L0_READONLY
    description = (
        "列出技能根目录里可用的 SKILL.md 外部技能（id + 用途）。"
        "用户提到某类技能（PPT/设计/写作…）或想装新技能时先调本工具。"
    )

    def run(self, params: dict, ctx: Any) -> ActionResult:
        idx = index()
        rows = [{"id": k, "name": v["name"], "description": v["description"]} for k, v in sorted(idx.items())]
        human = "还没有加载任何外部技能" if not rows else \
            "可用外部技能：\n" + "\n".join(f"- {r['id']}（{r['name']}）：{r['description']}" for r in rows)
        return ActionResult(success=True, data={"skills": rows, "human": human})


class SkillsRefreshTool(Tool):
    id = "skills.refresh"
    label = "刷新外部技能"
    default_risk = RiskLevel.L0_READONLY
    description = (
        "重扫技能根目录，热加载新放入/新安装的 SKILL.md 技能（无需重启 sidecar）。"
        "手动放了技能目录或装完新技能后调用。"
    )

    def run(self, params: dict, ctx: Any) -> ActionResult:
        idx = refresh_index()
        return ActionResult(
            success=True,
            data={"skills": sorted(idx),
                  "human": f"已刷新技能库，当前 {len(idx)} 个技能：{', '.join(sorted(idx)) or '无'}"},
        )


class SkillsInstallTool(Tool):
    id = "skills.install"
    label = "安装外部技能"
    default_risk = RiskLevel.L3_HIGH
    description = (
        "从 GitHub（仓库 URL 或仓库内子路径，如 https://github.com/anthropics/skills "
        "或 .../tree/main/skills/slides）或本地目录路径安装 SKILL.md 技能到技能根目录，装完自动刷新。"
    )

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "GitHub 仓库 URL / 仓库内子路径，或本地技能目录绝对路径",
                        },
                        "name": {
                            "type": "string",
                            "description": "可选：安装到技能根目录后的目录名（缺省取仓库名/子路径末段）",
                        },
                    },
                    "required": ["url"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        from yibao_brain.tools.skills_index import skills_root

        url = str(params.get("url") or "").strip()
        name = str(params.get("name") or "").strip()
        root = skills_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return ActionResult(success=False, error=f"无法创建技能目录 {root}：{e}")
        if not url:
            return ActionResult(success=False, error="缺参数：url（GitHub 仓库或本地路径）")
        local = Path(url).expanduser()
        try:
            if local.is_dir():  # 本地目录复制
                target = root / (name or _slugify(local.name))
                shutil.copytree(local, target, dirs_exist_ok=True)
                installed = str(target.relative_to(root))
            else:
                installed = _git_install(url, name, root)
        except Exception as e:
            return ActionResult(success=False, error=f"安装失败：{e}")
        idx = refresh_index()
        return ActionResult(
            success=True,
            data={"path": installed, "skills": sorted(idx),
                  "human": f"已安装到 {installed}；技能库现有 {len(idx)} 个技能：{', '.join(sorted(idx)) or '无'}"},
        )


# ---------- 安装支撑 ----------


def _run_git(args: list[str]) -> None:
    subprocess.run(["git", *args], capture_output=True, text=True, timeout=_CLONE_TIMEOUT, check=True)


def _git_install(url: str, name: str, root: Path) -> str:
    """git clone 安装：支持仓库根 URL 与 /tree/<branch>/<sub> 子路径；集合目录（子路径含
    子技能）也接受——scan 会递归发现。"""
    if not (url.startswith("https://") or url.startswith("git@")):
        raise ValueError("仅支持 https:// GitHub URL 或本地目录路径")
    sub: str | None = None
    m = re.match(r"^(https://[^/]+/[^/]+/[^/]+?)(?:/tree/[^/]+/(.*))?$", url.rstrip("/"))
    if m:
        repo_url = m.group(1)
        sub = m.group(2)
    else:
        repo_url = url
    default_name = Path(sub).name if sub else repo_url.rstrip("/").rsplit("/", 1)[-1]
    target = root / (name or _slugify(default_name))
    if sub:
        with tempfile.TemporaryDirectory() as td:
            _run_git(["clone", "--depth", "1", repo_url, td])
            src = Path(td) / sub
            # 集合容错：子路径下递归存在 SKILL.md 即可（技能或技能集合，scan 递归发现）
            if not any(src.rglob("SKILL.md")):
                raise ValueError(f"子路径 {sub} 下没有 SKILL.md 技能（或其集合）")
            shutil.copytree(src, target, dirs_exist_ok=True)
    else:
        _run_git(["clone", "--depth", "1", repo_url, str(target)])
    return str(target.relative_to(root))


# ---------- 固化（skill_import：SKILL.md → 声明式插件） ----------


def _plugins_dir() -> Path:
    env = os.environ.get("YIBAO_PLUGINS_DIR")
    if env:
        return Path(env).expanduser()
    # bridge.py → plugins/skills/bridge/ → 上两级即 <repo>/plugins
    return Path(__file__).resolve().parents[2]


def _plugin_slug(name: str) -> str:
    """插件 id 规则：^[A-Za-z_][A-Za-z0-9_]*$（连字符不允许，桥接名含 - 时转 _）。"""
    s = _slugify(name).replace("-", "_")
    return s if re.match(r"^[A-Za-z_]", s) else f"skill_{s}"


def _manifest_for(name: str, description: str, body: str) -> str:
    """生成声明式插件 manifest：prompt 类型 tool 承载 SKILL.md 正文 + references（快照化）。"""
    return (
        "# 由 skills.import 从 SKILL.md 固化生成（capability-unified-design spec §P1）。\n"
        "# 固化为正式插件后：省每轮上下文、可配面板与 api.toml；上游更新需重新固化。\n"
        f'id = {json.dumps(_plugin_slug(name))}\n'
        f'name = {json.dumps(name)}\n'
        f'description = {json.dumps(description)}\n'
        'capabilities = ["llm"]\n'
        "\n"
        "[[tool]]\n"
        'id = "run"\n'
        'type = "prompt"\n'
        f'description = {json.dumps(description)}\n'
        'risk = "L1"\n'
        "[tool.prompt.template]\n"
        f"text = {json.dumps(body)}\n"
    )


class SkillsImportTool(Tool):
    id = "skills.import"
    label = "固化外部技能"
    default_risk = RiskLevel.L3_HIGH
    description = (
        "把已桥接的外部技能固化为正式插件（声明式 prompt 插件落盘 plugins/ 目录）："
        "省每轮上下文、可配面板。固化后调用 capability_refresh 或重启生效；"
        "同一来源固化后，use_skill 的桥条目停用（单形态激活）。"
    )

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "要固化的技能 id（skills.list 返回的 skill:xxx，也接受裸名）",
                        },
                        "name": {
                            "type": "string",
                            "description": "可选：固化后的插件名（缺省取技能名）",
                        },
                    },
                    "required": ["skill"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        from yibao_brain.tools.skills_index import deactivate

        name_arg = str(params.get("skill") or "").strip()
        hit = resolve(name_arg)
        if hit is None:
            return ActionResult(
                success=False,
                error=f"没有这个技能：{name_arg or '(空)'}（可用：{', '.join(index()) or '无'}）",
            )
        key, entry = hit
        name = str(params.get("name") or "").strip() or entry["name"]
        slug = _plugin_slug(name)
        plugins_root = _plugins_dir()
        target = plugins_root / slug
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "manifest.toml").write_text(
                _manifest_for(name, entry["description"], build_body(entry)), encoding="utf-8"
            )
        except Exception as e:
            return ActionResult(success=False, error=f"固化失败：{e}")
        # 单形态激活（spec §G.3）：固化后桥条目停用，同一来源只激活插件形态
        deactivate(key)  # 根目录/插件包内两注册表都摘
        return ActionResult(
            success=True,
            data={
                "path": str(target.relative_to(plugins_root)),
                "key": key,
                "human": f"已固化「{entry['name']}」为插件 {slug}（{target}）。"
                         f"桥条目 {key} 已停用（单形态激活）；调用 capability_refresh 或重启后插件生效。",
            },
        )


def make_tools(ctx: Any) -> list[Tool]:
    return [SkillsListTool(), SkillsRefreshTool(), SkillsInstallTool(), SkillsImportTool()]
