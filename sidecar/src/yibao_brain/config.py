"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """dev 期自动加载 sidecar/.env（若存在），不覆盖已有 env（真 env 优先）。生产无此文件则跳过。"""
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _env(new: str, old: str = "", default: str = "") -> str:
    """读新名 env，回退旧名（YIBAO_GLM_* 向后兼容），再回退默认值。"""
    return os.environ.get(new) or (os.environ.get(old, "") if old else "") or default


def llm_api_key() -> str:
    # 主 LLM provider 的 key（任意 OpenAI 兼容端点：智谱 GLM / DeepSeek / OpenAI …）
    return _env("YIBAO_LLM_API_KEY", "YIBAO_GLM_API_KEY")


def llm_model() -> str:
    return _env("YIBAO_LLM_MODEL", "YIBAO_GLM_MODEL", "glm-4.6")


def llm_base_url() -> str:
    return _env("YIBAO_LLM_BASE_URL", "YIBAO_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")


def a11y_enabled() -> bool:
    """是否启用真实 a11y/执行基座（默认开；置 0 关闭，仅走 fake）。"""
    return os.environ.get("YIBAO_A11Y", "1") != "0"


def search_engine() -> str:
    """web_search 技能的搜索引擎（baidu/bing/google，默认 baidu）。"""
    return os.environ.get("YIBAO_SEARCH_ENGINE", "baidu")


def screenshot_dir() -> str:
    return os.environ.get("YIBAO_SCREENSHOT_DIR", "/tmp")


def vision_model() -> str:
    """computer-use 视觉兜底模型（目前仅 GLM-4.6V 支持；DeepSeek 等无视觉模型时该兜底自动禁用）。"""
    return _env("YIBAO_VISION_MODEL", "YIBAO_GLM_VISION_MODEL", "glm-4.6v-flash")


def voice_enabled() -> bool:
    return os.environ.get("YIBAO_VOICE", "1") != "0"


def stt_model_dir() -> str:
    """Paraformer 中文模型目录（含 model.int8.onnx + tokens.txt）。"""
    return os.environ.get(
        "YIBAO_STT_MODEL_DIR",
        os.path.join(os.path.dirname(__file__), "models", "paraformer-zh"),
    )


def vad_model_path() -> str:
    return os.environ.get(
        "YIBAO_VAD_MODEL",
        os.path.join(os.path.dirname(__file__), "models", "silero_vad.onnx"),
    )


def tts_voice() -> str:
    """edge-tts 中文音色（默认 XiaoxiaoNeural 女声，最自然）。"""
    return os.environ.get("YIBAO_TTS_VOICE", "zh-CN-XiaoxiaoNeural")


def mem0_embedder_model() -> str:
    """mem0 本地 embedding 模型（默认 BAAI/bge-small-zh-v1.5，中文 512 维 ~100MB）。"""
    return os.environ.get("YIBAO_MEM0_EMBEDDER", "BAAI/bge-small-zh-v1.5")


def mem0_embedder_dim() -> int:
    """embedder 向量维度（须与 mem0_embedder_model 匹配；bge-small-zh-v1.5=512）。"""
    return int(os.environ.get("YIBAO_MEM0_EMBED_DIM", "512"))


def mem0_vector_path() -> str:
    """mem0 本地 qdrant 向量库存储路径（嵌入模式，免外部 server）。"""
    return os.environ.get(
        "YIBAO_MEM0_VECTOR_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "mem0_store"),
    )
