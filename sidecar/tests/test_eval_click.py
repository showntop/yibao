"""点击评测脚本的场景参数回归测试。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_eval_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "eval_click.py"
    spec = importlib.util.spec_from_file_location("eval_click", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scenario_scale_prefers_explicit_window_crop_scale():
    mod = _load_eval_module()

    assert mod._scenario_scale({"scale": 1.0}, "/not/read.png") == 1.0


def test_eval_preflight_rejects_non_visual_provider(monkeypatch):
    mod = _load_eval_module()
    monkeypatch.setenv("YIBAO_LLM_API_KEY", "deepseek-key")
    monkeypatch.setenv("YIBAO_LLM_BASE_URL", "https://api.deepseek.com")
    monkeypatch.delenv("YIBAO_VISION_API_KEY", raising=False)
    monkeypatch.delenv("YIBAO_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("YIBAO_GLM_API_KEY", raising=False)
    monkeypatch.delenv("YIBAO_GLM_BASE_URL", raising=False)

    with pytest.raises(SystemExit, match="YIBAO_VISION"):
        mod._require_vision_provider()
