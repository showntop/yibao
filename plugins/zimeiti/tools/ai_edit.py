"""zimeiti.ai_edit：编辑器选段 AI 协作——改写/扩写/缩写/自定义指令（L1，只生成不落盘）。

编辑器（webview）选中片段 → 桥调本工具 → 返回 replacement 给 iframe 做 diff 预览，
用户确认后才替换进编辑区；落盘仍走 article_save（L2 确认），本工具不写任何数据。
"""
from __future__ import annotations

from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_MAX_SELECTION = 4000  # 片段太长说明该走对话改稿，不是选段润色
_MAX_CONTEXT = 3000  # 全文只作语境，截断防 prompt 爆炸

_MODES = {
    "rewrite": "改写这段文字：保持原意，写得更通顺、更有表达力",
    "expand": "扩写这段文字：补充细节、例子或论述，篇幅约为原文 1.5~2 倍",
    "condense": "压缩这段文字：保留核心要点，篇幅约为原文一半",
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
    description = "编辑器选段 AI 处理：改写/扩写/缩写/自定义指令，返回替换文本（不落盘）。"
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
                        "selection": {"type": "string", "description": "选中的原文片段"},
                        "mode": {"type": "string", "enum": ["rewrite", "expand", "condense", "custom"],
                                 "description": "处理方式，默认 rewrite；custom 需配合 instruction"},
                        "instruction": {"type": "string", "description": "自定义处理要求（mode=custom 时必填）"},
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
        if len(selection) > _MAX_SELECTION:
            return ActionResult(success=False, error=f"选中片段太长（{len(selection)} 字），选短一点或走对话改稿")
        mode = str(params.get("mode") or "rewrite")
        instruction = str(params.get("instruction") or "").strip()
        if mode == "custom" and not instruction:
            return ActionResult(success=False, error="自定义模式需要填写处理要求")
        if mode not in _MODES and mode != "custom":
            return ActionResult(success=False, error=f"未知模式：{mode}")
        context = str(params.get("context") or "")[:_MAX_CONTEXT].strip()

        try:
            out = llm.chat(_build_prompt(mode, selection, instruction, context))
        except Exception as e:
            return ActionResult(success=False, error=f"AI 处理失败：{e}")
        replacement = _unwrap(out)
        if not replacement:
            return ActionResult(success=False, error="AI 返回了空内容，换个说法再试")
        return ActionResult(success=True, data={"replacement": replacement, "mode": mode})


def make_tools(ctx: Any) -> list[Skill]:
    return [AiEditSkill()]
