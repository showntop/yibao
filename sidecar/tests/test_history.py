"""会话历史：跨 run / 跨进程重启保持上下文（修复「大脑重启后失忆」）。"""
import asyncio
import json

from yibao_brain.audit import AuditLog
from yibao_brain.history import ConversationHistory
from yibao_brain.ipc import RiskLevel
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.loop import AgentLoop
from yibao_brain.memory import FakeMemory
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.skills import EchoSkill, SkillRegistry


def build_loop(tmp_path, provider, history):
    reg = SkillRegistry()
    reg.register(EchoSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        history=history,
    )


def test_second_run_sees_previous_turn(tmp_path):
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(text="好的")
    loop = build_loop(tmp_path, provider, history)
    list(loop.run("我叫小明"))
    list(loop.run("我叫什么"))
    msgs = provider.calls[1]["messages"]
    roles = [(m["role"], m["content"]) for m in msgs]
    assert ("user", "我叫小明") in roles
    assert ("assistant", "好的") in roles
    # 顺序：历史轮次在当前输入之前
    assert msgs.index({"role": "assistant", "content": "好的"}) < msgs.index(
        {"role": "user", "content": "我叫什么"}
    )


def test_history_persists_across_restart(tmp_path):
    path = tmp_path / "h.json"
    loop = build_loop(tmp_path, FakeProvider(text="记住了"), ConversationHistory(path))
    list(loop.run("我喜欢吃辣"))
    # 模拟大脑重启：全新 history 对象从同一文件加载
    restarted = ConversationHistory(path)
    msgs = restarted.messages()
    assert {"role": "user", "content": "我喜欢吃辣"} in msgs
    assert {"role": "assistant", "content": "记住了"} in msgs


def test_history_trimmed_to_max_turns(tmp_path):
    history = ConversationHistory(tmp_path / "h.json", max_turns=2)
    for i in range(3):
        history.record_turn(f"u{i}", f"a{i}")
    contents = [m["content"] for m in history.messages()]
    assert contents == ["u1", "a1", "u2", "a2"]


def test_failed_run_not_recorded(tmp_path):
    # 模型一直要调工具 → 达到最大步数报错，这种失败 run 不应污染历史
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "x"})])
    loop = build_loop(tmp_path, provider, history)
    events = list(loop.run("转圈圈"))
    assert events[-1].kind == "error"
    assert history.messages() == []


def test_arun_also_uses_history(tmp_path):
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(text="异步好的")
    loop = build_loop(tmp_path, provider, history)

    async def twice():
        async for _ in loop.arun("第一句"):
            pass
        async for _ in loop.arun("第二句"):
            pass

    asyncio.run(twice())
    msgs = provider.astream_calls[1]["messages"]
    assert {"role": "user", "content": "第一句"} in msgs
    assert {"role": "assistant", "content": "异步好的"} in msgs


def test_corrupt_history_file_ignored(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{{{not json")
    history = ConversationHistory(path)
    assert history.messages() == []
    # 损坏不碍事：继续记录并覆盖成合法 JSON
    history.record_turn("u", "a")
    assert json.loads(path.read_text())[0]["content"] == "u"
