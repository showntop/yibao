from yibao_brain.tools import ToolRegistry, EchoTool, ToolContext
from yibao_brain.ipc import RiskLevel


def test_echo_skill_runs():
    ctx = ToolContext()
    r = EchoTool().run({"text": "hello"}, ctx)
    assert r.success and r.data == {"echo": "hello"}


def test_registry_register_get_list():
    reg = ToolRegistry()
    reg.register(EchoTool())
    assert reg.get("echo").id == "echo"
    assert [s.id for s in reg.list()] == ["echo"]


def test_registry_openai_tools_schema():
    reg = ToolRegistry()
    reg.register(EchoTool())
    tools = reg.openai_tools()
    assert tools[0]["name"] == "echo"
    assert "parameters" in tools[0]


def test_registry_openai_tools_sanitizes_nested_schema():
    """嵌套 OpenAI 格式（code skill 自带 function 包装）也要改安全名——
    漏改时 function.name 带点号，provider 400 拒整个请求（2026-07-22 实测）。"""
    from yibao_brain.ipc import ActionResult
    from yibao_brain.tools import Tool

    class _Nested(Tool):
        id = "p.x"

        def run(self, params, ctx):
            return ActionResult(success=True)

        def openai_schema(self):
            return {"type": "function",
                    "function": {"name": self.id, "description": "d",
                                 "parameters": {"type": "object", "properties": {}}}}

    reg = ToolRegistry()
    reg.register(_Nested(), plugin="p")
    tools = reg.openai_tools()
    assert tools[0]["function"]["name"] == "p_x"  # 点号必须转下划线
    import re
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", tools[0]["function"]["name"])


def test_echo_skill_default_risk_is_low():
    assert EchoTool().default_risk == RiskLevel.L1_LOW
