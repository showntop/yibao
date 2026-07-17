"""长期记忆：接口 + Fake（测试）+ Mem0（生产）。"""
from __future__ import annotations

from abc import ABC, abstractmethod


class Memory(ABC):
    @abstractmethod
    def add(self, text: str, user_id: str) -> None: ...

    @abstractmethod
    def recall(self, query: str, user_id: str) -> list[str]: ...


class FakeMemory(Memory):
    """简单子串匹配；按 user_id 隔离。"""

    def __init__(self) -> None:
        self._by_user: dict[str, list[str]] = {}

    def add(self, text: str, user_id: str) -> None:
        self._by_user.setdefault(user_id, []).append(text)

    def recall(self, query: str, user_id: str) -> list[str]:
        items = self._by_user.get(user_id, [])
        q = query.lower()
        return [it for it in items if q and (q in it.lower() or it.lower() in q)]


class Mem0Memory(Memory):
    """mem0 封装：DeepSeek(LLM 抽取) + 本地 HF(embedder) + 本地 qdrant(vector)。

    LLM 复用主 provider 配置（llm_api_key/model/base_url）；embedder/vector 本地，免外部服务。
    mem0 未装（optional）或初始化失败时，由调用方 try/except 降级为 FakeMemory。
    """

    def __init__(self) -> None:
        from mem0 import Memory as _Mem0

        from .config import (
            llm_api_key, llm_base_url, llm_model,
            mem0_embedder_dim, mem0_embedder_model, mem0_vector_path,
        )

        cfg = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": mem0_vector_path(),
                    "embedding_model_dims": mem0_embedder_dim(),
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": llm_model(),
                    "openai_base_url": llm_base_url(),
                    "api_key": llm_api_key(),
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": mem0_embedder_model()},
            },
        }
        self._m = _Mem0.from_config(cfg)

    def add(self, text: str, user_id: str) -> None:
        self._m.add(messages=[{"role": "user", "content": text}], user_id=user_id)

    def recall(self, query: str, user_id: str) -> list[str]:
        try:
            res = self._m.search(query=query, filters={"user_id": user_id})
        except Exception:
            return []
        out: list[str] = []
        items = res if isinstance(res, list) else (res.get("results", []) if isinstance(res, dict) else [])
        for item in items:
            mem = item.get("memory") if isinstance(item, dict) else str(item)
            if mem:
                out.append(mem)
        return out
