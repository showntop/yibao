"""mac a11y 控件树序列化回归测试。

真实 AX 属性可能返回 pyobjc 不桥接的不透明对象（AXUIElementRef 等），
_read_tree 结果若直接进 Event.result，server.py 的 model_dump(mode="json")
会抛 "Unable to serialize unknown type"。这里在 _summarize / _json_safe 层钉死。
"""
import json
from unittest.mock import patch

from yibao_brain.mac import a11y_mac
from yibao_brain.ipc import Event, ActionResult


class _OpaqueRef:
    """模拟 pyobjc 不透明 AXUIElementRef：repr 形如 CoreFoundation 对象。"""

    def __repr__(self) -> str:
        return "<core-foundation class AXUIElementRef at 0xdeadbeef>"


def test_json_safe_passes_primitives_through():
    assert a11y_mac._json_safe(None) is None
    assert a11y_mac._json_safe(True) is True
    assert a11y_mac._json_safe(3) == 3
    assert a11y_mac._json_safe("x") == "x"


def test_json_safe_coerces_opaque_and_nested():
    ref = _OpaqueRef()
    # 不透明对象本身被收成字符串
    assert isinstance(a11y_mac._json_safe(ref), str)
    # 数组/字典里的不透明元素同样收口，其余原样保留
    nested = a11y_mac._json_safe({"k": [ref, 1, "s"]})
    json.dumps(nested, ensure_ascii=False)  # 不抛
    assert isinstance(nested["k"][0], str)
    assert nested["k"][1] == 1 and nested["k"][2] == "s"


def test_summarized_node_with_ax_ref_is_serializable():
    """复现原 bug：value 属性是不透明 AXUIElementRef 时整棵树仍可序列化。"""
    ref = _OpaqueRef()
    attrs = {
        a11y_mac.kAXTitleAttribute: "计算器",
        a11y_mac.kAXRoleAttribute: "AXButton",
        a11y_mac.kAXValueAttribute: ref,  # 原本会让 model_dump 抛错的不透明值
        a11y_mac.kAXEnabledAttribute: True,
        a11y_mac.kAXFocusedAttribute: False,
    }

    def fake_get_attr(el, attr):
        return attrs.get(attr)

    with patch.object(a11y_mac, "_get_attr", fake_get_attr), \
            patch.object(a11y_mac, "_get_children", lambda el: []):
        node = a11y_mac._summarize(object())

    assert node["role"] == "AXButton"
    # json.dumps 路径（_stringify_result / 审计）不抛
    json.dumps({"tree": node}, ensure_ascii=False)
    # server.py 事件序列化路径：pydantic model_dump(mode="json") 不抛
    evt = Event(kind="action_result", result=ActionResult(success=True, data={"tree": node}))
    dumped = evt.model_dump(mode="json")
    assert dumped["result"]["data"]["tree"]["role"] == "AXButton"
    assert isinstance(dumped["result"]["data"]["tree"]["value"], str)
