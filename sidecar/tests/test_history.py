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


class _SeqProvider:
    """第一次返回 first，之后都返回 second（chat/astream 各自计数）。"""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self._n_chat = 0
        self._n_stream = 0

    def chat(self, messages, tools=None):
        self._n_chat += 1
        src = self._first if self._n_chat == 1 else self._second
        return src.chat(messages, tools)

    async def astream(self, messages, tools=None):
        self._n_stream += 1
        src = self._first if self._n_stream == 1 else self._second
        async for d in src.astream(messages, tools):
            yield d


def test_tool_run_records_full_trace(tmp_path):
    """工具轮整轮入史：user → assistant(tool_calls) → tool → assistant 终复。

    模型模仿历史里的行为模式——只记「请求→文字答复」会教它跳过工具直接声称完成。
    """
    history = ConversationHistory(tmp_path / "h.json")
    provider = _SeqProvider(
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        FakeProvider(text="已回显 hi"),
    )
    loop = build_loop(tmp_path, provider, history)
    events = list(loop.run(" echo 一下"))
    assert events[-1].kind == "final_reply"

    msgs = history.messages()
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1].get("tool_calls"), "assistant 消息必须带 tool_calls（模式的关键）"
    assert msgs[2]["tool_call_id"] == "t1"
    assert msgs[3]["content"] == "已回显 hi"
    # 重启加载后轨迹仍在
    assert [m["role"] for m in ConversationHistory(tmp_path / "h.json").messages()] == [
        "user", "assistant", "tool", "assistant",
    ]


def test_trim_with_tool_traces_keeps_user_boundary(tmp_path):
    """带工具轨迹裁剪只在 user 边界下刀：孤儿 tool 消息会让严格校验的 provider 400。"""
    history = ConversationHistory(tmp_path / "h.json", max_turns=1)
    trace = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "r"},
        {"role": "assistant", "content": "a"},
    ]
    history.record_messages(trace)
    history.record_messages([dict(m) for m in trace])
    msgs = history.messages()
    assert msgs[0]["role"] == "user"
    assert all(m["role"] != "tool" or msgs[i - 1]["role"] == "assistant" for i, m in enumerate(msgs))


def test_tool_content_truncated_in_history(tmp_path):
    history = ConversationHistory(tmp_path / "h.json")
    history.record_messages([
        {"role": "user", "content": "u"},
        {"role": "tool", "tool_call_id": "t1", "content": "x" * 500},
    ])
    content = history.messages()[1]["content"]
    assert len(content) == 301 and content.endswith("…")


def test_load_drops_orphan_head(tmp_path):
    """文件头有孤儿 assistant/tool（手工编辑/旧版裁剪残留）→ 丢弃到第一条 user 为止。"""
    path = tmp_path / "h.json"
    path.write_text(json.dumps([
        {"role": "tool", "tool_call_id": "t9", "content": "孤儿"},
        {"role": "assistant", "content": "也是孤儿"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ], ensure_ascii=False), encoding="utf-8")
    msgs = ConversationHistory(path).messages()
    assert [m["content"] for m in msgs] == ["u", "a"]


def test_panel_surface_tagged_in_history(tmp_path):
    """会话分流：面板场景的轮次落史带 surface 标签；喂模型时剥标签、加【xx 面板】标记。"""
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(text="好")
    loop = build_loop(tmp_path, provider, history)
    list(loop.run("记一条选题", surface="panel:zimeiti"))
    list(loop.run("刚才那条呢"))
    msgs = provider.calls[1]["messages"]
    assert all("surface" not in m for m in msgs)  # provider 不认的字段不许漏过去
    assert {"role": "user", "content": "【zimeiti 面板】记一条选题"} in msgs


def test_pet_surface_not_tagged(tmp_path):
    """pet 是默认场景：不打标签、不加标记（历史与现状一致）。"""
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(text="好")
    loop = build_loop(tmp_path, provider, history)
    list(loop.run("你好", surface="pet"))
    list(loop.run("再说一遍"))
    msgs = provider.calls[1]["messages"]
    assert {"role": "user", "content": "你好"} in msgs


def test_arun_panel_surface_tagged(tmp_path):
    """异步路径同样打标签（voice/panel 走的都是 arun）。"""
    history = ConversationHistory(tmp_path / "h.json")
    provider = FakeProvider(text="好")
    loop = build_loop(tmp_path, provider, history)

    async def twice():
        async for _ in loop.arun("写初稿", surface="panel:zimeiti"):
            pass
        async for _ in loop.arun("继续"):
            pass

    asyncio.run(twice())
    msgs = provider.astream_calls[1]["messages"]
    assert all("surface" not in m for m in msgs)
    assert {"role": "user", "content": "【zimeiti 面板】写初稿"} in msgs
