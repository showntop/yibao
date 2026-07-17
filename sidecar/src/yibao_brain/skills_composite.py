"""复合技能：把原子能力编排成用户看得懂的场景（找文件/搜索/打开/写东西）。

实现原则：确定性优先 —— 能走 CLI（open/mdfind）就不点像素，能走 AX 设值就不模拟键入。
find_file/web_search/open_path 不依赖 host（直接 subprocess）；write_note 依赖 host（AX/键入）。
"""
from __future__ import annotations

import subprocess
import time
import urllib.parse
from pathlib import Path

from .config import search_engine
from .ipc import ActionResult, RiskLevel
from .skills import Skill, SkillContext, SkillRegistry

_SEARCH_ENGINES = {
    "baidu": "https://www.baidu.com/s?wd=",
    "bing": "https://www.bing.com/search?q=",
    "google": "https://www.google.com/search?q=",
}


def _run_argv(argv: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


class FindFileSkill(Skill):
    id = "find_file"
    description = "在本机全盘搜索文件（Spotlight），按文件名或内容关键词匹配，返回最相关的前 10 个路径。配合 open_path 打开结果。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "文件名或内容关键词"}},
                "required": ["query"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ActionResult(success=False, error="缺少 query 参数")
        try:
            cp = _run_argv(["mdfind", query])
        except Exception as e:
            return ActionResult(success=False, error=f"mdfind 失败：{e}")
        paths = [p.strip() for p in (cp.stdout or "").splitlines() if p.strip()][:10]
        return ActionResult(success=True, data={"paths": paths, "count": len(paths)})


class WebSearchSkill(Skill):
    id = "web_search"
    description = "用系统默认浏览器打开搜索引擎查询关键词（结果页交给人看，或再用 read_tree/screenshot 读取内容）。"
    default_risk = RiskLevel.L1_LOW

    def __init__(self, engine: str | None = None):
        self._engine = engine  # None → 运行时读 config（YIBAO_SEARCH_ENGINE）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词"}},
                "required": ["query"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ActionResult(success=False, error="缺少 query 参数")
        engine = self._engine or search_engine()
        base = _SEARCH_ENGINES.get(engine, _SEARCH_ENGINES["baidu"])
        url = base + urllib.parse.quote(query)
        try:
            _run_argv(["open", url])
        except Exception as e:
            return ActionResult(success=False, error=f"打开浏览器失败：{e}")
        return ActionResult(success=True, data={"engine": engine, "url": url})


class OpenPathSkill(Skill):
    id = "open_path"
    description = "用默认应用打开一个本地文件/目录；reveal=true 时改为在 Finder 中定位显示该文件。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "绝对路径"},
                    "reveal": {"type": "boolean", "default": False, "description": "true 时在 Finder 中定位而非打开"},
                },
                "required": ["path"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        path = str(params.get("path", "")).strip()
        if not path:
            return ActionResult(success=False, error="缺少 path 参数")
        if not Path(path).exists():
            return ActionResult(success=False, error=f"路径不存在：{path}")
        reveal = bool(params.get("reveal"))
        argv = ["open", "-R", path] if reveal else ["open", path]
        try:
            cp = _run_argv(argv)
        except Exception as e:
            return ActionResult(success=False, error=f"open 失败：{e}")
        if cp.returncode != 0:
            return ActionResult(success=False, error=f"open 退出码 {cp.returncode}")
        return ActionResult(success=True, data={"path": path, "reveal": reveal})


class WriteNoteSkill(Skill):
    id = "write_note"
    description = "打开文本编辑应用（默认 TextEdit）并写入一段文字（新建草稿，不落盘）。适合起草、记录、写文案。"
    default_risk = RiskLevel.L2_MEDIUM

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要写入的文字"},
                    "app": {"type": "string", "default": "TextEdit", "description": "目标编辑器应用名"},
                },
                "required": ["text"],
            },
        }

    def run(self, params: dict, ctx: SkillContext) -> ActionResult:
        if ctx.host is None:
            return ActionResult(success=False, error="无执行基座 host（ctx.host 为空）")
        text = str(params.get("text", ""))
        if not text.strip():
            return ActionResult(success=False, error="缺少 text 参数")
        app = str(params.get("app", "TextEdit")).strip() or "TextEdit"
        pid = ctx.host.a11y.launch_app(app)
        if pid is None:
            return ActionResult(success=False, error=f"无法打开应用：{app}")
        time.sleep(1.0)  # 等应用起窗
        handle = ctx.host.a11y.find(role="AXTextArea")
        if handle is not None and ctx.host.a11y.set_value(handle, text):
            return ActionResult(success=True, data={"method": "ax", "app": app, "chars": len(text)})
        ctx.host.input.type_text(text)
        return ActionResult(success=True, data={"method": "type", "app": app, "chars": len(text)})


def register_composite_skills(reg: SkillRegistry) -> None:
    """把 4 个复合技能注册到 registry。"""
    for skill in (FindFileSkill(), WebSearchSkill(), OpenPathSkill(), WriteNoteSkill()):
        reg.register(skill)
