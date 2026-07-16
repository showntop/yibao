"""测试用 FakeHost 及其句柄——照 test_llm.py 的 FakeClient 范式：
记录所有调用、返回可配置的 canned 数据，供真实技能单测断言。
"""
from __future__ import annotations

from typing import Any


class _FakeHandle:
    """Fake 的不透明控件句柄，携带 bbox 供技能编排。"""

    def __init__(self, role: str = "AXButton", title: str = "", bbox=(0.0, 0.0, 10.0, 10.0)):
        self.role = role
        self.title = title
        self.bbox = bbox


class FakeScreenshotter:
    def __init__(self, path: str = "/tmp/yibao-fake.png"):
        self.path = path
        self.calls: list[str] = []

    def capture(self) -> str:
        self.calls.append("capture")
        return self.path


class FakeA11yReader:
    def __init__(self) -> None:
        self.tree: dict = {"role": "AXApp", "title": "FakeApp", "children": []}
        # (role, title) -> handle 查找表；技能测里按需塞
        self.handles: dict[tuple[str | None, str | None], Any] = {}
        self.press_calls: list[Any] = []
        self.set_value_calls: list[tuple[Any, str]] = []
        self.element_at_result: Any | None = None
        self.press_ok: bool = True
        self.set_value_ok: bool = True
        self.launch_pid: int | None = 1234
        self.launch_calls: list[str] = []

    def frontmost_tree(self, max_depth: int = 8) -> dict:
        return self.tree

    def find(self, role: str | None = None, title: str | None = None) -> Any | None:
        return self.handles.get((role, title))

    def bbox(self, handle: Any) -> tuple[float, float, float, float] | None:
        return getattr(handle, "bbox", None)

    def press(self, handle: Any) -> bool:
        self.press_calls.append(handle)
        return self.press_ok

    def set_value(self, handle: Any, text: str) -> bool:
        self.set_value_calls.append((handle, text))
        return self.set_value_ok

    def element_at(self, x: float, y: float) -> Any | None:
        return self.element_at_result

    def launch_app(self, app: str) -> int | None:
        self.launch_calls.append(app)
        return self.launch_pid


class FakeInputInjector:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []
        self.types: list[str] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def type_text(self, text: str) -> None:
        self.types.append(text)


class FakeHost:
    """聚合三个 fake 句柄，实现 Host Protocol。"""

    def __init__(self) -> None:
        self.screenshotter = FakeScreenshotter()
        self.a11y = FakeA11yReader()
        self.input = FakeInputInjector()
