"""把 Codex 对话尾段 + git 摘要凝练成交接 Brief（给 Claude Code 当接续上下文）。"""
from __future__ import annotations

_PROMPT = """你是交接助手。下面是用户在 Codex（另一个 coding agent）里的工作记录尾段 + 该项目 git 状态。
请凝练成一段「交接 Brief」，给 Claude Code 当接续上下文。结构：任务 / 已完成 / 当前卡点 / 下一步建议（中文、具体带文件名，宁缺毋滥、别编造）。

【Codex 工作记录（近几轮）】
{dialog}

【git 状态】
{git}
"""


def build_brief(llm, conversation: list[dict], git_summary: str) -> str | None:
    dialog = "\n".join(f"{t['role']}: {t['text']}" for t in conversation) or "（无）"
    prompt = _PROMPT.format(dialog=dialog, git=git_summary or "（无）")
    try:
        # ctx.llm 是 LlmChat 包装：chat(prompt:str) -> str（非原始 provider 的 chat(messages,timeout)）
        text = (llm.chat(prompt) or "").strip()
        return text or None
    except Exception as e:
        import sys; print(f"[yibao/coding] brief 生成失败：{e}", file=sys.stderr)
        return None
