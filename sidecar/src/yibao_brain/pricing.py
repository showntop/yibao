"""LLM 定价表：按模型计算每次调用的费用（单位：元，每 1M token）。

价格来自各模型官方公开定价（人民币）。cached（命中缓存）输入按更低折扣价计。
未知模型返回 None（前端不显示费用，避免误导）。
"""
from __future__ import annotations

from .llm import Usage

# (输入/1M, 输出/1M, 缓存命中输入/1M) 单位：元
# 仅收录实际可用模型；未知模型 → None（计费不可靠就不显示）
_MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    # 智谱 GLM
    "glm-4.5": (0.6, 2.0, 0.6),
    "glm-4.5-air": (0.6, 2.0, 0.6),
    "glm-4.6": (0.6, 2.0, 0.6),
    "glm-4-plus": (0.05, 0.05, 0.05),
    "glm-4-air": (0.001, 0.001, 0.001),
    "glm-4-flash": (0.0001, 0.0001, 0.0001),
    # DeepSeek
    "deepseek-chat": (2.0, 8.0, 0.5),
    "deepseek-reasoner": (4.0, 16.0, 1.0),
}


def price_for(model: str) -> tuple[float, float, float] | None:
    """取模型定价；未知模型返回 None（调用方决定是否显示费用）。"""
    return _MODEL_PRICES.get(model) or _MODEL_PRICES.get(model.split("/")[-1])


def compute_cost(model: str, usage: Usage) -> float | None:
    """按 usage 计算本次调用费用（元）。未知模型/零用量返回 None。"""
    price = price_for(model)
    if price is None:
        return None
    prompt_in, prompt_out, prompt_cached = price
    cached = usage.cached_tokens
    uncached = max(0, usage.prompt_tokens - cached)
    cost = (
        uncached * prompt_in + cached * prompt_cached + usage.completion_tokens * prompt_out
    ) / 1_000_000
    return round(cost, 6)
