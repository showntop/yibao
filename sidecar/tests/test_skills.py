from yibao_brain.skills import SkillRegistry, EchoSkill, SkillContext
from yibao_brain.ipc import RiskLevel


def test_echo_skill_runs():
    ctx = SkillContext()
    r = EchoSkill().run({"text": "hello"}, ctx)
    assert r.success and r.data == {"echo": "hello"}


def test_registry_register_get_list():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    assert reg.get("echo").id == "echo"
    assert [s.id for s in reg.list()] == ["echo"]


def test_registry_openai_tools_schema():
    reg = SkillRegistry()
    reg.register(EchoSkill())
    tools = reg.openai_tools()
    assert tools[0]["name"] == "echo"
    assert "parameters" in tools[0]


def test_echo_skill_default_risk_is_low():
    assert EchoSkill().default_risk == RiskLevel.L1_LOW
