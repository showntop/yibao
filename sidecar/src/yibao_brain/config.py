"""配置：从环境变量读取，带默认值。"""
from __future__ import annotations

import os


def glm_api_key() -> str:
    return os.environ.get("YIBAO_GLM_API_KEY", "")


def glm_model() -> str:
    return os.environ.get("YIBAO_GLM_MODEL", "glm-4.6")


def glm_base_url() -> str:
    return os.environ.get("YIBAO_GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/")
