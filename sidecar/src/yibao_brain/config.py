"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

import os


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
