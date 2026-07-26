"""zimeiti.ai_edit：编辑器 AI 协作——选段改写/扩写/缩写/自定义 + 全文润色/标题/平台改写（L1，只生成不落盘）。

编辑器（webview）→ 桥调本工具 → 返回 replacement/titles 给 iframe 做 diff 预览或挑选，
用户确认后才落稿；落盘仍走 article_save（L2 确认），本工具不写任何数据。
选段模式处理「选中片段」（上限 4000）；polish/title/platform 为全文模式，selection 即整文（上限 8000）。
"""
from __future__ import annotations

import json
import re
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_SELECTION = 4000  # 片段太长说明该走对话改稿，不是选段润色
_MAX_FULL = 8000  # 全文三模式（polish/title/platform）的上限
_MAX_CONTEXT = 3000  # 全文只作语境，截断防 prompt 爆炸

_MODES = {
    "rewrite": "改写这段文字：保持原意，写得更通顺、更有表达力",
    "expand": "扩写这段文字：补充细节、例子或论述，篇幅约为原文 1.5~2 倍",
    "condense": "压缩这段文字：保留核心要点，篇幅约为原文一半",
}

# 全文模式：selection 即整文，不走选段那套「片段+语境」prompt
_FULL_MODES = ("polish", "title", "platform")

# platform 模式的平台风格要点（没列出的平台直接把平台名交给模型发挥）
_PLATFORMS = {
    "小红书": "短段落、口语化、适当 emoji 点缀，文末加 3-5 个话题标签（#话题）",
    "知乎": "论述感强、逻辑连接词清晰、表达克制，不滥用 emoji",
    "公众号": "阅读节奏好、小标题分段、段落不宜过长",
}


def _build_prompt(mode: str, selection: str, instruction: str, context: str) -> str:
    task = _MODES[mode] if mode in _MODES else f"按这个要求处理这段文字：{instruction}"
    parts = [
        f"你是中文自媒体写作助手。任务：{task}。",
        "要求：只输出处理后的文本本身——不要解释、不要「好的」等前后缀、不要代码围栏；"
        "保持 markdown 格式与原文语气；处理对象是「选中片段」，不要重写全文。",
    ]
    if context:
        parts.append(f"全文语境（仅供理解上下文，不要输出它）：\n{context}")
    parts.append(f"选中片段：\n{selection}")
    return "\n\n".join(parts)


def _build_full_prompt(mode: str, text: str, platform: str) -> str:
    if mode == "polish":
        task = "全文润色：保持 markdown 结构、事实与语气不变，只提升表达流畅度与节奏"
    else:  # platform
        task = f"按「{platform}」的平台风格改写全文"
        style = _PLATFORMS.get(platform)
        if style:
            task += f"（{platform} 风格要点：{style}）"
    return (
        f"你是中文自媒体写作助手。任务：{task}。\n"
        "要求：只输出处理后的全文本身——不要解释、不要「好的」等前后缀、不要代码围栏；"
        "保持 markdown 格式。\n\n"
        f"全文：\n{text}"
    )


def _build_title_prompt(text: str) -> str:
    return (
        "你是中文自媒体写作助手。基于下面的全文起 5 个候选标题，风格各异"
        "（悬念/干货/情绪/数字/提问）。只输出 JSON 数组字符串，"
        '形如 ["标题一","标题二","标题三","标题四","标题五"]，不要任何其它内容。\n\n'
        f"全文：\n{text}"
    )


def _parse_titles(text: str) -> list[str]:
    """先按 JSON 数组解析；失败保底按行抓（去空行/序号前缀/引号），最多 5 条。"""
    t = text.strip()
    if t.startswith("```"):  # 剥代码围栏
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    i, j = t.find("["), t.rfind("]")
    if i >= 0 and j > i:
        try:
            arr = json.loads(t[i : j + 1])
            if isinstance(arr, list):
                titles = [str(x).strip() for x in arr if str(x).strip()]
                if titles:
                    return titles[:5]
        except (json.JSONDecodeError, TypeError):
            pass
    titles = []
    for line in t.splitlines():
        s = re.sub(r"^(?:\d+\s*[.、。)）]\s*|[-*•]\s*)", "", line.strip()).strip().strip("\"'「」")
        if s:
            titles.append(s)
        if len(titles) >= 5:
            break
    return titles


def _unwrap(text: str) -> str:
    """LLM 偶尔用代码围栏/引号包答案，剥掉。"""
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        t = t[3:-3].strip()
        if "\n" in t and t.split("\n", 1)[0].strip().isalpha():  # ```markdown 之类语言行
            t = t.split("\n", 1)[1].strip()
    return t


class AiEditSkill(Skill):
    id = "zimeiti.ai_edit"
    description = (
        "编辑器 AI 协作：选段改写/扩写/缩写/自定义指令，全文润色/起标题/平台风格改写，"
        "返回替换文本或候选标题（不落盘）。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selection": {"type": "string", "description": "选中的原文片段；全文模式（polish/title/platform）传整文"},
                        "mode": {"type": "string", "enum": ["rewrite", "expand", "condense", "custom", "polish", "title", "platform"],
                                 "description": "处理方式，默认 rewrite；custom 需配合 instruction；polish/title/platform 为全文模式"},
                        "instruction": {"type": "string", "description": "自定义处理要求（mode=custom 时必填）"},
                        "platform": {"type": "string", "description": "platform 模式的目标平台，如 小红书/知乎/公众号"},
                        "context": {"type": "string", "description": "全文（仅作语境，可选）"},
                    },
                    "required": ["selection"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        llm = getattr(ctx, "llm", None)
        if llm is None:
            return ActionResult(success=False, error="底座未提供 LLM 能力")
        selection = str(params.get("selection") or "").strip()
        if not selection:
            return ActionResult(success=False, error="没有选中文字")
        mode = str(params.get("mode") or "rewrite")
        if mode in _FULL_MODES:
            if len(selection) > _MAX_FULL:
                return ActionResult(success=False, error=f"文章太长（{len(selection)} 字），先拆段或走对话改稿")
        elif len(selection) > _MAX_SELECTION:
            return ActionResult(success=False, error=f"选中片段太长（{len(selection)} 字），选短一点或走对话改稿")
        instruction = str(params.get("instruction") or "").strip()
        platform = str(params.get("platform") or "").strip()
        if mode == "custom" and not instruction:
            return ActionResult(success=False, error="自定义模式需要填写处理要求")
        if mode == "platform" and not platform:
            return ActionResult(success=False, error="platform 模式需要指定目标平台（platform 参数）")
        if mode not in _MODES and mode not in _FULL_MODES and mode != "custom":
            return ActionResult(success=False, error=f"未知模式：{mode}")
        context = str(params.get("context") or "")[:_MAX_CONTEXT].strip()

        if mode == "title":
            prompt = _build_title_prompt(selection)
        elif mode in ("polish", "platform"):
            prompt = _build_full_prompt(mode, selection, platform)
        else:
            prompt = _build_prompt(mode, selection, instruction, context)
        try:
            out = llm.chat(prompt)
        except Exception as e:
            return ActionResult(success=False, error=f"AI 处理失败：{e}")
        if mode == "title":
            titles = _parse_titles(out)
            if not titles:
                return ActionResult(success=False, error="AI 没给出可用标题，换个说法再试")
            return ActionResult(success=True, data={"titles": titles, "mode": mode})
        replacement = _unwrap(out)
        if not replacement:
            return ActionResult(success=False, error="AI 返回了空内容，换个说法再试")
        return ActionResult(success=True, data={"replacement": replacement, "mode": mode})


def make_tools(ctx: Any) -> list[Skill]:
    return [AiEditSkill()]
