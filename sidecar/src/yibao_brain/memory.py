"""长期记忆：接口 + Fake（测试）+ Mem0（生产）+ LazyMem0（后台懒加载）。"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import warnings
from abc import ABC, abstractmethod

# mem0 的 PostHog 遥测默认开（本地产品不该外发），须在 mem0 首次导入前关掉；
# 顺带压住 mem0 调 sentence-transformers 旧接口的 FutureWarning（第三方噪音）。
os.environ.setdefault("MEM0_TELEMETRY", "false")
# fastembed 模型缓存默认在系统临时目录（重启即清空，每次启动重下几十 MB 且国内直连 HF 常被拒）——
# 固定到数据目录下 models/fastembed（随用户数据走，首启下载一次后离线可用）。
from . import config as _cfg

os.environ.setdefault("FASTEMBED_CACHE_PATH", os.path.join(_cfg.data_dir(), "models", "fastembed"))
warnings.filterwarnings("ignore", message=".*get_sentence_embedding_dimension.*", category=FutureWarning)
# mem0 对未装的可选组件（spaCy 词形/实体、fastembed BM25）每次启动刷 warning——
# 这些能力我们不用（向量召回已够），压到 ERROR 以下，别污染大脑 stderr。
logging.getLogger("mem0").setLevel(logging.ERROR)


class Memory(ABC):
    @abstractmethod
    def add(self, text: str, user_id: str) -> bool:
        """记录一条事实：True=新增事实，False=无新增/去重/降级。

        Task 3：让调用方（loop）能按「是否真的新增了记忆」决定要不要写 Feed，
        而不是每次 add 后都写一条（去重/降级场景会污染 Feed）。
        """

    @abstractmethod
    def recall(self, query: str, user_id: str) -> list[str]: ...

    def list_all(self, user_id: str) -> list[dict]:
        """记忆管理（OS 感 §4.4）：列出该命名空间全部记忆 [{"id","text"}]。默认空（不支持后端的退化）。"""
        return []

    def delete_by_id(self, memory_id: str) -> None:
        """按 id 删一条；未就绪/不支持时实现方应抛异常（调用方转人话）。"""
        raise RuntimeError("当前记忆后端不支持删除")

    def update(self, memory_id: str, text: str) -> bool:
        """按 id 编辑一条的文本：成功 True，不存在/后端失败 False；未就绪/不支持时实现方可抛异常。"""
        raise RuntimeError("当前记忆后端不支持编辑")


def _humanize_err(err: Exception | None) -> str:
    """mem0 初始化失败的原文 → 给用户看的人话（第三方报错原文太技术且常误导）。"""
    s = str(err or "未知错误")
    if "already accessed by another instance" in s:
        return "记忆库被另一个译宝实例占用"
    if "Missing credentials" in s or "OPENAI_API_KEY" in s:
        # mem0 的 openai provider 在 key 为空时抛的原生文案（实际复用主 LLM 配置）
        return "未配置模型 key（请到设置页完成模型配置）"
    return s


class FakeMemory(Memory):
    """简单子串匹配；按 user_id 隔离。"""

    def __init__(self) -> None:
        self._by_user: dict[str, list[str]] = {}

    def add(self, text: str, user_id: str) -> bool:
        self._by_user.setdefault(user_id, []).append(text)
        return True

    def recall(self, query: str, user_id: str) -> list[str]:
        items = self._by_user.get(user_id, [])
        q = query.lower()
        return [it for it in items if q and (q in it.lower() or it.lower() in q)]

    def list_all(self, user_id: str) -> list[dict]:
        # 合成稳定 id（user_id:下标），测试可断言删除效果
        return [{"id": f"{user_id}:{i}", "text": t} for i, t in enumerate(self._by_user.get(user_id, []))]

    def delete_by_id(self, memory_id: str) -> None:
        uid, _, idx = memory_id.rpartition(":")
        items = self._by_user.get(uid)
        if items is None or not idx.isdigit() or int(idx) >= len(items):
            raise RuntimeError(f"记忆不存在：{memory_id}")
        del items[int(idx)]

    def update(self, memory_id: str, text: str) -> bool:
        uid, _, idx = memory_id.rpartition(":")
        items = self._by_user.get(uid)
        if items is None or not idx.isdigit() or int(idx) >= len(items):
            return False
        items[int(idx)] = text
        return True


# 每轮对话注入模型的记忆条数上限：太多既贵又随 query 变化切断缓存前缀。
# 5 条 ≈ 1k tokens，足够覆盖高相关事实，且召回集小、相邻轮次更可能重叠。
_RECALL_TOP_K = 5


class Mem0Memory(Memory):
    """mem0 封装：主 LLM 复用(事实抽取) + 本地 fastembed/ONNX(embedder) + 本地 qdrant(vector)。

    LLM 复用主 provider 配置（llm_api_key/model/base_url）；embedder/vector 本地，免外部服务。
    embedder 从 sentence-transformers/torch 换成 fastembed（同模型 ONNX 量化版）——打包瘦身，
    注意与旧 torch 实现的向量数值有微差，切换时旧 mem0_store 需重建（2026-07-22）。
    初始化失败时由调用方 try/except 降级为 FakeMemory。
    """

    def __init__(self) -> None:
        from mem0 import Memory as _Mem0

        from .config import (
            llm_api_key, llm_base_url, llm_model,
            mem0_embedder_dim, mem0_embedder_model, mem0_vector_path,
        )

        if not llm_api_key():
            # key 缺失时 mem0 的 openai provider 抛原生「Missing credentials …OPENAI_API_KEY」，
            # 文案误导（实际复用的是主 LLM 配置）——拦在前面给人话
            raise RuntimeError("未配置模型 key（请到设置页完成模型配置）")
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
                "provider": "fastembed",
                "config": {"model": mem0_embedder_model()},
            },
            # 事实抽取默认英文偏好（中文输入被翻成英文事实，bge-zh 跨语言召回打折）：
            # 指令级定制，事实保留用户原话语言
            "custom_instructions": "提取的事实一律用用户原话的语言记录（中文输入记中文），保留专有名词原文。",
        }
        self._m = _Mem0.from_config(cfg)

    def add(self, text: str, user_id: str) -> bool:
        # mem0 返回 {"results":[{"event":"ADD"/"NOOP"/"UPDATE"}, ...]}：
        # 仅当含 ADD 事件才算新增事实（NOOP=去重、UPDATE=改写都不算），Feed 才值得写。
        # self._m.add 抛异常或返回结构异常时降级为 False，不阻断回路（与 recall 一致）。
        try:
            res = self._m.add(messages=[{"role": "user", "content": text}], user_id=user_id)
            results = (res or {}).get("results", []) if isinstance(res, dict) else []
            return any(isinstance(it, dict) and it.get("event") == "ADD" for it in results)
        except Exception:
            return False

    def recall(self, query: str, user_id: str) -> list[str]:
        try:
            # top_k 必须显式给：mem0 默认返回 20 条，几十条事实动辄 3000+ tokens，
            # 既把 prompt 撑爆也随 query 变化反复切断缓存前缀（cost 全按未命中收）。
            res = self._m.search(query=query, filters={"user_id": user_id}, top_k=_RECALL_TOP_K)
        except Exception:
            return []
        out: list[str] = []
        items = res if isinstance(res, list) else (res.get("results", []) if isinstance(res, dict) else [])
        for item in items:
            mem = item.get("memory") if isinstance(item, dict) else str(item)
            if mem:
                out.append(mem)
        return out

    def list_all(self, user_id: str) -> list[dict]:
        try:
            res = self._m.get_all(filters={"user_id": user_id})
        except Exception:
            return []
        items = res if isinstance(res, list) else (res.get("results", []) if isinstance(res, dict) else [])
        out = []
        for it in items:
            if isinstance(it, dict) and it.get("memory"):
                out.append({
                    "id": str(it.get("id") or ""),
                    "text": str(it["memory"]),
                    "created_at": str(it.get("created_at") or ""),
                })
        out.sort(key=lambda m: m["created_at"], reverse=True)  # 最新在前（mem0 get_all 不保证时间序）
        return out

    def delete_by_id(self, memory_id: str) -> None:
        self._m.delete(memory_id=memory_id)

    def update(self, memory_id: str, text: str) -> bool:
        try:
            self._m.update(memory_id=memory_id, text=text)
        except Exception:
            return False
        return True


class LazyMem0Memory(Memory):
    """mem0 后台懒加载：构造秒回（不 import mem0/onnx），真实实例在后台线程初始化。

    就绪前 recall 返回空、add 进缓冲（上限 buffer_max 条）；就绪后回放缓冲并直通真实实例；
    初始化失败会按 init_attempts 次重试（间隔 init_delay_s 秒）——旧大脑刚被回收、
    qdrant 锁尚未释放是常态竞态，重试覆盖它；最终失败才永久降级为空记忆（不阻断回路）。
    解决 mem0/onnxruntime 冷加载把 sidecar 启动拖慢的问题（大脑先上线，记忆随后接入）。
    """

    def __init__(self, factory=None, buffer_max: int = 50,
                 init_attempts: int = 5, init_delay_s: float = 3.0) -> None:
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
                self._fail_msg = f"长期记忆不可用（{_humanize_err(err)}），本次运行将记不住事"
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

    def add(self, text: str, user_id: str) -> bool:
        with self._lock:
            real = self._real
            if real is None:
                # 未就绪：进缓冲（待后台回放）或已降级（永久丢弃）都不算新增事实，
                # 调用方据此跳过 Feed 写入，避免缓冲被回放成旧事实时重复/错时推送。
                if not self._failed and len(self._buf) < self._buf_max:
                    self._buf.append((text, user_id))
                return False
        return real.add(text, user_id)

    def recall(self, query: str, user_id: str) -> list[str]:
        with self._lock:
            real = self._real
        if real is None:
            return []
        return real.recall(query, user_id)

    def list_all(self, user_id: str) -> list[dict]:
        with self._lock:
            real = self._real
        if real is None:
            return []  # 未就绪/已降级：管理页显示空 + 状态提示（ready/failed 由 server 带出）
        return real.list_all(user_id)

    def delete_by_id(self, memory_id: str) -> None:
        with self._lock:
            real = self._real
        if real is None:
            raise RuntimeError("长期记忆尚未就绪，请稍后再试")
        real.delete_by_id(memory_id)

    def update(self, memory_id: str, text: str) -> bool:
        with self._lock:
            real = self._real
        if real is None:
            raise RuntimeError("长期记忆尚未就绪，请稍后再试")
        return real.update(memory_id, text)
