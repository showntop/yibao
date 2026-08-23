"""coding.studio:打开多工位面板(module 面板入口 skill)。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# 插件 tools 不在 src 下,单独加路径(仿 test_coding_plugin.py)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "coding", "tools"))
from coding import StudioTool  # noqa: E402


def test_studio_skill_points_at_module_panel():
    res = StudioTool().run({}, None)
    assert res.success
    assert res.panel == "coding:studio"
