"""LLM 生成 webview 面板（gen 面板）：panel_gen/open/list/delete 四个底座技能。

生成契约：单个完整 HTML、全内联（禁外链/网络请求，前端另有 CSP 注入兜底）、中文 UI、
视觉沿用插件面板设计 token。落盘 data_dir()/gen_panels/<slug>.html + <slug>.meta.json，
注册进面板注册表（ref = 「gen:<slug>」），走既有 panel 事件链路（panel_payload）到前端
iframe srcdoc 渲染——本模块不碰事件链路与前端。

gen 面板无 invoke 能力（v1 刻意）：前端桥要求 method 以面板 pid 开头，而 sidecar 没有
gen 的 api.toml 白名单，双保险拒掉任何调用——面板纯展示，数据只能由调用方经 onInit 推入。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from . import config
from .ipc import ActionResult, RiskLevel
from .plugins import register_panel, unregister_panel
from .skills import Skill

PANEL_PID = "gen"  # gen 面板固定 pid（前端 invoke 粗筛 + 无 api 白名单，天然无调用能力）

_SLUG = re.compile(r"^[a-z0-9-]{1,40}$")

# 外链/网络请求检测（大小写不敏感）：命中即要求 LLM 重写，再命中拒绝注册
_EXTERNAL_PATTERNS = [
    (r"<script[^>]*\ssrc\s*=", "script 外链（<script src=…>）"),
    (r"<link\b", "资源外链（<link …>）"),
    (r"src\s*=\s*[\"']https?://", "src 引用 http 资源"),
    (r"href\s*=\s*[\"']https?://", "href 引用 http 资源"),
    (r"\bfetch\s*\(", "fetch( 网络请求"),
    (r"XMLHttpRequest", "XMLHttpRequest 网络请求"),
    (r"WebSocket", "WebSocket 网络连接"),
    (r"\bimport\s*\(", "import( 动态导入"),
    (r"url\(\s*[\"']?https?://", "CSS url(http…) 外链"),
]

# 设计 token：照抄 plugins/zimeiti/panel/editor.html 的 :root 变量（沙箱内吃不到 tokens.css，
# 生成面板与插件面板同一视觉语言——米白底、卡片圆角、橙色主色）
_DESIGN_TOKENS = """\
:root {
  --bg: #f6f1ea; --card: #ffffff; --border: #eee4d6; --border-soft: #f3ecdf;
  --text: #3f372e; --muted: #a89a86; --faint: #c9bcab;
  --accent: #ff8a5c; --accent-deep: #f2703f; --accent-soft: #fff0e8;
  --green-bg: #dcf0e2; --green: #3e8e5a; --green-bar: #8fd0a6;
  --red-bg: #fce7e3; --red: #c0574b; --red-bar: #f0aaa1;
}
body { font: 13px/1.6 -apple-system, "PingFang SC", sans-serif; background: var(--bg); color: var(--text); }"""


def _panels_dir() -> Path:
    return Path(config.data_dir()) / "gen_panels"


def _slugify(name: str) -> str:
    """LLM 传的 name 规整成 slug：小写、非字母数字转 -、压连续 -、截 40；空了给 panel。"""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")[:40].rstrip("-")
    return s or "panel"


def _find_external_refs(html: str) -> list[str]:
    """扫描 HTML 里的外链/网络请求痕迹，返回命中描述列表（空 = 干净）。"""
    return [desc for pat, desc in _EXTERNAL_PATTERNS if re.search(pat, html, re.IGNORECASE)]


def _unwrap(text: str) -> str:
    """剥代码围栏与正文外的解释文字：尽量截取 <!DOCTYPE/<html 起到 </html> 止。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t).strip()
    low = t.lower()
    start = low.find("<!doctype")
    if start < 0:
        start = low.find("<html")
    end = low.rfind("</html>")
    if start >= 0 and end > start:
        return t[start : end + len("</html>")].strip()
    return t


def _build_prompt(title: str, purpose: str, has_data: bool) -> str:
    """生成契约 prompt：单文件全内联 HTML + 译宝视觉语言 + 可选 yibao.onInit 初始数据。"""
    parts = [
        "你是前端工程师，为桌面宠物「译宝」生成一个 webview 信息面板。",
        f"面板中文名（<title> 用它）：{title}",
        f"面板要做的事：{purpose}",
        (
            "硬性约束：\n"
            "1. 输出单个完整 HTML 文件（<!DOCTYPE html> 开始，</html> 结束）；\n"
            "2. 所有 CSS 写在 <style> 里、所有 JS 写在 <script> 里——禁止任何外链与网络请求："
            "不许 <script src>、<link>、fetch、XMLHttpRequest、WebSocket、动态 import()、"
            "CSS 的 url(http…)，也不许引用任何 CDN；图片/字体只允许 data: 内嵌；\n"
            "3. 界面文案全部用中文；\n"
            "4. 视觉沿用译宝设计 token（米白底、卡片圆角、橙色主色），"
            "把下面这组变量原样放进 :root 并基于它们写样式：\n" + _DESIGN_TOKENS
        ),
    ]
    if has_data:
        parts.append(
            "调用方会给面板一份初始 JSON 数据（结构按「面板要做的事」自行设计并渲染）。"
            "页面加载后父窗会推送数据，用这段代码接收：\n"
            "if (window.yibao && window.yibao.onInit) {\n"
            "  window.yibao.onInit(function (data) { /* 用 data 渲染界面 */ });\n"
            "}\n"
            "数据可能晚于页面脚本到达，先渲染空态，收到 data 再填充。"
        )
    parts.append("只输出 HTML 文件本身：不要解释、不要前后缀、不要代码围栏。")
    return "\n\n".join(parts)


def _retry_prompt(prompt: str, refs: list[str]) -> str:
    """外链命中后的重写 prompt：把命中项点名列给 LLM，要求全内联重出。"""
    hits = "\n".join(f"- {r}" for r in refs)
    return (
        prompt
        + "\n\n你上一版输出违反了约束（只允许内联代码/样式，不允许外链与网络请求），命中：\n"
        + hits
        + "\n去掉所有外链与网络请求，重新输出完整 HTML（仍然只输出 HTML 本身）。"
    )


def _read_meta(slug: str) -> dict:
    try:
        return json.loads((_panels_dir() / f"{slug}.meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(slug: str, title: str, purpose: str, html: str) -> None:
    d = _panels_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.html").write_text(html, encoding="utf-8")
    meta = {"title": title, "purpose": purpose, "created_at": int(time.time())}
    (d / f"{slug}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_panels() -> list[dict]:
    d = _panels_dir()
    out: list[dict] = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.html")):
        slug = f.stem
        if not _SLUG.match(slug):
            continue
        meta = _read_meta(slug)
        out.append({
            "name": slug,
            "title": meta.get("title") or slug,
            "purpose": meta.get("purpose") or "",
            "created_at": meta.get("created_at") or 0,
        })
    return out


def load_saved_panels() -> int:
    """启动恢复：扫 gen_panels/*.html 逐个注册（meta 缺了用 slug 当 title）。返回恢复数。"""
    n = 0
    for p in _list_panels():
        html = (_panels_dir() / f"{p['name']}.html").read_text(encoding="utf-8")
        register_panel(PANEL_PID, p["name"], f"译宝 · {p['title']}", html)
        n += 1
    return n


def make_skills(llm) -> list[Skill]:
    """底座注册口（server.build_loop 照 reminders 的方式用）：llm 需有 chat(prompt) -> str。"""
    return [PanelGenSkill(llm), PanelOpenSkill(), PanelListSkill(), PanelDeleteSkill()]


class PanelGenSkill(Skill):
    id = "panel_gen"
    label = "生成面板"
    description = (
        "生成并打开一个 webview 面板：用 purpose 描述要什么样的界面，LLM 现场生成"
        "（HTML 全内联、无网络）。用户说「做一个/生成一个 XX 面板/看板/小工具」时用；"
        "想改某个面板也用同名 name 再生成一次（覆盖旧的）。"
    )
    default_risk = RiskLevel.L1_LOW

    def __init__(self, llm) -> None:
        self._llm = llm

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "面板英文标识（小写字母/数字/连字符，如 weather-board）"},
                        "title": {"type": "string", "description": "面板中文名（显示在面板标题）"},
                        "purpose": {"type": "string", "description": "面板要做什么、长什么样，越具体越好"},
                        "data": {"type": "object", "description": "可选：面板初始数据（JSON object），面板经 yibao.onInit 接收"},
                    },
                    "required": ["name", "title", "purpose"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        if self._llm is None:
            return ActionResult(success=False, error="底座未提供 LLM 能力")
        title = str(params.get("title") or "").strip()
        purpose = str(params.get("purpose") or "").strip()
        if not title or not purpose:
            return ActionResult(success=False, error="缺参数：title（中文名）和 purpose（要做什么）都要给")
        slug = _slugify(str(params.get("name") or ""))
        raw_data = params.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}

        prompt = _build_prompt(title, purpose, bool(data))
        html = self._chat(prompt)
        if html is None:
            return ActionResult(success=False, error="LLM 生成面板失败，换个说法再试")
        refs = _find_external_refs(html)
        if refs:  # 命中外链约束：命中项喂回去让 LLM 重写一次
            html = self._chat(_retry_prompt(prompt, refs))
            if html is None:
                return ActionResult(success=False, error="LLM 生成面板失败，换个说法再试")
            refs = _find_external_refs(html)
        if refs:  # 再命中：拒绝注册
            return ActionResult(
                success=False,
                error="面板只允许内联代码/样式（不允许外链与网络请求），已拒绝：" + "、".join(refs),
            )

        _save(slug, title, purpose, html)
        register_panel(PANEL_PID, slug, f"译宝 · {title}", html)
        return ActionResult(success=True, panel=f"{PANEL_PID}:{slug}", data=data)

    def _chat(self, prompt: str) -> str | None:
        """chat + 剥围栏；LLM 异常/返回空统一归一成 None。"""
        try:
            return _unwrap(self._llm.chat(prompt)) or None
        except Exception:
            return None


class PanelOpenSkill(Skill):
    id = "panel_open"
    label = "打开面板"
    description = "重新打开一个之前生成过的面板（不知道有哪些时先 panel_list）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "面板标识（panel_list 返回的 name）"},
                    },
                    "required": ["name"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        slug = _slugify(str(params.get("name") or ""))
        html_file = _panels_dir() / f"{slug}.html"
        if not html_file.is_file():
            existing = "、".join(f"{p['name']}（{p['title']}）" for p in _list_panels()) or "无"
            return ActionResult(
                success=False,
                error=f"没有这个面板：{slug}。现有面板：{existing}（先用 panel_list 确认）",
            )
        html = html_file.read_text(encoding="utf-8")
        title = _read_meta(slug).get("title") or slug
        register_panel(PANEL_PID, slug, f"译宝 · {title}", html)
        return ActionResult(success=True, panel=f"{PANEL_PID}:{slug}", data={})


class PanelListSkill(Skill):
    id = "panel_list"
    label = "列出面板"
    description = "列出生成过的所有面板（name/title/用途/创建时间）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.id, "description": self.description,
                         "parameters": {"type": "object", "properties": {}}},
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        panels = _list_panels()
        return ActionResult(
            success=True,
            data={"panels": panels,
                  "human": "还没有生成过面板" if not panels else
                           "已有面板：\n" + "\n".join(f"- {p['name']}（{p['title']}）：{p['purpose']}" for p in panels)},
        )


class PanelDeleteSkill(Skill):
    id = "panel_delete"
    label = "删除面板"
    description = "删除一个生成过的面板（先 panel_list 拿 name；用户说「删掉那个面板」时用）。"
    default_risk = RiskLevel.L2_MEDIUM

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "面板标识（panel_list 返回的 name）"},
                    },
                    "required": ["name"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        slug = _slugify(str(params.get("name") or ""))
        html_file = _panels_dir() / f"{slug}.html"
        if not html_file.is_file():
            return ActionResult(success=False, error=f"没有这个面板：{slug}（先 panel_list 确认）")
        html_file.unlink()
        meta_file = _panels_dir() / f"{slug}.meta.json"
        if meta_file.exists():
            meta_file.unlink()
        unregister_panel(f"{PANEL_PID}:{slug}")
        return ActionResult(success=True, data={"name": slug, "human": f"已删除面板：{slug}"})
