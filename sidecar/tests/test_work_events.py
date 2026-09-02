"""work_output 声明式投影：normalize 校验 + materialize 事件扇出。

重点覆盖 foreach_from（2026-09-02 动态 N 产物：一个 tool 一次调用产出 N 个
artifact/edge，N 由运行期 data 数组长度决定——分镜的 shot 是第一个真实用例）：
- foreach_from 指向对象数组 → 每元素产一条事件，item.* 路径取元素字段；
- 根路径（data.*/params.*）与 item.* 可在同一声明里混用（边的一端恒定、一端随元素）；
- 空数组 → 零事件（数据驱动的条件产出：有稿才产 derived_from 边）；
- strict（PluginDb 事务路径）：foreach 未命中数组/元素非对象 → 抛错回滚，不丢事件。
"""
from __future__ import annotations

import pytest

from yibao_brain.work_events import materialize_work_events, normalize_work_output


# ---------- normalize_work_output：foreach_from 声明校验 ----------


def test_normalize_accepts_foreach_from():
    spec = normalize_work_output({
        "kind": "artifact",
        "artifact_type": "video.shot",
        "foreach_from": "data.shots",
        "ref_from": "item.shot_ref",
    })
    assert spec["foreach_from"] == "data.shots"
    assert spec["kind"] == "artifact"


def test_normalize_rejects_empty_foreach_from():
    with pytest.raises(ValueError, match="foreach_from"):
        normalize_work_output({
            "kind": "artifact",
            "artifact_type": "video.shot",
            "foreach_from": "  ",
            "ref_from": "item.shot_ref",
        })


# ---------- materialize：无 foreach 的行为不变 ----------


def test_materialize_single_artifact_unchanged():
    events = materialize_work_events(
        ({"kind": "artifact", "artifact_type": "video.script",
          "ref_from": "data.id", "content_ref_from": "data.content_ref",
          "metadata_fields": ["data.version", "params.note"]},),
        params={"note": "初稿"}, data={"id": "t1", "content_ref": "blob://sha256/x", "version": 2},
        tool_id="demo.save", strict=True,
    )
    assert events == [{
        "event_type": "artifact.upsert",
        "payload": {
            "artifact_type": "video.script", "ref": "t1",
            "content_ref": "blob://sha256/x", "lifecycle": "draft",
            "metadata": {"tool_id": "demo.save", "version": 2, "note": "初稿"},
        },
    }]


# ---------- foreach 扇出 ----------


def test_foreach_fans_out_artifacts_per_element():
    """一个声明 × 数组每元素一条 artifact 事件；item.* 取元素字段，data.* 取根字段。"""
    specs = ({
        "kind": "artifact",
        "artifact_type": "video.shot",
        "foreach_from": "data.shots",
        "ref_from": "item.shot_ref",
        "content_ref_from": "data.content_ref",
        "metadata_fields": ["item.idx", "item.narration", "data.version"],
    },)
    data = {
        "content_ref": "blob://sha256/sb",
        "version": 3,
        "shots": [
            {"shot_ref": "t1#s1", "idx": 1, "narration": "开场"},
            {"shot_ref": "t1#s2", "idx": 2, "narration": "转折"},
        ],
    }
    events = materialize_work_events(specs, params={}, data=data, tool_id="demo.sb", strict=True)
    assert [e["payload"]["ref"] for e in events] == ["t1#s1", "t1#s2"]
    assert all(e["event_type"] == "artifact.upsert" for e in events)
    assert all(e["payload"]["artifact_type"] == "video.shot" for e in events)
    assert all(e["payload"]["content_ref"] == "blob://sha256/sb" for e in events)
    assert events[0]["payload"]["metadata"] == {
        "tool_id": "demo.sb", "idx": 1, "narration": "开场", "version": 3,
    }


def test_foreach_edge_mixes_root_and_item_endpoints():
    """contains 边：source 恒定（分镜本体），target 随元素（各 shot）。"""
    specs = ({
        "kind": "edge",
        "relation": "contains",
        "foreach_from": "data.shots",
        "source_artifact_type": "video.storyboard",
        "source_ref_from": "data.storyboard_ref",
        "target_artifact_type": "video.shot",
        "target_ref_from": "item.shot_ref",
    },)
    data = {"storyboard_ref": "t1", "shots": [{"shot_ref": "t1#s1"}, {"shot_ref": "t1#s2"}]}
    events = materialize_work_events(specs, params={}, data=data, tool_id="demo.sb", strict=True)
    assert [e["event_type"] for e in events] == ["artifact.edge.upsert"] * 2
    assert events[0]["payload"] == {
        "source": {"artifact_type": "video.storyboard", "ref": "t1"},
        "target": {"artifact_type": "video.shot", "ref": "t1#s1"},
        "relation": "contains", "label": "", "metadata": {"tool_id": "demo.sb"},
    }
    assert events[1]["payload"]["target"] == {"artifact_type": "video.shot", "ref": "t1#s2"}


def test_foreach_empty_array_emits_nothing_even_strict():
    """空数组 = 条件不成立：零事件且不抛错（有稿才产 derived_from 边的机制）。"""
    specs = ({
        "kind": "edge",
        "relation": "derived_from",
        "foreach_from": "data.script_links",
        "source_artifact_type": "video.storyboard",
        "source_ref_from": "data.storyboard_ref",
        "target_artifact_type": "video.script",
        "target_ref_from": "item.script_ref",
    },)
    events = materialize_work_events(
        specs, params={}, data={"storyboard_ref": "t1", "script_links": []},
        tool_id="demo.sb", strict=True,
    )
    assert events == []


def test_events_sorted_artifacts_before_edges_with_foreach():
    """参照完整性：先建节点再建边，foreach 产物同样按优先级排序。"""
    specs = (
        {"kind": "edge", "relation": "contains", "foreach_from": "data.shots",
         "source_artifact_type": "video.storyboard", "source_ref_from": "data.storyboard_ref",
         "target_artifact_type": "video.shot", "target_ref_from": "item.shot_ref"},
        {"kind": "artifact", "artifact_type": "video.shot", "foreach_from": "data.shots",
         "ref_from": "item.shot_ref"},
        {"kind": "artifact", "artifact_type": "video.storyboard", "ref_from": "data.storyboard_ref"},
    )
    data = {"storyboard_ref": "t1", "shots": [{"shot_ref": "t1#s1"}]}
    events = materialize_work_events(specs, params={}, data=data, tool_id="demo.sb", strict=True)
    assert [e["event_type"] for e in events] == [
        "artifact.upsert", "artifact.upsert", "artifact.edge.upsert",
    ]
    assert events[0]["payload"]["artifact_type"] == "video.shot"  # 声明序在前的 artifact 先出
    assert events[1]["payload"]["artifact_type"] == "video.storyboard"


# ---------- strict / 容错分界 ----------


def test_foreach_strict_raises_when_path_missing_or_not_array():
    specs = ({
        "kind": "artifact", "artifact_type": "video.shot",
        "foreach_from": "data.shots", "ref_from": "item.shot_ref",
    },)
    with pytest.raises(ValueError, match="foreach"):
        materialize_work_events(specs, params={}, data={}, tool_id="demo.sb", strict=True)
    with pytest.raises(ValueError, match="foreach"):
        materialize_work_events(
            specs, params={}, data={"shots": "不是数组"}, tool_id="demo.sb", strict=True,
        )
    with pytest.raises(ValueError, match="foreach"):
        materialize_work_events(
            specs, params={}, data={"shots": ["不是对象"]}, tool_id="demo.sb", strict=True,
        )


def test_foreach_non_strict_skips_bad_specs():
    specs = (
        {"kind": "artifact", "artifact_type": "video.shot",
         "foreach_from": "data.shots", "ref_from": "item.shot_ref"},
        {"kind": "artifact", "artifact_type": "video.storyboard", "ref_from": "data.ref"},
    )
    events = materialize_work_events(
        specs, params={}, data={"ref": "t1"}, tool_id="demo.sb",
    )
    assert [e["payload"]["artifact_type"] for e in events] == ["video.storyboard"]


def test_foreach_strict_raises_when_element_lacks_ref():
    """元素里取不到 ref：与普通声明缺 ref 同语义——事务路径必须抛错不丢事件。"""
    specs = ({
        "kind": "artifact", "artifact_type": "video.shot",
        "foreach_from": "data.shots", "ref_from": "item.shot_ref",
    },)
    with pytest.raises(ValueError, match="artifact_type/ref"):
        materialize_work_events(
            specs, params={}, data={"shots": [{"idx": 1}]}, tool_id="demo.sb", strict=True,
        )
