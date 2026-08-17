"""把对话尾段 + git 摘要凝练成交接 Brief（给另一个 coding agent 当接续上下文）。"""
from __future__ import annotations

_PROMPT = """你是交接助手。下面是用户在 {src}（另一个 coding agent）里的工作记录尾段 + 该项目 git 状态。
请凝练成一段「交接 Brief」，给 {dst} 当接续上下文。结构：任务 / 已完成 / 当前卡点 / 下一步建议（中文、具体带文件名，宁缺毋滥、别编造）。

【{src} 工作记录（近几轮）】
{dialog}

【git 状态】
{git}
"""


def build_brief(llm, conversation: list[dict], git_summary: str,
                src: str = "Codex", dst: str = "Claude Code") -> str | None:
    """凝练交接 Brief；src/dst 是源/目标引擎显示名（双向交接 wording，缺省保持 Codex→CC 旧行为）。"""
    dialog = "\n".join(f"{t['role']}: {t['text']}" for t in conversation) or "（无）"
    prompt = _PROMPT.format(src=src, dst=dst, dialog=dialog, git=git_summary or "（无）")
    try:
        # ctx.llm 是 LlmChat 包装：chat(prompt:str) -> str（非原始 provider 的 chat(messages,timeout)）
        text = (llm.chat(prompt) or "").strip()
        return text or None
    except Exception as e:
        import sys; print(f"[yibao/coding] brief 生成失败：{e}", file=sys.stderr)
        return None
