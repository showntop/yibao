"""感知/执行基座抽象：技能经 SkillContext.host 访问截图、a11y、键鼠注入。

设计：host 只提供「原语」，技能（skills_real.py）负责编排三级回退逻辑
（AX 动作 → AX 设值 → 坐标）。handle 为平台相关的不透明对象
（macOS = AXUIElementRef），跨平台代码不要假设其内部结构。
"""
from __future__ import annotations

from typing import Any, Protocol


class Screenshotter(Protocol):
    def capture(self) -> str:
        """截当前主屏，存 png，返回绝对路径。"""
        ...


class A11yReader(Protocol):
    def frontmost_tree(self, max_depth: int = 8) -> dict:
        """前台 app 的 a11y 控件树摘要（title/role/bbox/enabled/children），限深限宽。"""
        ...

    def find(self, role: str | None = None, title: str | None = None) -> Any | None:
        """在前台树里按 role/title 模糊查找控件，返回 handle 或 None。"""
        ...

    def bbox(self, handle: Any) -> tuple[float, float, float, float] | None:
        """handle 的包围盒 (x, y, w, h)，左上角原点屏幕坐标；失败 None。"""
        ...

    def press(self, handle: Any) -> bool:
        """对控件触发主动作（AXButton=Press / AXMenuItem=Pick）。"""
        ...

    def set_value(self, handle: Any, text: str) -> bool:
        """对文本控件直接设值（AXValue）。不可写则返回 False。"""
        ...

    def element_at(self, x: float, y: float) -> Any | None:
        """屏幕坐标命中测试，返回该坐标的控件 handle 或 None。"""
        ...

    def launch_app(self, app: str) -> int | None:
        """打开应用并返回其 PID；失败返回 None。"""
        ...


class InputInjector(Protocol):
    def click(self, x: float, y: float) -> None:
        """在屏幕坐标 (x,y) 左键点击。"""
        ...

    def type_text(self, text: str) -> None:
        """输入文本：ASCII 走键入，含中文走剪贴板粘贴。"""
        ...


class Host(Protocol):
    """执行/感知基座聚合体，注入 SkillContext.host。"""

    screenshotter: Screenshotter
    a11y: A11yReader
    input: InputInjector
