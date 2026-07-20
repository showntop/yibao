import asyncio

from yibao_brain.loop import AgentLoop
from yibao_brain.llm import FakeProvider, ToolCall, LLMDelta, ToolCallDelta
from yibao_brain.skills import SkillRegistry, EchoSkill, Skill, SkillContext
from yibao_brain.safety import RiskClassifier, Gate, GatePolicy
from yibao_brain.audit import AuditLog
from yibao_brain.memory import FakeMemory
from yibao_brain.ipc import ActionResult, RiskLevel


def build_loop(tmp_path, provider, confirmer=lambda a: True):
    reg = SkillRegistry()
    reg.register(EchoSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )


def test_loop_executes_tool_then_replies(tmp_path):
    # 第一轮模型调用 echo，第二轮给出最终回复
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(tmp_path, provider)
    events = list(loop.run("请回显 hi"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert kinds[-1] == "final_reply"
    assert "echoed: hi" in events[-1].text


def test_loop_confirms_high_risk(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())
    loop = AgentLoop(
        provider=_TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
            second=FakeProvider(text="done"),
        ),
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=lambda a: False,  # 用户拒绝
    )
    events = list(loop.run("做危险的事"))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    # 拒绝后不执行 danger
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


class _TwoStepProvider:
    """第一次返回 first，之后都返回 second。chat/astream 各自计数（互不干扰）。"""

    def __init__(self, first, second):
        self._first = first
        self._second = second
        self._n_chat = 0
        self._n_stream = 0

    def chat(self, messages, tools=None):
        self._n_chat += 1
        return self._first.chat(messages, tools) if self._n_chat == 1 else self._second.chat(messages, tools)

    async def astream(self, messages, tools=None):
        self._n_stream += 1
        src = self._first if self._n_stream == 1 else self._second
        async for d in src.astream(messages, tools):
            yield d


async def _collect_events(agen):
    out = []
    async for e in agen:
        out.append(e)
    return out


def test_loop_arun_streams_chunks_then_final(tmp_path):
    provider = FakeProvider(chunks=["你好", "，我是", "译宝"])
    loop = build_loop(tmp_path, provider)
    events = asyncio.run(_collect_events(loop.arun("hi")))
    kinds = [e.kind for e in events]
    assert kinds[:-1] == ["final_reply_chunk", "final_reply_chunk", "final_reply_chunk"]
    assert kinds[-1] == "final_reply"
    assert events[-1].text == "你好，我是译宝"


def test_loop_arun_executes_tool_then_streams_reply(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(chunks=["echoed:", " hi"]),
    )
    loop = build_loop(tmp_path, provider)
    events = asyncio.run(_collect_events(loop.arun("请回显 hi")))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert "final_reply_chunk" in kinds
    assert kinds[-1] == "final_reply"
    assert events[-1].text == "echoed: hi"


def test_loop_arun_interrupt_mid_stream(tmp_path):
    async def _go():
        provider = FakeProvider(chunks=["A", "B", "C", "D"], delay=0.02)
        loop = build_loop(tmp_path, provider)
        cancel = asyncio.Event()

        async def _trip():
            await asyncio.sleep(0.01)
            cancel.set()

        asyncio.ensure_future(_trip())
        return await _collect_events(loop.arun("hi", cancel))

    events = asyncio.run(_go())
    kinds = [e.kind for e in events]
    assert "interrupted" in kinds
    assert "final_reply" not in kinds


def test_loop_arun_async_confirmer_rejected(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())

    async def confirmer(_action):
        return False  # 异步 confirmer 返回协程

    loop = AgentLoop(
        provider=_TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
            second=FakeProvider(text="done"),
        ),
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )
    events = asyncio.run(_collect_events(loop.arun("做危险的事")))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    assert "error" in kinds
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


def test_loop_arun_assistant_msg_carries_tool_calls(tmp_path):
    # 回归：DeepSeek 严格校验——tool 消息前 assistant 必须带 tool_calls（曾 400）
    class _Recording:
        def __init__(self):
            self.seen: list[list[dict]] = []
            self._n = 0

        async def astream(self, messages, tools=None):
            self.seen.append([dict(m) for m in messages])
            self._n += 1
            if self._n == 1:
                yield LLMDelta(
                    tool_call_deltas=[
                        ToolCallDelta(index=0, id="c1", skill_id="echo", arguments='{"text":"hi"}')
                    ]
                )
            else:
                yield LLMDelta(text="done")

    prov = _Recording()
    loop = build_loop(tmp_path, prov)
    asyncio.run(_collect_events(loop.arun("回显 hi")))
    assert len(prov.seen) == 2  # 第二轮请求存在
    second = prov.seen[1]
    asst = [m for m in second if m.get("role") == "assistant"][-1]
    assert "tool_calls" in asst, "assistant 消息缺 tool_calls → DeepSeek 会 400"
    assert asst["tool_calls"][0]["function"]["name"] == "echo"
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in second)


class _RaisingLog:
    """record 永远失败的审计日志（模拟 UNIQUE 冲突/磁盘故障）。"""

    def record(self, *a, **kw):
        raise RuntimeError("UNIQUE constraint failed: actions.id")

    def recent(self, n=50):
        return []


def test_loop_survives_audit_failure(tmp_path):
    # 审计写库失败不应炸掉整个 run，用户仍拿到回复
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="echoed: hi"),
    )
    loop = build_loop(tmp_path, provider)
    loop.log = _RaisingLog()
    events = list(loop.run("请回显 hi"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert kinds[-1] == "final_reply"


# ---------- Plan 5 修复：arun 不把同步阻塞调用压在事件循环上 ----------


def test_arun_runs_skill_and_memory_off_loop_thread(tmp_path):
    """skill.run / memory.recall / memory.add 是同步阻塞实现（HTTP/torch），
    必须在线程池执行，否则冻结事件循环 → 看门狗 15s 无 pong 杀大脑。"""
    import threading

    main_tid = threading.get_ident()
    seen: dict[str, int] = {}

    class SlowEcho(EchoSkill):
        def run(self, params, ctx):
            seen["skill"] = threading.get_ident()
            return super().run(params, ctx)

    class SpyMemory(FakeMemory):
        def recall(self, query, user_id):
            seen["recall"] = threading.get_ident()
            return super().recall(query, user_id)

        def add(self, text, user_id):
            seen["add"] = threading.get_ident()
            return super().add(text, user_id)

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "hi"})]),
        second=FakeProvider(text="done"),
    )
    loop = build_loop(tmp_path, provider)
    reg = SkillRegistry()
    reg.register(SlowEcho())
    loop.skills = reg
    loop.memory = SpyMemory()

    async def _go():
        return [e async for e in loop.arun("hi")]

    asyncio.run(_go())
    assert seen["skill"] != main_tid
    assert seen["recall"] != main_tid
    assert seen["add"] != main_tid


def test_arun_skill_exception_becomes_tool_error(tmp_path):
    """技能抛异常 → 失败的 action_result 喂回模型，run 继续到 final_reply（不死）。"""
    class BoomSkill(Skill):
        id = "boom"
        description = "必炸"
        def run(self, params, ctx):
            raise RuntimeError("炸了")

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="boom", params={})]),
        second=FakeProvider(text="换个法子完成了"),
    )
    loop = build_loop(tmp_path, provider)
    reg = SkillRegistry()
    reg.register(BoomSkill())
    loop.skills = reg

    async def _go():
        return [e async for e in loop.arun("炸一下")]

    events = asyncio.run(_go())
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    ar = next(e for e in events if e.kind == "action_result")
    assert ar.result.success is False
    assert "炸了" in ar.result.error
    assert kinds[-1] == "final_reply"


def test_run_skill_exception_becomes_tool_error(tmp_path):
    """同步 run() 路径同上。"""
    class BoomSkill(Skill):
        id = "boom"
        description = "必炸"
        def run(self, params, ctx):
            raise RuntimeError("炸了")

    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="boom", params={})]),
        second=FakeProvider(text="换个法子完成了"),
    )
    loop = build_loop(tmp_path, provider)
    reg = SkillRegistry()
    reg.register(BoomSkill())
    loop.skills = reg
    events = list(loop.run("炸一下"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    assert kinds[-1] == "final_reply"


# ---------- ⑤a：action_result 之后的 panel 事件 ----------


class _PanelSkill(Skill):
    """返回带 panel 引用的结果（ref 由测试用 monkeypatch 注入 _PANELS）。"""

    id = "paneldemo"
    description = "演示 panel 事件"

    def __init__(self, ref="notes:list", data=None):
        self._ref = ref
        self._data = data if data is not None else {"rows": [1]}

    def run(self, params, ctx):
        return ActionResult(success=True, data=self._data, panel=self._ref)


def _build_panel_loop(tmp_path, provider, skill):
    reg = SkillRegistry()
    reg.register(skill)
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
    )


def test_run_emits_panel_event_after_action_result(tmp_path, monkeypatch):
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "notes:list", {"type": "list"})
    monkeypatch.delitem(plugins._PANEL_TITLES, "notes:list", raising=False)  # 全局注册表可能被其他测试写入，隔离为缺省 ref
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="paneldemo", params={})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_panel_loop(tmp_path, provider, _PanelSkill())
    events = list(loop.run("go"))
    kinds = [e.kind for e in events]
    assert kinds.index("panel") == kinds.index("action_result") + 1  # 紧跟其后
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload == {"panel": "notes:list", "title": "notes:list", "schema": {"type": "list"}, "data": {"rows": [1]}}


def test_arun_emits_panel_event_after_action_result(tmp_path, monkeypatch):
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "notes:list", {"type": "list"})
    monkeypatch.delitem(plugins._PANEL_TITLES, "notes:list", raising=False)  # 全局注册表可能被其他测试写入，隔离为缺省 ref
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="paneldemo", params={})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_panel_loop(tmp_path, provider, _PanelSkill())
    events = asyncio.run(_collect_events(loop.arun("go")))
    kinds = [e.kind for e in events]
    assert kinds.index("panel") == kinds.index("action_result") + 1
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload["schema"] == {"type": "list"} and pe.payload["data"] == {"rows": [1]}


def test_panel_event_unknown_schema_gives_none(tmp_path):
    # schema 找不到：payload.schema = None，不炸（前端做未知降级）
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="paneldemo", params={})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_panel_loop(tmp_path, provider, _PanelSkill(ref="zz:ghost"))
    events = list(loop.run("go"))
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload == {"panel": "zz:ghost", "title": "zz:ghost", "schema": None, "data": {"rows": [1]}}


def test_no_panel_event_without_ref(tmp_path):
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "x"})]),
        second=FakeProvider(text="done"),
    )
    loop = build_loop(tmp_path, provider)
    events = list(loop.run("go"))
    assert "panel" not in [e.kind for e in events]  # 无 panel 引用不发事件


def test_plugin_tool_names_are_llm_safe(tmp_path):
    """插件 tool id 带点号（notes.keep），DeepSeek/OpenAI 要求 function name ^[a-zA-Z0-9_-]+$：
    发给 LLM 的 schema 用安全名（点→下划线），回调时映射回真实 id。"""
    from yibao_brain.skills import SkillRegistry

    class Keep(Skill):
        id = "notes.keep"
        description = "记"

        def run(self, params, ctx):
            raise NotImplementedError

    reg = SkillRegistry()
    reg.register(EchoSkill())
    reg.register(Keep(), plugin="notes")

    names = [t["name"] for t in reg.openai_tools()]
    assert "notes_keep" in names          # 点号转下划线
    assert "notes.keep" not in names      # 非法字符不进 schema
    assert "echo" in names                # 底座 id 原样
    assert reg.resolve_llm_name("notes_keep") == "notes.keep"
    assert reg.resolve_llm_name("echo") == "echo"
    assert reg.resolve_llm_name("ghost") == "ghost"  # 未知名原样返回（走既有的 skill 未找到路径）


def test_loop_executes_plugin_tool_called_by_safe_name(tmp_path):
    """端到端：LLM 回调安全名 notes_keep，loop 映射回 notes.keep 并执行。"""
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="notes_keep", params={"text": "hi"})]),
        second=FakeProvider(text="记好了"),
    )
    loop = build_loop(tmp_path, provider)
    from yibao_brain.skills import SkillRegistry
    from yibao_brain.ipc import ActionResult as AR

    class Keep(Skill):
        id = "notes.keep"
        description = "记"
        def run(self, params, ctx):
            return AR(success=True, data={"kept": params.get("text")})

    reg = SkillRegistry()
    reg.register(Keep(), plugin="notes")
    loop.skills = reg
    events = list(loop.run("记一下 hi"))
    kinds = [e.kind for e in events]
    assert "action_result" in kinds
    ar = next(e for e in events if e.kind == "action_result")
    assert ar.result.success and ar.result.data == {"kept": "hi"}
    assert kinds[-1] == "final_reply"


def _build_focus_loop(tmp_path, provider, focus):
    reg = SkillRegistry()
    reg.register(EchoSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        focus_provider=lambda: focus,
    )


def test_focus_injected_as_system_message(tmp_path):
    """面板焦点存在时，run 的消息里带一条「用户当前正在看」的 system 消息。"""
    provider = FakeProvider(text="这条选题角度可以")
    focus = {
        "plugin": "zimeiti",
        "panel": "detail",
        "item": {"id": "abc123", "title": "K3 是垃圾", "status": "writing"},
    }
    loop = _build_focus_loop(tmp_path, provider, focus)
    list(loop.run("这个怎么样"))
    messages = provider.calls[0]["messages"]
    focus_msgs = [m for m in messages if m["role"] == "system" and "用户当前正在看" in m["content"]]
    assert len(focus_msgs) == 1
    content = focus_msgs[0]["content"]
    assert "zimeiti" in content and "detail" in content
    assert "K3 是垃圾" in content and "abc123" in content and "writing" in content
    assert "这个/它" in content


def test_focus_none_injects_nothing(tmp_path):
    """无焦点（None / 空 dict / 缺 plugin）时不注入额外 system 消息。"""
    for focus in (None, {}, {"panel": "board"}):
        provider = FakeProvider(text="你好")
        loop = _build_focus_loop(tmp_path, provider, focus)
        list(loop.run("你好"))
        messages = provider.calls[0]["messages"]
        assert not any("用户当前正在看" in m["content"] for m in messages if m["role"] == "system")


def test_focus_without_item_has_no_pronoun_hint(tmp_path):
    """焦点只有面板没有选中条目时，不出现「这个/它」指代提示。"""
    provider = FakeProvider(text="看板上有 3 条")
    loop = _build_focus_loop(tmp_path, provider, {"plugin": "zimeiti", "panel": "board"})
    list(loop.run("有几条选题"))
    messages = provider.calls[0]["messages"]
    focus_msg = next(m for m in messages if m["role"] == "system" and "用户当前正在看" in m["content"])
    assert "zimeiti" in focus_msg["content"] and "board" in focus_msg["content"]
    assert "这个/它" not in focus_msg["content"]


def test_focus_provider_exception_is_ignored(tmp_path):
    """focus_provider 抛异常时对话照常，不注入焦点消息。"""
    provider = FakeProvider(text="ok")

    def boom():
        raise RuntimeError("focus gone")

    reg = SkillRegistry()
    reg.register(EchoSkill())
    loop = AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        focus_provider=boom,
    )
    events = list(loop.run("你好"))
    assert events[-1].kind == "final_reply"
    messages = provider.calls[0]["messages"]
    assert not any("用户当前正在看" in m["content"] for m in messages if m["role"] == "system")
