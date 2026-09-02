"""Gate 持久化：每道 L3 审批（confirmation）落 Work Graph，决策可审计。

挂钩点：ToolInvoker 批量确认前记 pending、拿到 verdict 记 approved/denied；
抢占/取消与进程死亡留下的悬空 pending 标 expired（≠ 用户拒绝）。
"""
from __future__ import annotations

import asyncio

from yibao_brain.audit import AuditLog
from yibao_brain.invoker import ToolInvoker
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.llm import FakeProvider, ToolCall
from yibao_brain.loop import AgentLoop
from yibao_brain.memory import FakeMemory
from yibao_brain.safety import Gate, GatePolicy, RiskClassifier
from yibao_brain.tools import Tool, ToolRegistry
from yibao_brain.work_events import WorkGraphGateSink, WorkGraphInvocationSink
from yibao_brain.work_graph import WorkGraphStore


class _DangerTool(Tool):
    id = "demo.danger"
    description = "危险占位"
    default_risk = RiskLevel.L3_HIGH

    def run(self, params, ctx):
        return ActionResult(success=True, data={"did": True})


def _graph(tmp_path) -> WorkGraphStore:
    graph = WorkGraphStore(str(tmp_path / "work_graph.db"))
    graph.create_workspace("ws", "闸门项目", str(tmp_path / "ws"))
    return graph


def _invoker(tmp_path, graph, confirmer=None) -> ToolInvoker:
    registry = ToolRegistry()
    registry.register(_DangerTool(), plugin="demo")
    invoker = ToolInvoker(
        registry, RiskClassifier(), Gate(GatePolicy()), AuditLog(str(tmp_path / "audit.db")),
        confirmer=confirmer,
    )
    invoker.gate_sink = WorkGraphGateSink(graph, lambda cid: "ws" if cid else "")
    return invoker


# ---------- 存储层：状态机 ----------


def test_gate_pending_records_action_snapshot_and_run(tmp_path):
    graph = _graph(tmp_path)
    try:
        run_id = graph.workspace_view("ws")["workflow_run"]["id"]
        graph.record_gate_pending(
            "act_1", tool_id="demo.danger", params={"path": "/tmp/x"}, risk=3,
            conversation_id="s1", workspace_id="ws",
        )
        gate = graph.gate_view("act_1")
        assert gate["status"] == "pending"
        assert gate["action"] == {"tool_id": "demo.danger", "params": {"path": "/tmp/x"}}
        assert gate["risk"] == 3
        assert gate["conversation_id"] == "s1"
        assert gate["workflow_run_id"] == run_id
        assert gate["invocation_id"] == ""
        assert gate["decided_by"] == "" and gate["decided_at"] is None
        assert gate["created_at"] > 0
        # preview/diff 列已预留，当前审批尚无结构化 preview 产物。
        assert gate["preview_ref"] is None and gate["diff_ref"] is None

        # 同一 action 重复登记不覆盖第一道 pending（审计起点唯一）。
        graph.record_gate_pending(
            "act_1", tool_id="demo.danger", params={"path": "/tmp/y"}, risk=3,
            conversation_id="s1", workspace_id="ws",
        )
        assert graph.gate_view("act_1")["action"]["params"] == {"path": "/tmp/x"}
    finally:
        graph.close()


def test_gate_decision_approve_and_deny_are_terminal(tmp_path):
    graph = _graph(tmp_path)
    try:
        graph.record_gate_pending("a1", tool_id="t", params={}, risk=3, conversation_id="s")
        graph.record_gate_pending("a2", tool_id="t", params={}, risk=3, conversation_id="s")
        graph.record_gate_decision("a1", True)
        graph.record_gate_decision("a2", False)
        approved = graph.gate_view("a1")
        denied = graph.gate_view("a2")
        assert approved["status"] == "approved" and approved["decided_by"] == "user"
        assert approved["decided_at"] is not None
        assert denied["status"] == "denied"

        # 已决是终态：后到的相反决策不改写审计。
        graph.record_gate_decision("a1", False)
        assert graph.gate_view("a1")["status"] == "approved"
    finally:
        graph.close()


def test_expire_gates_marks_pending_and_ignores_late_decision(tmp_path):
    graph = _graph(tmp_path)
    try:
        graph.record_gate_pending("a1", tool_id="t", params={}, risk=3, conversation_id="s")
        graph.record_gate_pending("a2", tool_id="t", params={}, risk=3, conversation_id="s")
        assert graph.expire_gates(["a1"]) == 1
        assert graph.gate_view("a1")["status"] == "expired"
        assert graph.gate_view("a1")["decided_at"] is None  # 无人做过决策
        # expired 是终态：取消后迟到的 (False, False) verdict 不得把它写成 denied。
        graph.record_gate_decision("a1", False)
        assert graph.gate_view("a1")["status"] == "expired"
        # 已决的 Gate 不被 expire 误伤。
        graph.record_gate_decision("a2", True)
        assert graph.expire_gates(["a2"]) == 0
        assert graph.gate_view("a2")["status"] == "approved"
    finally:
        graph.close()


def test_dangling_pending_gate_expires_on_reopen(tmp_path):
    """进程死在确认等待中：重启后 pending 不得永远冒充待决。"""
    path = tmp_path / "work_graph.db"
    graph = WorkGraphStore(str(path))
    graph.create_workspace("ws", "闸门项目", str(tmp_path / "ws"))
    graph.record_gate_pending("a1", tool_id="t", params={}, risk=3, conversation_id="s")
    graph.close()

    reopened = WorkGraphStore(str(path))
    try:
        assert reopened.gate_view("a1")["status"] == "expired"
    finally:
        reopened.close()


def test_gate_links_invocation_after_approval(tmp_path):
    """按印放行进入执行后，审批记录经 action_id 挂上 Invocation。"""
    graph = _graph(tmp_path)
    try:
        graph.record_gate_pending("act_9", tool_id="demo.danger", params={}, risk=3, conversation_id="s")
        graph.record_gate_decision("act_9", True)
        invocation_id = graph.begin_invocation(
            action_id="act_9", workspace_id="ws", conversation_id="s",
            surface="home", tool_id="demo.danger", params={},
        )
        assert graph.gate_view("act_9")["invocation_id"] == invocation_id
    finally:
        graph.close()


def test_list_gates_filters(tmp_path):
    graph = _graph(tmp_path)
    try:
        graph.record_gate_pending("a1", tool_id="t", params={}, risk=3, conversation_id="s1")
        graph.record_gate_pending("a2", tool_id="t", params={}, risk=3, conversation_id="s2")
        graph.record_gate_decision("a1", True)
        assert [g["id"] for g in graph.list_gates(status="pending")] == ["a2"]
        assert [g["id"] for g in graph.list_gates(conversation_id="s1")] == ["a1"]
        run_id = graph.workspace_view("ws")["workflow_run"]["id"]
        assert {g["id"] for g in graph.list_gates()} == {"a1", "a2"}
        assert graph.list_gates(workflow_run_id="nonexistent") == []
        assert run_id  # 两个 Gate 都经 workspace_id 关联到了当前 run
        assert {g["id"] for g in graph.list_gates(workflow_run_id=run_id)} == set()  # 未传 workspace_id
    finally:
        graph.close()


# ---------- 审批链路集成：invoker / loop ----------


def test_invoker_batch_confirm_records_pending_then_denied(tmp_path):
    graph = _graph(tmp_path)
    # 显式拒绝（用户点了「拒绝」）：与 server batch_confirmer 的 verdict 形态一致。
    invoker = _invoker(tmp_path, graph, confirmer=lambda actions: {a.id: (False, False) for a in actions})
    try:
        action = invoker.propose(ToolCall(id="t1", tool_id="demo.danger", params={"path": "/tmp/x"}))
        verdicts = invoker.batch_confirm_sync([action], meta={"conversation_id": "s1"})
        assert verdicts[action.id] == (False, False)
        gate = graph.gate_view(action.id)
        assert gate["status"] == "denied"
        assert gate["action"]["params"] == {"path": "/tmp/x"}
        assert gate["conversation_id"] == "s1"
        assert gate["workflow_run_id"] == graph.workspace_view("ws")["workflow_run"]["id"]
        assert gate["risk"] == int(RiskLevel.L3_HIGH)
    finally:
        graph.close()


def test_invoker_batch_confirm_approve_then_execution_links_invocation(tmp_path):
    graph = _graph(tmp_path)
    invoker = _invoker(tmp_path, graph, confirmer=lambda actions: {a.id: (True, False) for a in actions})
    invoker.invocation_sink = WorkGraphInvocationSink(graph, lambda _cid: "ws")
    try:
        action = invoker.propose(ToolCall(id="t2", tool_id="demo.danger", params={}))
        verdicts = invoker.batch_confirm_sync([action], meta={"conversation_id": "s1"})
        assert verdicts[action.id] == (True, False)
        assert graph.gate_view(action.id)["status"] == "approved"

        result = invoker.execute(action, {}, {"conversation_id": "s1"})
        assert result.success
        gate = graph.gate_view(action.id)
        assert gate["invocation_id"].startswith("invocation_")
        assert graph.invocation_view(gate["invocation_id"])["status"] == "succeeded"
    finally:
        graph.close()


class _TwoStepProvider:
    """第一次返回 tool_call，之后给最终回复（对齐 test_loop 先例）。"""

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


def test_loop_confirmation_lands_gate_approved(tmp_path):
    """经 agent loop 的完整链路：confirmation_needed → 用户批准 → Gate(approved)。"""
    graph = _graph(tmp_path)
    registry = ToolRegistry()
    registry.register(_DangerTool(), plugin="demo")
    loop = AgentLoop(
        provider=_TwoStepProvider(
            FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="demo.danger", params={"x": 1})]),
            FakeProvider(text="done"),
        ),
        skills=registry,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(str(tmp_path / "audit.db")),
        confirmer=lambda actions: {a.id: (True, False) for a in actions},
    )
    loop.invoker.gate_sink = WorkGraphGateSink(graph, lambda cid: "ws" if cid else "")
    try:
        events = list(loop.run("做危险的事", conversation_id="s1"))
        confirmation = next(e for e in events if e.kind == "confirmation_needed")
        gate = graph.gate_view(confirmation.action.id)
        assert gate["status"] == "approved"
        assert gate["conversation_id"] == "s1"
        assert gate["action"]["tool_id"] == "demo.danger"
    finally:
        graph.close()


def test_loop_confirmation_rejected_lands_gate_denied(tmp_path):
    graph = _graph(tmp_path)
    registry = ToolRegistry()
    registry.register(_DangerTool(), plugin="demo")
    loop = AgentLoop(
        provider=_TwoStepProvider(
            FakeProvider(tool_calls=[ToolCall(id="t1", tool_id="demo.danger", params={})]),
            FakeProvider(text="done"),
        ),
        skills=registry,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy()),
        memory=FakeMemory(),
        log=AuditLog(str(tmp_path / "audit.db")),
        confirmer=lambda actions: {a.id: (False, False) for a in actions},
    )
    loop.invoker.gate_sink = WorkGraphGateSink(graph, lambda cid: "ws" if cid else "")
    try:
        events = list(loop.run("做危险的事", conversation_id="s1"))
        confirmation = next(e for e in events if e.kind == "confirmation_needed")
        assert graph.gate_view(confirmation.action.id)["status"] == "denied"
    finally:
        graph.close()


def test_gate_sink_failure_does_not_break_confirmation(tmp_path):
    """持久化失败只记 stderr，审批主链路照常（与 invocation sink 同纪律）。"""
    graph = _graph(tmp_path)
    graph.close()  # 关库让 Gate 写入必然失败
    invoker = _invoker(tmp_path, graph, confirmer=lambda actions: {a.id: (True, False) for a in actions})
    action = invoker.propose(ToolCall(id="t3", tool_id="demo.danger", params={}))
    verdicts = invoker.batch_confirm_sync([action], meta={"conversation_id": "s1"})
    assert verdicts[action.id] == (True, False)


def test_async_batch_confirm_records_gates(tmp_path):
    """异步 confirmer（server 真实形态）同样落 Gate。"""
    graph = _graph(tmp_path)

    async def confirmer(actions):
        return {a.id: (True, False) for a in actions}

    invoker = _invoker(tmp_path, graph, confirmer=confirmer)
    try:
        action = invoker.propose(ToolCall(id="t4", tool_id="demo.danger", params={}))
        verdicts = asyncio.run(invoker.batch_confirm([action], meta={"conversation_id": "s9"}))
        assert verdicts[action.id] == (True, False)
        gate = graph.gate_view(action.id)
        assert gate["status"] == "approved" and gate["conversation_id"] == "s9"
    finally:
        graph.close()
