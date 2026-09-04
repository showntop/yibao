"""core 自投表面：产物列表面板载荷。"""
from __future__ import annotations

from yibao_brain.core_surfaces import artifacts_panel


def test_artifacts_panel_shape_and_native_actions():
    rows = [
        {"id": "a1", "type": "video.render", "ref": "t1#render", "version": 3,
         "path": "/tmp/renders/t1/v3.mp4", "updated_at": 1757000000.0},
        {"id": "a2", "type": "video.script", "ref": "t1#script", "version": None,
         "path": "", "updated_at": 1756990000.0},
    ]
    payload = artifacts_panel(rows)
    assert payload["panel"] == "core:artifacts"
    assert payload["explicit"] is True
    assert payload["presentation"] == "stage"
    assert payload["schema"]["type"] == "list"

    out = payload["data"]["rows"]
    assert [r["title"] for r in out] == ["t1#render", "t1#script"]
    # 文件型：两个 native 动作，params 带字面路径
    assert out[0]["path"] == "/tmp/renders/t1/v3.mp4"
    assert [a["method"] for a in out[0]["actions"]] == ["native:reveal", "native:open"]
    assert all(a["params"]["path"] == "/tmp/renders/t1/v3.mp4" for a in out[0]["actions"])
    assert "v3" in out[0]["meta"] and "video.render" in out[0]["meta"]
    # 非文件型：无 path、无 actions（行级覆盖，不落模板空按钮）
    assert "path" not in out[1]
    assert "actions" not in out[1]
    assert "v" not in out[1]["meta"].split("·")[1]


def test_artifacts_panel_empty():
    payload = artifacts_panel([])
    assert payload["data"]["rows"] == []
    assert payload["schema"]["empty"]["title"]
