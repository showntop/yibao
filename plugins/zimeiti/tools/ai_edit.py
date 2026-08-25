"""zimeiti.ai_edit：编辑器 AI 协作——选段改写/扩写/缩写/自定义 + 全文润色/标题/平台改写（L1，只生成不落盘）。

编辑器（webview）→ 桥调本工具 → 返回 replacement/titles 给 iframe 做 diff 预览或挑选，
用户确认后才落稿；落盘仍走 article_save（L2 确认），本工具不写任何数据。
选段模式处理「选中片段」（上限 4000）；polish/title/platform 为全文模式（selection 即整文）——
polish 超 8000 自动分段润色队列，title 截断取要，platform 需整文视角仍限 8000。
"""
from __future__ import annotations

import json
import re
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_MAX_SELECTION = 4000  # 片段太长说明该走对话改稿，不是选段润色
_MAX_FULL = 8000  # platform 模式上限（需整文视角）；polish 超上限自动分段润色，title 截断取要
_MAX_CONTEXT = 3000  # 全文只作语境，截断防 prompt 爆炸

_MODES = {
    "rewrite": "改写这段文字：保持原意，写得更通顺、更有表达力",
    "expand": "扩写这段文字：补充细节、例子或论述，篇幅约为原文 1.5~2 倍",
    "condense": "压缩这段文字：保留核心要点，篇幅约为原文一半",
}

# 全文模式：selection 即整文，不走选段那套「片段+语境」prompt
_FULL_MODES = ("polish", "title", "platform")

# 语气规则（与 skills/write/SKILL.md 同一套——编辑器 AI 与对话初稿必须同文风）：
#   口语优先、短句、段落 ≤4 行、克制形容词与惊叹号、具体数字/名字/场景优于抽象判断
_TONE = ("语气规则：口语优先，短句优先，一段不超过 4 行；不堆形容词，不用「非常」「极其」，"
         "惊叹号全文不超过 2 个；数字、名字、具体场景优于抽象判断；事实与来源括注一律保留。")

# platform 模式的平台规格（没列出的平台直接把平台名交给模型发挥）——与 SKILL.md 平台适配节对齐
_PLATFORMS = {
    "小红书": "正文 ≤1000 字，短段落清单体，emoji 分段点缀；标题 ≤20 字且带搜索关键词；"
              "首图文案即标题；文末加 3-5 个话题标签（#话题，选用户会搜的词）",
    "知乎": "首段直接亮观点，论述结构清晰、逻辑连接词明确，表达克制，不滥用 emoji；"
            "小标题推进论证，结尾给可讨论的问题",
    "公众号": "标题带悬念或利益点；导语 3 行内给钩子；小标题分段、段落 ≤4 行；"
              "配图位留标注（如【配图：xxx】）",
}


def _build_prompt(mode: str, selection: str, instruction: str, context: str, brief: str) -> str:
    task = _MODES[mode] if mode in _MODES else f"按这个要求处理这段文字：{instruction}"
    parts = [
        f"你是中文内容创作写作助手。任务：{task}。",
        "要求：只输出处理后的文本本身——不要解释、不要「好的」等前后缀、不要代码围栏；"
        f"保持 markdown 格式。{_TONE}处理对象是「选中片段」，不要重写全文。",
    ]
    if brief:
        parts.append(f"选题简报（理解写作方向，不要输出它）：{brief}")
    if context:
        parts.append(f"全文语境（仅供理解上下文，不要输出它）：\n{context}")
    parts.append(f"选中片段：\n{selection}")
    return "\n\n".join(parts)


def _build_full_prompt(mode: str, text: str, platform: str, brief: str) -> str:
    if mode == "polish":
        task = "全文润色：保持 markdown 结构、事实与来源括注不变，只提升表达流畅度与节奏"
    else:  # platform
        task = f"按「{platform}」的平台规格改写全文"
        style = _PLATFORMS.get(platform)
        if style:
            task += f"（{platform} 规格：{style}）"
    parts = [
        f"你是中文内容创作写作助手。任务：{task}",
        "要求：只输出处理后的全文本身——不要解释、不要「好的」等前后缀、不要代码围栏；"
        f"保持 markdown 格式。{_TONE}",
    ]
    if brief:
        parts.append(f"选题简报（理解写作方向，不要输出它）：{brief}")
    parts.append(f"全文：\n{text}")
    return "\n\n".join(parts)


def _build_title_prompt(text: str, platform: str) -> str:
    plat = ""
    if platform:
        spec = _PLATFORMS.get(platform, "")
        plat = f"目标平台是「{platform}」，标题要贴合该平台调性{('（' + spec + '）') if spec else ''}。"
    return (
        f"你是中文内容创作写作助手。{plat}基于下面的全文起 5 个候选标题，风格各异"
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


def _chunks(text: str, max_len: int) -> list[str]:
    """长文切段（polish 分段队列用）：按空行分段组装，每段 ≤ max_len；单段超长硬切。"""
    paras = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    cur: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if cur:
            chunks.append("\n\n".join(cur))
            cur.clear()
            size = 0

    for p in paras:
        while len(p) > max_len:  # 单段超长：先冲当前段，再硬切
            flush()
            chunks.append(p[:max_len])
            p = p[max_len:]
        if cur and size + len(p) + 2 > max_len:
            flush()
        cur.append(p)
        size += len(p) + 2
    flush()
    return [c for c in chunks if c.strip()]


class AiEditTool(Tool):
    id = "zimeiti.ai_edit"
    label = "AI 改稿"
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
                        "brief": {"type": "string", "description": "选题简报（标题/角度/目标平台一句话，编辑器自动带上）"},
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
        if mode == "platform" and len(selection) > _MAX_FULL:
            # 平台改写要整文视角（调结构/压篇幅），分段会碎——长文走对话改稿
            return ActionResult(success=False, error=f"文章太长（{len(selection)} 字），平台改写需要整文视角：先走对话改稿压缩，再回编辑器做平台改写")
        if mode not in _FULL_MODES and len(selection) > _MAX_SELECTION:
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
        brief = str(params.get("brief") or "").strip()

        # polish 长文：分段润色队列（逐段调 LLM 后拼接），不再把长文踢出编辑器
        if mode == "polish" and len(selection) > _MAX_FULL:
            return self._polish_chunked(llm, selection, brief)
        if mode == "title":
            prompt = _build_title_prompt(selection[:_MAX_FULL], platform)  # 起标题取要即可，长文截断不踢出
        elif mode in ("polish", "platform"):
            prompt = _build_full_prompt(mode, selection, platform, brief)
        else:
            prompt = _build_prompt(mode, selection, instruction, context, brief)
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

    def _polish_chunked(self, llm: Any, text: str, brief: str) -> ActionResult:
        """长文分段润色：逐段调 LLM，拼接返回（data.chunks 给编辑器状态栏展示「分 N 段」）。"""
        chunks = _chunks(text, _MAX_FULL)
        parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            try:
                piece = _unwrap(llm.chat(_build_full_prompt("polish", chunk, "", brief)))
            except Exception as e:
                return ActionResult(success=False, error=f"分段润色第 {i}/{len(chunks)} 段失败：{e}")
            if not piece:
                return ActionResult(success=False, error=f"分段润色第 {i}/{len(chunks)} 段返回空，换个说法再试")
            parts.append(piece)
        return ActionResult(success=True, data={"replacement": "\n\n".join(parts), "mode": "polish", "chunks": len(parts)})


def make_tools(ctx: Any) -> list[Tool]:
    return [AiEditTool()]
