"""路由式暴露（§12-2）：插件 tool 默认隐藏，use_plugin 按需展开。"""
from __future__ import annotations

import asyncio

from yibao_brain.audit import AuditLog
from yibao_brain.ipc import ActionResult
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.loop import AgentLoop
from yibao_brain.memory import FakeMemory
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.skills import EchoSkill, Skill, SkillRegistry, UsePluginSkill

_SUMMARIES = {"p": {"name": "测试插件", "description": "演示用"}}


class _PSkill(Skill):
    id = "p.x"

    def run(self, params, ctx):
        return ActionResult(success=True, data={"ok": True})


def _registry(active: set) -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(EchoSkill())
    reg.register(_PSkill(), plugin="p")
    reg.register(UsePluginSkill(reg, active, _SUMMARIES))
    return reg


# ---------- registry ----------

def test_openai_tools_filters_inactive_plugins():
    reg = _registry(set())
    all_names = [t["name"] for t in reg.openai_tools()]  # None=全量（兼容）
    assert "p_x" in all_names
    hidden = [t["name"] for t in reg.openai_tools(active_plugins=set())]
    assert "p_x" not in hidden and "echo" in hidden and "use_plugin" in hidden
    shown = [t["name"] for t in reg.openai_tools(active_plugins={"p"})]
    assert "p_x" in shown


def test_plugin_tools_mapping():
    reg = _registry(set())
    assert reg.plugin_tools() == {"p": ["p.x"]}


# ---------- use_plugin 技能 ----------

def test_use_plugin_unknown():
    active: set = set()
    sk = _registry(active).get("use_plugin")
    r = sk.run({"plugin": "nope"}, None)
    assert not r.success and "nope" in r.error


def test_use_plugin_activates_and_reports_tools():
    active: set = set()
    reg = _registry(active)
    r = reg.get("use_plugin").run({"plugin": "p"}, None)
    assert r.success and "p" in active
    assert r.data["tools"] == ["p.x"] and not r.data["already"]
    assert "测试插件" in r.data["human"]
    r2 = reg.get("use_plugin").run({"plugin": "p"}, None)
    assert r2.success and r2.data["already"]  # 幂等


# ---------- loop 集成 ----------

class _RecProvider:
    """记录每次 LLM 调用收到的 tool 名；按脚本顺序委托给 FakeProvider。"""

    def __init__(self, script):
        self._script = list(script)
        self.calls_tools: list[list[str]] = []
        self._n = 0

    def _next(self, tools):
        self.calls_tools.append([t.get("name") or t["function"]["name"] for t in (tools or [])])
        p = self._script[min(self._n, len(self._script) - 1)]
        self._n += 1
        return p

    def chat(self, messages, tools=None):
        return self._next(tools).chat(messages, tools)

    async def astream(self, messages, tools=None):
        async for d in self._next(tools).astream(messages, tools):
            yield d


def _loop(tmp_path, provider, active, focus=None):
    return AgentLoop(
        provider=provider,
        skills=_registry(active),
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        focus_provider=(lambda: focus) if focus else None,
        active_plugins=active,
    )


def test_loop_plugin_hidden_then_expanded(tmp_path):
    active: set = set()
    provider = _RecProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="use_plugin", params={"plugin": "p"})]),
        FakeProvider(text="好了"),
    ])
    events = list(_loop(tmp_path, provider, active).run("用 p 插件"))
    assert "p_x" not in provider.calls_tools[0]  # 初始隐藏
    assert "p_x" in provider.calls_tools[1]      # 展开后下一步可见
    notices = [e for e in events if e.kind == "notice"]
    assert notices and "测试插件" in notices[0].text  # §12-2 要知情
    assert "p" in active


def test_loop_focus_plugin_counts_active(tmp_path):
    active: set = set()
    provider = _RecProvider([FakeProvider(text="hi")])
    list(_loop(tmp_path, provider, active, focus={"plugin": "p", "panel": "main"}).run("这个怎样"))
    assert "p_x" in provider.calls_tools[0]  # 焦点面板所在插件视为激活
    assert "p" not in active  # 但不写进激活集（焦点走了不该留痕）


def test_loop_direct_plugin_call_auto_activates(tmp_path):
    active: set = set()
    provider = _RecProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="p_x", params={})]),  # LLM 直接猜名
        FakeProvider(text="done"),
    ])
    events = list(_loop(tmp_path, provider, active).run("直接调"))
    assert "p" in active  # 执行过即激活，后续步骤工具可见
    assert "p_x" in provider.calls_tools[1]
    assert not [e for e in events if e.kind == "notice"]  # 直接调用不刷提示


def test_loop_arun_same_routing(tmp_path):
    active: set = set()
    provider = _RecProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="use_plugin", params={"plugin": "p"})]),
        FakeProvider(text="好了"),
    ])

    async def collect():
        return [e async for e in _loop(tmp_path, provider, active).arun("用 p 插件")]

    events = asyncio.run(collect())
    assert "p_x" in provider.calls_tools[1]
    assert any(e.kind == "notice" for e in events)
