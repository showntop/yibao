"""插件测试套件（v2 方案 §10）：fake ctx 一键构造。

插件作者（人或 agent）用它对 tool 做 TDD；agent 生成插件后的自测（冒烟用例）也跑在这上面。
db 用真实 PluginDb 落在 tmp 目录（行为与线上一致），其余能力全部 fake。
"""
from __future__ import annotations

from pathlib import Path

from .memory import FakeMemory
from .plugindb import PluginDb
from .skills import SkillContext


class FakeHttp:
    """预置 url → 响应；未预置的返回空 dict。calls 记录 (method, url)。"""

    def __init__(self, responses: dict | None = None):
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    def get(self, url: str, **kw):
        self.calls.append(("GET", url))
        return self._responses.get(url, {})

    def post(self, url: str, body: dict | None = None, **kw):
        self.calls.append(("POST", url))
        return self._responses.get(url, {})


class FakeLlm:
    """固定回答；calls 记录每次 prompt。"""

    def __init__(self, text: str = "(fake llm 回答)"):
        self._text = text
        self.calls: list[str] = []

    def chat(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._text


class PanelRecorder:
    """emit_panel 的 fake：把推给面板的事件全记下来。"""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.events.append(payload)


def make_ctx(
    tmp_path: str | Path,
    *,
    plugin_id: str = "test",
    capabilities: frozenset = frozenset({"db", "memory", "http", "llm"}),
    host=None,
    http: FakeHttp | None = None,
    llm: FakeLlm | None = None,
) -> SkillContext:
    """一键构造插件测试 ctx：按 capabilities 注入（未声明的能力为 None，与线上一致）。"""
    data_dir = Path(tmp_path) / "plugins" / plugin_id
    return SkillContext(
        host=host,
        db=PluginDb(plugin_id, str(data_dir / "data.db")) if "db" in capabilities else None,
        memory=FakeMemory() if "memory" in capabilities else None,
        http=(http or FakeHttp()) if "http" in capabilities else None,
        llm=(llm or FakeLlm()) if "llm" in capabilities else None,
        emit_panel=PanelRecorder(),
    )
