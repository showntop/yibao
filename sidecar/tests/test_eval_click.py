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
    # 置空而非删除：config 若在测试中途才被 import，_load_dotenv 会用 setdefault
    # 从数据目录 .env 重新播种被删除的变量；置空（falsy）则 setdefault 跳过且回退链不取
    monkeypatch.setenv("YIBAO_VISION_API_KEY", "")
    monkeypatch.setenv("YIBAO_VISION_BASE_URL", "")
    monkeypatch.setenv("YIBAO_GLM_API_KEY", "")
    monkeypatch.setenv("YIBAO_GLM_BASE_URL", "")

    with pytest.raises(SystemExit, match="YIBAO_VISION"):
        mod._require_vision_provider()


def test_window_rect_from_tree():
    mod = _load_eval_module()

    tree = {"role": "AXApplication", "children": [
        {"role": "AXWindow", "bbox": [100, 50, 500, 400], "children": []},
        {"role": "AXMenuBar", "children": []},
    ]}
    assert mod._window_rect(tree) == (100.0, 50.0, 500.0, 400.0)
    assert mod._window_rect({}) is None
    assert mod._window_rect({"role": "AXApp", "children": []}) is None


def test_run_win_crops_window_and_maps_back(tmp_path):
    mod = _load_eval_module()
    from PIL import Image

    shot = tmp_path / "s.png"
    Image.new("RGB", (400, 200), "white").save(shot)

    class FakeClient:
        def next_action(self, b64, task, history):
            return {"action": "click", "box": [20, 10, 40, 30]}

    sc = {"screenshot": str(shot),
          "tree": {"role": "AXApplication", "children": [
              {"role": "AXWindow", "bbox": [100, 50, 300, 150], "children": []}]},
          "target": "按钮"}
    # crop = [100,50,300,150]（scale 1.0）；box 中心 (30,20) → + 窗口原点 (100,50)
    assert mod.run_win(FakeClient(), sc, 1.0) == (130.0, 70.0)
    # 树里无窗口 → None
    assert mod.run_win(FakeClient(), {"screenshot": str(shot), "tree": {}, "target": "x"}, 1.0) is None
