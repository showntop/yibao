"""面板动作直调（从 server.py 拆出）：panel_action 白名单方法的 propose → decide → 确认/执行 → 审计链路。

纪律：本模块的函数从 server.py 原样搬来，不改逻辑；改行为请另开 commit。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .ipc import Action, Event, RiskLevel
from .llm import ToolCall
from .loop import AgentLoop, _offload
from .plugins import get_api, panel_payload
from .safety import Decision

if TYPE_CHECKING:
    from .server import WriteMsg


class _KeepMissing(dict):
    """format_map 缺键时保留 {key} 原样（intent 渲染不炸）。"""

    def __missing__(self, key):
        return "{" + key + "}"


def _render_intent(api, params: dict) -> str:
    """intent 模板用 params 渲染（{key} 占位）；无 intent 用「调用 <handler>」。"""
    template = api.intent or f"调用 {api.handler}"
    return template.format_map(_KeepMissing(params))


async def _emit_refresh_panel(agent: AgentLoop, emit, refresh_tool: str) -> None:
    """直调成功后的声明式刷新：执行查询 tool（应为本插件 L0 只读），把它的 panel 事件推给壳。

    刷新 tool 若意外需要确认/被拒，静默跳过（不弹确认——刷新不该打断用户）。
    """
    action = agent.invoker.propose(ToolCall(id=f"pa_refresh_{id(emit)}", tool_id=refresh_tool, params={}))
    if agent.invoker.decide(action) != Decision.AUTO:
        return
    result = await _offload(agent.invoker.execute, action, {})
    payload = panel_payload(result)
    if payload is not None:
        emit(Event(kind="panel", payload=payload))


async def handle_panel_action(msg: dict, agent: AgentLoop, write_msg: WriteMsg, *, run_text) -> None:
    """处理壳侧 panel_action（v2 §7）：api.toml 白名单内的面板方法。

    direct=true：invoker 直调（propose → api.risk 只许收紧 → decide → 确认/执行 → 审计）；
    direct=false：intent 渲染后交给 run_text（与 type="run" 同路径的 agent 流程）。
    """
    surface = str(msg.get("surface") or "pet")  # 会话分流：事件随发起场景标记，壳侧各窗按 surface 过滤
    conversation_id = str(msg.get("conversation_id") or "")
    invoke_meta = {"conversation_id": conversation_id, "surface": surface}

    def emit(event: Event) -> None:
        write_msg({"type": "event", "surface": surface, "event": event.model_dump(mode="json")})

    rid = msg.get("id")
    method = ""
    tag = Action(id=f"pa_{rid}", tool_id="?")  # 错误事件归属标签：壳侧桥按 pa_<rid> 认领，不误杀其他调用
    try:
        method = str(msg.get("method", ""))
        tag = Action(id=f"pa_{rid}", tool_id=method or "?")
        params = msg.get("params") or {}
        api = get_api(method)
        if api is None:  # 白名单外：拒绝执行
            emit(Event(kind="error", text=f"面板方法未在白名单：{method}", action=tag))
            write_msg({"type": "run_done", "id": rid})
            return
        if not api.direct:
            await run_text(_render_intent(api, params), rid)
            return

        action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", tool_id=api.handler, params=params))
        action.id = f"pa_{rid}"  # propose 会重新发 id；壳侧桥靠 pa_<rid> 关联回包/确认/错误，必须保留
        if api.risk is not None:
            action.risk = max(action.risk, api.risk)  # api.toml 只许收紧，不许放宽
        decision = agent.invoker.decide(action)
        if decision == Decision.DENY:
            emit(Event(kind="error", text=f"策略禁止执行 {api.handler}（风险过高）", action=action))
            write_msg({"type": "run_done", "id": rid})
            return
        if decision == Decision.CONFIRM:
            emit(Event(kind="confirmation_needed", action=action, confirmation_id=action.id))
            # 面板直达走批量 confirmer（batch size=1）：等壳 confirm_batch 回执。
            # remember 写入复用 invoker.apply_verdict（F4：消除 loop 之外的第 3 处重复）。
            verdicts = await agent.invoker.batch_confirm([action])
            approved, remember = verdicts.get(action.id, (False, False))
            agent.invoker.apply_verdict(action, approved, remember)
            if not approved:
                emit(Event(kind="error", text=f"用户拒绝执行 {api.handler}", action=action))
                write_msg({"type": "run_done", "id": rid})
                return
        result = await _offload(agent.invoker.execute, action, params, invoke_meta)  # 与 arun 一致挪线程池
        emit(Event(kind="action_result", action=action, result=result))
        if result.success and api.refresh is not None:
            # 声明式刷新：删除类操作后跟一次查询，面板拿新数据而不是操作回执
            await _emit_refresh_panel(agent, emit, api.refresh)
        else:
            if result.success and api.panel is not None:
                result.panel = api.panel  # method 声明的面板优先于 tool 自带引用（如 webview 编辑器）
            if not api.quiet:  # quiet：不弹面板（唤起条存素材等静默直调）
                payload = panel_payload(result)
                if payload is not None:
                    emit(Event(kind="panel", payload=payload))
        write_msg({"type": "run_done", "id": rid})
    except Exception as e:  # 兜底：任何意外都要给壳一个交代，别让面板卡死
        emit(Event(kind="error", text=f"面板操作失败：{e}", action=tag))
        write_msg({"type": "run_done", "id": rid})


async def _readonly_no_run(text: str, rid) -> None:
    """L0 只读直调永远不会走 agent 路径（direct=true 才并发）；防御性兜底。"""
    raise RuntimeError("只读直调不应进入 agent 路径")


def _is_readonly_direct(msg: dict, agent: AgentLoop) -> bool:
    """L0 只读直调（get/list/article_read 等纯查询）→ 不占槽位、不抢占。

    面板/编辑器的数据加载与在跑的对话是并行关系：互相抢占会让 read_article 顶掉
    写稿 run（回复截断），也让 run 期间的面板加载被排队/取消（「编辑器没反应」）。
    db 层单连接+锁，并发读安全。
    """
    api = get_api(str(msg.get("method", "")))
    if api is None or not api.direct:
        return False
    action = agent.invoker.propose(
        ToolCall(id=f"pa_{msg.get('id')}", tool_id=api.handler, params=msg.get("params") or {})
    )
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    return action.risk <= RiskLevel.L0_READONLY
