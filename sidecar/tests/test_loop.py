import asyncio
import json
import time

from yibao_brain.loop import AgentLoop, SYSTEM_PROMPT
from yibao_brain.llm import FakeProvider, ToolCall, LLMDelta, ToolCallDelta
from yibao_brain.skills import SkillRegistry, EchoSkill, Skill, SkillContext
from yibao_brain.safety import RiskClassifier, Gate, GatePolicy
from yibao_brain.audit import AuditLog
from yibao_brain.history import ConversationHistory
from yibao_brain.memory import FakeMemory
from yibao_brain.ipc import ActionResult, RiskLevel


class _SensitiveSkill(Skill):
    id = "sensitive"
    description = "返回敏感数据"
    default_risk = RiskLevel.L0_READONLY
    sensitive_output = True

    def run(self, params, ctx):
        return ActionResult(success=True, data={"secret": "Window Secret", "count": 1})

    def safe_result(self, result):
        return ActionResult(success=result.success, error=result.error, data={"count": 1})

    def post_reply_notice(self, result):
        return "已参考敏感上下文" if result.success else None


def build_loop(tmp_path, provider, confirmer=lambda actions: {a.id: (True, False) for a in actions}):
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


def _build_sensitive_loop(tmp_path, provider):
    reg = SkillRegistry()
    reg.register(_SensitiveSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        history=ConversationHistory(tmp_path / "h.json"),
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


def test_loop_sensitive_result_is_full_for_model_but_safe_for_shell_and_history(tmp_path):
    first = FakeProvider(
        tool_calls=[ToolCall(id="t1", skill_id="sensitive", params={})]
    )
    second = FakeProvider(text="你刚才在 Window Secret")
    loop = _build_sensitive_loop(tmp_path, _TwoStepProvider(first, second))

    events = list(loop.run("我刚才在干嘛"))

    assert "Window Secret" in json.dumps(second.astream_calls[0]["messages"], ensure_ascii=False)
    action_result = next(e for e in events if e.kind == "action_result")
    assert action_result.result.data == {"count": 1}
    assert [(e.kind, e.text) for e in events[-2:]] == [
        ("final_reply", "你刚才在 Window Secret"),
        ("notice", "已参考敏感上下文"),
    ]
    disk = (tmp_path / "h.json").read_text(encoding="utf-8")
    assert "Window Secret" not in disk
    assert "本轮使用敏感工具回答，敏感内容未写入会话历史" in disk


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
        confirmer=lambda actions: {a.id: (False, False) for a in actions},  # 用户拒绝（批量接口）
    )
    events = list(loop.run("做危险的事"))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    confirmation = next(e for e in events if e.kind == "confirmation_needed")
    rejected = next(e for e in events if e.kind == "error" and "用户拒绝" in (e.text or ""))
    assert rejected.action is not None and confirmation.action is not None
    assert rejected.action.id == confirmation.action.id
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


class _SequenceProvider:
    def __init__(self, providers):
        self.providers = list(providers)
        self.chat_calls = []
        self.astream_calls = []

    def chat(self, messages, tools=None):
        self.chat_calls.append({"messages": messages, "tools": tools})
        return self.providers.pop(0).chat(messages, tools)

    async def astream(self, messages, tools=None):
        self.astream_calls.append({"messages": messages, "tools": tools})
        provider = self.providers.pop(0)
        async for delta in provider.astream(messages, tools):
            yield delta


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


def test_system_prompt_avoids_redundant_screenshot_around_computer_use():
    assert "computer_use 会自行截图" in SYSTEM_PROMPT
    assert "不要在 computer_use 前后重复调用 screenshot" in SYSTEM_PROMPT


def test_system_prompt_steers_interactive_coding_to_panel():
    # coding 分工 steer：交互式 coding 引导去「编码面板」，dispatch_task 仅后台（DIV1）
    assert "编码面板" in SYSTEM_PROMPT
    assert "dispatch_task" in SYSTEM_PROMPT


def test_loop_sync_reserves_final_reply_after_tool_budget(tmp_path):
    provider = _SequenceProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "1"})]),
        FakeProvider(tool_calls=[ToolCall(id="t2", skill_id="echo", params={"text": "2"})]),
        FakeProvider(text="已到安全上限，停止继续操作"),
    ])
    loop = build_loop(tmp_path, provider)
    loop.max_steps = 2

    events = list(loop.run("连续操作"))

    assert events[-1].kind == "final_reply"
    assert events[-1].text == "已到安全上限，停止继续操作"
    assert not any(e.kind == "error" and "最大步数" in (e.text or "") for e in events)
    assert len(provider.astream_calls) == 3
    assert provider.astream_calls[-1]["tools"] == []


def test_loop_arun_reserves_final_reply_after_tool_budget(tmp_path):
    provider = _SequenceProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="echo", params={"text": "1"})]),
        FakeProvider(tool_calls=[ToolCall(id="t2", skill_id="echo", params={"text": "2"})]),
        FakeProvider(text="已到安全上限，停止继续操作"),
    ])
    loop = build_loop(tmp_path, provider)
    loop.max_steps = 2

    events = asyncio.run(_collect_events(loop.arun("连续操作")))

    assert events[-1].kind == "final_reply"
    assert events[-1].text == "已到安全上限，停止继续操作"
    assert not any(e.kind == "error" and "最大步数" in (e.text or "") for e in events)
    assert len(provider.astream_calls) == 3
    assert provider.astream_calls[-1]["tools"] == []


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


def test_loop_arun_exposes_threadsafe_cancel_to_interactive_skill(tmp_path):
    from yibao_brain.ipc import ActionResult
    from yibao_brain.skills import Skill, SkillRegistry

    class UserPreemptSkill(Skill):
        id = "user_preempt"

        def run(self, params, ctx):
            ctx.meta["request_cancel"]()
            return ActionResult(success=False, error="检测到用户正在操作")

    reg = SkillRegistry()
    reg.register(UserPreemptSkill())
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="user_preempt", params={})]),
        second=FakeProvider(text="不应继续生成"),
    )
    loop = AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
    )
    cancel = asyncio.Event()

    events = asyncio.run(_collect_events(loop.arun("操作电脑", cancel)))

    assert cancel.is_set()
    assert any(e.kind == "interrupted" for e in events)
    assert provider._n_stream == 1


def test_loop_arun_sensitive_result_is_full_for_model_but_safe_for_shell_and_history(tmp_path):
    first = FakeProvider(
        tool_calls=[ToolCall(id="t1", skill_id="sensitive", params={})]
    )
    second = FakeProvider(chunks=["你刚才在 ", "Window Secret"])
    loop = _build_sensitive_loop(tmp_path, _TwoStepProvider(first, second))

    events = asyncio.run(_collect_events(loop.arun("我刚才在干嘛")))

    assert "Window Secret" in json.dumps(
        second.astream_calls[0]["messages"], ensure_ascii=False
    )
    action_result = next(e for e in events if e.kind == "action_result")
    assert action_result.result.data == {"count": 1}
    assert [(e.kind, e.text) for e in events[-2:]] == [
        ("final_reply", "你刚才在 Window Secret"),
        ("notice", "已参考敏感上下文"),
    ]
    disk = (tmp_path / "h.json").read_text(encoding="utf-8")
    assert "Window Secret" not in disk
    assert "本轮使用敏感工具回答，敏感内容未写入会话历史" in disk


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


def test_loop_arun_passes_cancel_into_running_skill(tmp_path):
    seen = {"cancel": False}

    class WaitSkill(Skill):
        id = "wait"
        description = "等待中断"

        def run(self, params, ctx):
            cancel = ctx.meta.get("cancel")
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                if cancel is not None and cancel.is_set():
                    seen["cancel"] = True
                    break
                time.sleep(0.005)
            return ActionResult(success=True)

    async def _go():
        reg = SkillRegistry()
        reg.register(WaitSkill())
        provider = _TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="wait", params={})]),
            second=FakeProvider(text="done"),
        )
        loop = AgentLoop(
            provider=provider,
            skills=reg,
            classifier=RiskClassifier(),
            gate=Gate(GatePolicy()),
            memory=FakeMemory(),
            log=AuditLog(tmp_path / "a.db"),
        )
        cancel = asyncio.Event()

        async def _trip():
            await asyncio.sleep(0.02)
            cancel.set()

        asyncio.ensure_future(_trip())
        return await _collect_events(loop.arun("等待", cancel))

    events = asyncio.run(_go())

    assert seen["cancel"] is True
    assert any(e.kind == "interrupted" for e in events)


def test_loop_arun_async_confirmer_rejected(tmp_path):
    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())

    async def confirmer(actions):
        # 异步批量 confirmer：全拒绝
        return {a.id: (False, False) for a in actions}

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
    confirmation = next(e for e in events if e.kind == "confirmation_needed")
    rejected = next(e for e in events if e.kind == "error" and "用户拒绝" in (e.text or ""))
    assert rejected.action is not None and confirmation.action is not None
    assert rejected.action.id == confirmation.action.id
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


def test_loop_arun_remember_verdict_adds_to_session_allowed(tmp_path):
    """Task 1：verdict 含 remember=True 且 approved → loop 把 skill_id 写进 gate.session_allowed，
    后续同 skill 即便风险高也走 AUTO（不弹 confirmation_needed）。"""
    class DangerSkill(Skill):
        id = "danger"
        description = "危险占位"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(DangerSkill())

    # 一次批量 confirmer：对第一个 action 批准+remember，后续全 AUTO 不再调 confirmer
    calls = []

    async def confirmer(actions):
        calls.append([a.id for a in actions])
        return {a.id: (True, True) for a in actions}

    class _SeqProvider:
        def __init__(self, responses):
            self._responses = list(responses)
            self._n = 0

        async def astream(self, messages, tools=None):
            i = min(self._n, len(self._responses) - 1)
            self._n += 1
            async for d in self._responses[i].astream(messages, tools):
                yield d

    provider = _SeqProvider([
        FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="danger", params={})]),
        FakeProvider(text="第一次完成"),
        FakeProvider(tool_calls=[ToolCall(id="t2", skill_id="danger", params={})]),
        FakeProvider(text="第二次完成"),
    ])
    loop = AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )
    events = asyncio.run(_collect_events(loop.arun("做危险的事")))

    # 第一次确认后 session_allowed 写入 danger；第二次不再 confirmation_needed
    confirms = [e for e in events if e.kind == "confirmation_needed"]
    assert len(confirms) == 1
    assert "danger" in loop.invoker.gate.session_allowed
    # confirmer 只被调一次（第二次 AUTO，不进 CONFIRM 分支）
    assert len(calls) == 1


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
    assert pe.payload["panel"] == "notes:list"
    assert pe.payload["title"] == "notes:list"
    assert pe.payload["schema"] == {"type": "list"}
    assert pe.payload["data"] == {"rows": [1]}
    # 旧插件不声明表面 → 默认值（presentation=None 宿主按老规则推断）
    assert pe.payload["presentation"] is None
    assert pe.payload["attention"] == "suggest"


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
    assert pe.payload["panel"] == "zz:ghost"
    assert pe.payload["title"] == "zz:ghost"
    assert pe.payload["schema"] is None
    assert pe.payload["data"] == {"rows": [1]}


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
    messages = provider.astream_calls[0]["messages"]
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
        messages = provider.astream_calls[0]["messages"]
        assert not any("用户当前正在看" in m["content"] for m in messages if m["role"] == "system")


def test_focus_without_item_has_no_pronoun_hint(tmp_path):
    """焦点只有面板没有选中条目时，不出现「这个/它」指代提示。"""
    provider = FakeProvider(text="看板上有 3 条")
    loop = _build_focus_loop(tmp_path, provider, {"plugin": "zimeiti", "panel": "board"})
    list(loop.run("有几条选题"))
    messages = provider.astream_calls[0]["messages"]
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
    messages = provider.astream_calls[0]["messages"]
    assert not any("用户当前正在看" in m["content"] for m in messages if m["role"] == "system")


# ---------- refresh 传参交集 + focus 重定向到 webview ----------


class _SaveSkill(Skill):
    """写操作：panel=detail、refresh=get，入参 {id, content}。"""

    id = "w.save"
    description = "保存"
    refresh = "w.get"

    def run(self, params, ctx):
        return ActionResult(success=True, data={"id": params.get("id")}, panel="w:detail")


class _GetSkill(Skill):
    """只读查询：声明接受 {id, version}；记录实际收到的 params。"""

    id = "w.get"
    description = "查询"
    default_risk = RiskLevel.L0_READONLY

    def __init__(self):
        self.seen: list[dict] = []

    def openai_schema(self):
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "version": {"type": "integer"}},
                "required": [],
            },
        }

    def run(self, params, ctx):
        self.seen.append(dict(params))
        return ActionResult(success=True, data={"rows": [{"id": "t1", "status": "写作中"}]}, panel="w:detail")


def _build_w_loop(tmp_path, get_skill, focus=None):
    reg = SkillRegistry()
    reg.register(_SaveSkill(), plugin="w")
    reg.register(get_skill, plugin="w")
    return AgentLoop(
        provider=_TwoStepProvider(
            first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="w.save", params={"id": "t1", "content": "正文"})]),
            second=FakeProvider(text="已保存"),
        ),
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        focus_provider=lambda: focus,
    )


def test_refresh_receives_param_intersection(tmp_path, monkeypatch):
    """refresh 传参 = action 入参 ∩ refresh tool 声明参数（save{id,content} → get{id}，content 不透）。"""
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "w:detail", {"type": "detail"})
    get = _GetSkill()
    loop = _build_w_loop(tmp_path, get)
    events = list(loop.run("存一下"))
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload["data"] == {"rows": [{"id": "t1", "status": "写作中"}]}
    assert get.seen == [{"id": "t1"}]  # content 不透传


def test_focus_redirects_panel_to_webview_editor(tmp_path, monkeypatch):
    """用户正盯着 w:editor（webview）的条目 t1：写操作回跳改落 w:editor，不硬切 detail。"""
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "w:detail", {"type": "detail"})
    monkeypatch.setitem(plugins._PANELS, "w:editor", {"type": "webview", "html": "<html>editor</html>"})
    monkeypatch.setitem(plugins._PANEL_TITLES, "w:editor", "W · 编辑器")
    get = _GetSkill()
    focus = {"plugin": "w", "panel": "editor", "item": {"id": "t1", "title": "选题"}}
    loop = _build_w_loop(tmp_path, get, focus=focus)
    events = list(loop.run("改一下"))
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload["panel"] == "w:editor"
    assert pe.payload["webview"] == {"html": "<html>editor</html>"}
    assert pe.payload["schema"] is None
    assert pe.payload["data"] == {"rows": [{"id": "t1", "status": "写作中"}]}


def test_focus_other_item_does_not_redirect(tmp_path, monkeypatch):
    """focus 是另一条目（t9）时不动：回跳仍是 detail。"""
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "w:detail", {"type": "detail"})
    monkeypatch.setitem(plugins._PANELS, "w:editor", {"type": "webview", "html": "<html>editor</html>"})
    get = _GetSkill()
    focus = {"plugin": "w", "panel": "editor", "item": {"id": "t9", "title": "别的"}}
    loop = _build_w_loop(tmp_path, get, focus=focus)
    events = list(loop.run("改一下"))
    pe = next(e for e in events if e.kind == "panel")
    assert pe.payload["panel"] == "w:detail"


# ---------- Task 4：新记忆按小时合并写 Feed ----------


class _FakeFeed:
    """记录 append_hourly 调用（不落库）；用于断言 loop 在新记忆时是否写 Feed。"""

    def __init__(self):
        self.calls: list[str] = []

    def append_hourly(self, kind, text, meta, hour_key):
        self.calls.append("append_hourly")


class _NoopMem(FakeMemory):
    """记忆 add 永远返回 False（去重/降级场景）——不应触发 Feed 写入。"""

    def add(self, text, user_id):
        return False


def _empty_reg() -> SkillRegistry:
    """空技能注册表：无工具调用场景下给 AgentLoop 占位。"""
    return SkillRegistry()


def test_loop_writes_feed_when_memory_added(tmp_path):
    """memory.add 返回 True（新增事实）→ 按当前小时写一条「记住了：…」Feed。"""
    feed = _FakeFeed()
    loop = AgentLoop(
        provider=FakeProvider(text="好的"),
        skills=_empty_reg(),
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        feed=feed,
    )
    list(loop.run("我喜欢美式", surface="pet"))
    assert feed.calls == ["append_hourly"]


def test_loop_skips_feed_when_memory_noop(tmp_path):
    """memory.add 返回 False（去重/降级）→ 不写 Feed，避免污染主屏。"""
    feed = _FakeFeed()
    loop = AgentLoop(
        provider=FakeProvider(text="嗯嗯"),
        skills=_empty_reg(),
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),
        memory=_NoopMem(),
        log=AuditLog(tmp_path / "a.db"),
        feed=feed,
    )
    list(loop.run("嗨", surface="pet"))
    assert feed.calls == []


# ---------- Task 2：一轮多 CONFIRM 攒批 + 按 LLM 顺序执行 ----------


class _HighSkill(Skill):
    """L3 高危技能，默认走 CONFIRM；run 记录调用顺序到共享 list。"""

    id = "h"
    description = "高危占位"
    default_risk = RiskLevel.L3_HIGH

    def __init__(self, log: list[str] | None = None, key: str = "h"):
        self._log = log
        self._key = key

    def run(self, params, ctx):
        if self._log is not None:
            self._log.append(self._key)
        return ActionResult(success=True, data={"did": params.get("x")})


def _build_batch_loop(tmp_path, provider, confirmer, reg=None):
    if reg is None:
        reg = SkillRegistry()
        reg.register(_HighSkill())
    return AgentLoop(
        provider=provider,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(tmp_path / "a.db"),
        confirmer=confirmer,
    )


def test_run_batches_multiple_confirms_one_event(tmp_path):
    """一轮两个 CONFIRM tool_call → 一次 confirmation_needed（actions 长 2，旧 action=actions[0]）。"""
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="a", skill_id="h", params={"x": "A"}),
            ToolCall(id="b", skill_id="h", params={"x": "B"}),
        ]),
        second=FakeProvider(text="done"),
    )
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (True, False) for a in actions}
    )
    events = list(loop.run("做两件事"))
    cns = [e for e in events if e.kind == "confirmation_needed"]
    assert len(cns) == 1                       # 只推一次收件箱
    assert [a.params["x"] for a in cns[0].actions] == ["A", "B"]
    assert cns[0].action is cns[0].actions[0]  # 旧前端兼容
    assert cns[0].confirmation_id == cns[0].actions[0].id


def test_arun_batches_multiple_confirms_executes_in_llm_order(tmp_path):
    """批量批准后按 LLM 顺序执行（A 在 B 前）。"""
    order: list[str] = []
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="a", skill_id="h", params={"x": "A"}),
            ToolCall(id="b", skill_id="h", params={"x": "B"}),
        ]),
        second=FakeProvider(text="done"),
    )
    reg = SkillRegistry()
    reg.register(_HighSkill(log=order))
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (True, False) for a in actions}, reg=reg
    )
    events = asyncio.run(_collect_events(loop.arun("做两件事")))
    cns = [e for e in events if e.kind == "confirmation_needed"]
    assert len(cns) == 1 and len(cns[0].actions) == 2
    results = [e for e in events if e.kind == "action_result"]
    assert len(results) == 2
    assert [r.result.data["did"] for r in results] == ["A", "B"]  # LLM 顺序
    assert order == ["h", "h"]


def test_arun_batch_confirm_rejected_skips_and_records_message(tmp_path):
    """批量批里拒了 a、批了 b → a 不执行、messages 记拒绝；b 照常执行。"""
    second = FakeProvider(text="done")
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="a", skill_id="h", params={"x": "A"}),
            ToolCall(id="b", skill_id="h", params={"x": "B"}),
        ]),
        second=second,
    )

    def confirmer(actions):
        # a 拒、b 批
        return {actions[0].id: (False, False), actions[1].id: (True, False)}

    loop = _build_batch_loop(tmp_path, provider, confirmer=confirmer)
    events = asyncio.run(_collect_events(loop.arun("做两件事")))
    results = [e for e in events if e.kind == "action_result"]
    assert len(results) == 1                       # 只 b 执行
    assert results[0].result.data == {"did": "B"}
    # 第二轮请求的消息里包含 a 的拒绝回执
    msgs = second.astream_calls[0]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "a" and "拒绝" in m["content"]
               for m in msgs)


def test_arun_batch_confirm_preserves_llm_order_with_dependency(tmp_path):
    """依赖链 a→b：a CONFIRM(L3)、b AUTO(L0) 用 a 的结果。
    攒批后 a 先执行出结果、b 再用——AUTO 不抢在 CONFIRM 前重排（破坏依赖）。spec §3.1。"""
    shared: dict[str, str] = {}

    class Store(Skill):
        id = "store"
        description = "写入共享态（高危）"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            shared["v"] = "from_a"
            return ActionResult(success=True, data={"v": "from_a"})

    class Read(Skill):
        id = "read"
        description = "读共享态（只读）"
        default_risk = RiskLevel.L0_READONLY

        def run(self, params, ctx):
            return ActionResult(success=True, data={"got": shared.get("v", "EMPTY")})

    reg = SkillRegistry()
    reg.register(Store())
    reg.register(Read())
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="a", skill_id="store", params={}),
            ToolCall(id="b", skill_id="read", params={}),
        ]),
        second=FakeProvider(text="done"),
    )
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (True, False) for a in actions}, reg=reg
    )
    events = asyncio.run(_collect_events(loop.arun("先存后读")))
    results = [e for e in events if e.kind == "action_result"]
    assert [r.action.skill_id for r in results] == ["store", "read"]  # LLM 顺序不乱
    assert results[1].result.data == {"got": "from_a"}                # b 拿到 a 的结果


def test_arun_batch_confirm_remember_adds_all_to_session_allowed(tmp_path):
    """批量勾「本会话不再询问」→ 多个 skill 都进 session_allowed（remember 批量生效）。"""
    class HSkill(Skill):
        id = "h"
        description = "高危"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={"did": True})

    reg = SkillRegistry()
    reg.register(HSkill())
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="a", skill_id="h", params={"x": "A"})]),
        second=FakeProvider(text="done"),
    )
    # 批量 confirmer：批准 + remember
    calls: list[int] = []

    async def confirmer(actions):
        calls.append(len(actions))
        return {a.id: (True, True) for a in actions}

    loop = _build_batch_loop(tmp_path, provider, confirmer=confirmer, reg=reg)
    asyncio.run(_collect_events(loop.arun("做危险的事")))
    assert "h" in loop.invoker.gate.session_allowed
    assert calls == [1]  # 攒批后一次批量调用


def test_arun_batch_confirm_two_skills_remember(tmp_path):
    """两个不同高危 skill 同轮 CONFIRM、全批+remember → 两个 skill 都进 session_allowed。"""
    class H1(Skill):
        id = "h1"
        description = "高危1"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={})

    class H2(Skill):
        id = "h2"
        description = "高危2"
        default_risk = RiskLevel.L3_HIGH

        def run(self, params, ctx):
            return ActionResult(success=True, data={})

    reg = SkillRegistry()
    reg.register(H1())
    reg.register(H2())
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[
            ToolCall(id="a", skill_id="h1", params={}),
            ToolCall(id="b", skill_id="h2", params={}),
        ]),
        second=FakeProvider(text="done"),
    )
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (True, True) for a in actions}, reg=reg
    )
    asyncio.run(_collect_events(loop.arun("做两件危险事")))
    assert {"h1", "h2"}.issubset(loop.invoker.gate.session_allowed)


def test_single_confirm_size_one_unchanged(tmp_path):
    """单 CONFIRM（batch size=1）行为不变：confirmation_needed 带单个 action、actions 长 1。"""
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="a", skill_id="h", params={"x": "A"})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (True, False) for a in actions}
    )
    events = list(loop.run("做一件事"))
    cns = [e for e in events if e.kind == "confirmation_needed"]
    assert len(cns) == 1
    assert cns[0].actions is not None and len(cns[0].actions) == 1
    assert cns[0].action is cns[0].actions[0]  # 旧字段兼容
    ar = next(e for e in events if e.kind == "action_result")
    assert ar.result.data == {"did": "A"}


def test_arun_single_confirm_rejected_still_emits_error(tmp_path):
    """单 CONFIRM 被拒（batch size=1）仍发 error、不执行——回归 test_loop_arun_async_confirmer_rejected 同语义。"""
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="a", skill_id="h", params={"x": "A"})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_batch_loop(
        tmp_path, provider, confirmer=lambda actions: {a.id: (False, False) for a in actions}
    )
    events = asyncio.run(_collect_events(loop.arun("做一件事")))
    kinds = [e.kind for e in events]
    assert "confirmation_needed" in kinds
    assert "error" in kinds
    assert not any(e.kind == "action_result" and e.result and e.result.data.get("did") for e in events)


# ---------- Phase 1 Task 1：表面提示下沉（presentation/attention/object/origin） ----------


class _SurfaceSkill(Skill):
    """带表面提示的 panel 技能：presentation/attention/object 随 ActionResult 透传。"""

    id = "surface_demo"
    description = "演示表面提示"

    def __init__(self, ref="notes:list", presentation=None, attention="suggest", object_=None):
        self._ref = ref
        self._presentation = presentation
        self._attention = attention
        self._object = object_

    def run(self, params, ctx):
        return ActionResult(
            success=True,
            data={"rows": [1]},
            panel=self._ref,
            presentation=self._presentation,
            attention=self._attention,
            object=self._object,
        )


def test_panel_event_carries_surface_hints(tmp_path, monkeypatch):
    """技能声明的 presentation/attention/object 必须透传进 panel 事件——
    宿主裁决需要这些信息，此前只能靠前端猜「是不是用户明确要的」。"""
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "notes:list", {"type": "list"})
    monkeypatch.delitem(plugins._PANEL_TITLES, "notes:list", raising=False)
    skill = _SurfaceSkill(
        presentation="inline",
        attention="quiet",
        object_={"type": "note", "id": "7", "title": "读书笔记"},
    )
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="surface_demo", params={})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_panel_loop(tmp_path, provider, skill)
    events = list(loop.run("go"))
    ev = next(e for e in events if e.kind == "panel")
    assert ev.payload["presentation"] == "inline"
    assert ev.payload["attention"] == "quiet"
    assert ev.payload["object"]["id"] == "7"
    ar = next(e for e in events if e.kind == "action_result")
    assert ev.payload["origin"] == ar.action.id  # 锚点 = 发起动作的 id


def test_panel_event_defaults_when_skill_silent(tmp_path, monkeypatch):
    """旧插件不声明 → presentation=None（宿主按老规则推断）、attention="suggest"。"""
    from yibao_brain import plugins

    monkeypatch.setitem(plugins._PANELS, "notes:list", {"type": "list"})
    monkeypatch.delitem(plugins._PANEL_TITLES, "notes:list", raising=False)
    skill = _SurfaceSkill(ref="notes:list")
    provider = _TwoStepProvider(
        first=FakeProvider(tool_calls=[ToolCall(id="t1", skill_id="surface_demo", params={})]),
        second=FakeProvider(text="done"),
    )
    loop = _build_panel_loop(tmp_path, provider, skill)
    events = list(loop.run("go"))
    ev = next(e for e in events if e.kind == "panel")
    assert ev.payload["presentation"] is None
    assert ev.payload["attention"] == "suggest"
    assert ev.payload["object"] is None
