"""长期记忆：接口 + Fake（测试）+ Mem0（生产）+ LazyMem0（后台懒加载）。"""
from __future__ import annotations

import os
import sys
import threading
import time
import warnings
from abc import ABC, abstractmethod

# mem0 的 PostHog 遥测默认开（本地产品不该外发），须在 mem0 首次导入前关掉；
# 顺带压住 mem0 调 sentence-transformers 旧接口的 FutureWarning（第三方噪音）。
os.environ.setdefault("MEM0_TELEMETRY", "false")
warnings.filterwarnings("ignore", message=".*get_sentence_embedding_dimension.*", category=FutureWarning)


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


class LazyMem0Memory(Memory):
    """mem0 后台懒加载：构造秒回（不 import torch/mem0），真实实例在后台线程初始化。

    就绪前 recall 返回空、add 进缓冲（上限 buffer_max 条）；就绪后回放缓冲并直通真实实例；
    初始化失败会按 init_attempts 次重试（间隔 init_delay_s 秒）——旧大脑刚被回收、
    qdrant 锁尚未释放是常态竞态，重试覆盖它；最终失败才永久降级为空记忆（不阻断回路）。
    解决 torch/sentence-transformers 冷加载把 sidecar 启动拖慢的问题（大脑先上线，记忆随后接入）。
    """

    def __init__(self, factory=None, buffer_max: int = 50,
                 init_attempts: int = 3, init_delay_s: float = 2.0) -> None:
        self._factory = factory or Mem0Memory
        self._buf_max = buffer_max
        self._attempts = max(1, init_attempts)
        self._delay = max(0.0, init_delay_s)
        self._real = None
        self._failed = False
        self._fail_msg: str | None = None
        self._on_status = None  # 降级时通知壳（server 注入，经 call_soon_threadsafe 回主循环）
        self._buf: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._init, daemon=True).start()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._real is not None

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._failed

    def set_status_callback(self, cb) -> None:
        """注入降级通知回调；若已失败则立即补发（回调注入晚于失败时不错过）。"""
        with self._lock:
            self._on_status = cb
            msg = self._fail_msg
        if msg is not None:
            cb(msg)

    def _init(self) -> None:
        real = None
        err: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                real = self._factory()
                break
            except Exception as e:
                err = e
                if attempt < self._attempts:
                    print(f"[yibao] mem0 初始化失败（第 {attempt}/{self._attempts} 次），"
                          f"{self._delay:.0f}s 后重试：{e}", file=sys.stderr)
                    time.sleep(self._delay)
        if real is None:
            print(f"[yibao] mem0 后台初始化失败，记忆降级为空：{err}", file=sys.stderr)
            with self._lock:
                self._failed = True
                self._fail_msg = f"长期记忆不可用（{err}），本次运行将记不住事"
                cb = self._on_status
                self._buf.clear()
            if cb is not None:
                cb(self._fail_msg)
            return
        with self._lock:
            self._real = real
            pending, self._buf = self._buf, []
        for text, user_id in pending:  # 回放就绪前的缓冲（单条失败不阻断其余）
            try:
                real.add(text, user_id)
            except Exception:
                pass
        print("[yibao] mem0 后台就绪", file=sys.stderr)

    def add(self, text: str, user_id: str) -> None:
        with self._lock:
            real = self._real
            if real is None:
                if not self._failed and len(self._buf) < self._buf_max:
                    self._buf.append((text, user_id))
                return
        real.add(text, user_id)

    def recall(self, query: str, user_id: str) -> list[str]:
        with self._lock:
            real = self._real
        if real is None:
            return []
        return real.recall(query, user_id)
