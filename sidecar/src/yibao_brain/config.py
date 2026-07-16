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
