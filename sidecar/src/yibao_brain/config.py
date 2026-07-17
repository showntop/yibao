"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

import os


def glm_api_key() -> str:
    return os.environ.get("YIBAO_GLM_API_KEY", "")


def glm_model() -> str:
    return os.environ.get("YIBAO_GLM_MODEL", "glm-4.6")


def glm_base_url() -> str:
    return os.environ.get("YIBAO_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")


def a11y_enabled() -> bool:
    """是否启用真实 a11y/执行基座（默认开；置 0 关闭，仅走 fake）。"""
    return os.environ.get("YIBAO_A11Y", "1") != "0"


def screenshot_dir() -> str:
    return os.environ.get("YIBAO_SCREENSHOT_DIR", "/tmp")


def glm_vision_model() -> str:
    """computer-use 兜底用的视觉模型（glm-4.6v-flash 免费，生产用 glm-4.6v）。"""
    return os.environ.get("YIBAO_GLM_VISION_MODEL", "glm-4.6v-flash")


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
