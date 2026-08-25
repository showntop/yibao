"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done、feed（主屏动态+统计）。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission、panel_context、feed。
"""
from __future__ import annotations

from .log import log
import asyncio
import contextvars
import functools
import json
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable

from . import permissions
from .audit import AuditLog
from .background import (
    _DOCK_MAX,
    _consume_invoke_context,
    _describe_image_attachments,
    _describe_screen,
    _dispatch_reminder,
    _distiller_loop,
    _dock_list,
    _gate_proactive_event,
    _plugin_summaries_list,
    _proactive_level,
    _recap_decide,
    _recover_background_jobs,
    _perception_cleanup_loop,
    _reminder_loop,
    _watch_tick,
)
from .config import a11y_enabled, computer_use_enabled, computer_use_max_steps, history_path, http_port, llm_api_key, load_settings, perception_db_path, save_settings, screenshot_dir, stt_model_dir, tts_voice, vad_max_seconds, vad_min_silence, vad_model_path, vision_api_key, voice_enabled
from .feed import FeedStore
from .distiller import Distiller, DistillerStore
from .jobstore import JobsStore
from .history import ConversationHistory
from .http_api import EventTap
from .ipc import Event, RiskLevel
from .llm import FakeProvider, OpenAICompatProvider
from .loop import AgentLoop, _offload
from .memory import FakeMemory, LazyMem0Memory
from .proactive import ProactiveDispatcher
from .plugins import LlmChat, get_plugin_summaries
from .runtime import RuntimeCtx
from .runtime import helpers
from .runtime.mobile import MobileDomain
from .runtime.runs import RunsDomain
from .runtime.voice import VoiceDomain
from .safety import Gate, GatePolicy, RiskClassifier
from .tools import EchoTool, ToolRegistry
from .tools.composite import register_composite_tools
from .tools.perception import ComputerUseTool, register_core_tools
from .watch import WatchCtx
from .watch_service import WatchService

# re-export：已拆到专模块，保留 yibao_brain.server.<name> 引用路径（tests/下游 import 不变）
from .approvals import _coding_perm_registry, _fulfill_coding_perm
from .bridge import (
    _bridge_save,
    _conversations_payload,
    _ensure_http_token,
    _history_payload,
    _lan_ip,
    _pick_en_ip,
    _reminders_cancel_payload,
    _reminders_list_payload,
    _start_http_api,
)
from .panel import _is_readonly_direct, _readonly_no_run, _render_intent, handle_panel_action

# 面板焦点（v2 §5）：壳侧 panel_context 消息维护，run 时注入 LLM 上下文（「这个/它」有解）
_FOCUS: dict = {"value": None}

# 被抢占任务的收尾宽限（秒）：超时强制取消，防 hung 任务把槽位卡死（「点了没反应」的根）。
# 消费方在 runtime/runs.py（chain_start），serve_async 构造 RunsDomain 时传入本值——
# 测试 monkeypatch yibao_brain.server._PREEMPT_GRACE_S 的路径原样生效。
_PREEMPT_GRACE_S = 8.0

# stdio 协议 + 语音退出语 + run_done 载荷已拆到 transport.py（re-export 保持 server.<name> 引用路径）
from .transport import (  # noqa: E402
    ReadMsg,
    WriteMsg,
    _VOICE_SESSION_BYE,
    _VOICE_SESSION_HINT,
    _VOICE_SESSION_MAX_EMPTY,
    _run_done_msg,
    is_exit_phrase as _is_exit_phrase,
    line_reader,
    line_writer,
)

# 看门狗心跳：pong 改由读线程直接应答（见 serve_async._reader），不经事件循环——
# 循环被长任务占住时照样 pong（忙 ≠ 死，历史误杀的根）；
# 但循环 _TICK_FRESH_S 秒没调度（真卡死）→ 扣住 pong，让看门狗杀掉重启。
_LOOP_TICK = {"t": 0.0}
_TICK_FRESH_S = 12.0

snip_ctx: dict = {"b64": None, "ts": 0.0}  # 截图即问：区域截图 b64 暂存（可多次提问，过期丢弃）
SNIP_TTL_S = 300.0


def _peek_snip(stash: dict) -> str | None:
    """暂存区域截图 b64：新鲜→返回（不清空，同一截图可追问）；过期→丢弃返 None。"""
    b64 = stash.get("b64")
    if b64 is None:
        return None
    if time.time() - float(stash.get("ts") or 0.0) >= SNIP_TTL_S:
        stash["b64"] = None
        return None
    return b64


def _permissions_status() -> dict:
    """检测辅助功能/屏幕录制权限；检测本身失败时乐观返回 True（不出误报 banner）。"""
    try:
        return {
            "ax": permissions.check_ax(),
            "screen": permissions.check_screen(),
            "input": permissions.check_input(),
        }
    except Exception:
        return {"ax": True, "screen": True, "input": False}


def build_loop(
    read_msg: ReadMsg,
    use_real: bool,
    db_path: str,
    provider=None,
    skills_factory=None,
    confirmer=None,
    history_file: str | None = None,
    emit_event=None,
    feed=None,
) -> AgentLoop:
    real_a11y = use_real and a11y_enabled() and sys.platform == "darwin"
    reg = skills_factory() if skills_factory else ToolRegistry()
    if not skills_factory:
        reg.register(EchoTool())
        if real_a11y:
            cu_client = None
            describe = None
            if vision_api_key() and computer_use_enabled():
                try:
                    from .llm import ComputerUseClient, describe_screen

                    cu_client = ComputerUseClient()

                    def describe(path, _c=cu_client):
                        """截屏文件 → b64 → 可见窗口枚举；任何失败返 None。"""
                        try:
                            import base64

                            with open(path, "rb") as f:
                                b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()
                            return describe_screen(_c, b64)
                        except Exception:
                            return None

                except Exception as e:
                    log(f"computer-use 兜底未启用：{e}")
                    cu_client = None
            register_core_tools(reg, describe=describe)
            register_composite_tools(reg)
            if cu_client is not None:
                try:
                    reg.register(ComputerUseTool(cu_client, max_steps=computer_use_max_steps()))
                except Exception as e:
                    log(f"computer-use 技能注册失败：{e}")

    if provider is not None:
        prov = provider
    else:
        prov = OpenAICompatProvider() if (use_real and llm_api_key()) else FakeProvider(text="(未配置 LLM key，使用 fake 回复)")

    try:
        # 懒加载：构造秒回（不 import torch/mem0），真实 mem0 后台线程就绪后接入
        memory = LazyMem0Memory() if use_real else FakeMemory()
    except Exception:
        memory = FakeMemory()

    host = None
    if real_a11y:
        try:
            from .mac.host_mac import MacHost

            host = MacHost(screenshot_dir=screenshot_dir())
        except Exception as e:  # pyobjc 未装 / 非 mac → 回退无基座（技能会优雅报错）
            log(f"MacHost 不可用，回退无基座：{e}")

    active_plugins: set | None = None  # None=全量暴露（测试/兼容）；集合=路由式暴露
    reminder_store = None
    if use_real and not skills_factory:
        # 底座提醒存储先建：提醒管理插件（reminders capability）与底座技能共享同一实例
        from .reminders import ReminderStore, make_skills

        reminder_store = ReminderStore(os.path.join(os.path.dirname(db_path), "reminders.json"))
        _load_plugins_safe(reg, memory, prov, host, reminders=reminder_store, emit_event=emit_event)
        # 能力热重载（P1 reload 地基）：增量扫描 plugins/，新插件免重启生效
        from pathlib import Path

        from .plugins import HttpClient, LlmChat as _PlugLlm, get_plugin_summaries, load_plugins
        from .tools import CapabilityRefreshTool, UsePluginTool

        _plugins_dir = os.environ.get("YIBAO_PLUGINS_DIR") or str(
            Path(__file__).resolve().parents[3] / "plugins"
        )

        def _reload_plugins(existing=None):
            return load_plugins(
                _plugins_dir, reg,
                memory=memory, http=HttpClient(), llm=_PlugLlm(prov),
                host_available=host is not None, reminders=reminder_store,
                emit_event=emit_event, existing=existing,
            )

        reg.register(CapabilityRefreshTool(reg, _reload_plugins))
        # 能力台账（P3 管理面地基）：tool_list 只读列出全部 Tool + 来源形态/风险/特权
        from .tools import ToolListTool as _ToolListTool

        # 路由式暴露（§12-2）：插件 tool 默认隐藏，use_plugin 按需展开；
        # active 集合与 AgentLoop 共享（技能执行即改，下一步 LLM 调用即见新工具）
        # （get_plugin_summaries/load_plugins 已在上方 import）

        active_plugins = set()
        reg.register(UsePluginTool(reg, active_plugins, get_plugin_summaries()))
        # 技能展开（use_skill 与 use_plugin 对称）：说明书进主上下文，agent 循环执行
        from .tools.skills_index import refresh_index
        from .tools import UseSkillTool

        refresh_index()
        reg.register(UseSkillTool())
        reg.register(_ToolListTool(reg, active_plugins, get_plugin_summaries()))
        # 台账面板直调（P3 管理面板数据源）：L0 只读、不占槽位不抢占
        from .plugins import ApiMethod as _ApiMethod
        from .plugins import _API as _PLUGIN_API

        _PLUGIN_API["tool_ledger"] = _ApiMethod(
            name="tool_ledger", handler="tool_list", direct=True,
            intent=None, risk=RiskLevel.L0_READONLY, plugin_id="core",
        )
        # MCP 适配器（P2）：运行时挂载 MCP server，工具经 use_mcp 展开（默认隐藏）
        from .config import data_dir as _data_dir
        from .tools.mcp import (
            McpAddTool, McpConnectTool, McpDisconnectTool, McpListTool,
            McpManager, UseMcpTool,
        )

        mcp_manager = McpManager(os.path.join(_data_dir(), "mcp_servers.json"), reg, active_plugins)
        reg.register(UseMcpTool(mcp_manager, active_plugins))
        reg.register(McpListTool(mcp_manager))
        reg.register(McpConnectTool(mcp_manager))
        reg.register(McpDisconnectTool(mcp_manager))
        reg.register(McpAddTool(mcp_manager))
        # 台账管理（P3 操作闭环 + B/E 收尾）：SourceStore 持久化 + SourceManager 路由
        # 注意：必须放在 MCP 块之后（依赖 _data_dir 与 mcp_manager）
        from .tools.ledger import (
            ToolDisableTool as _ToolDisableTool,
            ToolEnableTool as _ToolEnableTool,
            ToolStatusTool as _ToolStatusTool,
            ToolUninstallTool as _ToolUninstallTool,
            ToolUpdateTool as _ToolUpdateTool,
        )
        from .tools.management import PluginManager as _PluginManager
        from .tools.management import SkillManager as _SkillManager
        from .tools.management import SourceRecord as _SourceRecord
        from .tools.management import SourceStore as _SourceStore

        _source_store = _SourceStore(os.path.join(_data_dir(), "sources.json"))
        _plugin_manager = _PluginManager(reg, _plugins_dir, loader=_reload_plugins)
        _skill_manager = _SkillManager()
        _managers = {"plugin": _plugin_manager, "skill": _skill_manager, "mcp": mcp_manager}
        # 启动对账（spec §对象模型）：discover → 存储 status 合并（disabled 保留）→ 恢复禁用集 + 落盘
        _discovered: dict[str, _SourceRecord] = {}
        for _m in (_plugin_manager, _skill_manager, mcp_manager):
            for _rec in _m.discover():
                _discovered[_rec.id] = _rec
        _discovered = _source_store.merge_status(_discovered)
        for _rid, _rec in _discovered.items():
            if _rec.status == "disabled":
                reg.disable_source(_rec.id)
        _source_store.save(_discovered)

        reg.register(_ToolDisableTool(reg, active_plugins, _managers, _source_store))
        reg.register(_ToolEnableTool(reg, active_plugins, _managers, _source_store))
        reg.register(_ToolStatusTool(reg, active_plugins, _managers, _source_store))
        reg.register(_ToolUpdateTool(reg, active_plugins, _managers, _source_store))
        reg.register(_ToolUninstallTool(reg, active_plugins, _managers, _source_store))
        for _method, _risk in (("tool_disable", RiskLevel.L2_MEDIUM),
                               ("tool_enable", RiskLevel.L2_MEDIUM),
                               ("tool_status", RiskLevel.L0_READONLY),
                               ("tool_update", RiskLevel.L3_HIGH),
                               ("tool_uninstall", RiskLevel.L3_HIGH)):
            _PLUGIN_API[_method] = _ApiMethod(
                name=_method, handler=_method, direct=True,
                intent=None, risk=_risk, plugin_id="core",
            )
        for sk in make_skills(reminder_store):
            reg.register(sk)
        # gen 面板（LLM 生成 webview）：启动恢复已生成面板 + 注册 panel_gen/open/list/delete
        from . import genpanel

        genpanel.load_saved_panels()
        for sk in genpanel.make_skills(LlmChat(prov)):
            reg.register(sk)

    def default_confirmer(actions) -> dict:
        # 由 serve 在 confirmation_needed 事件之后触发；阻塞读壳的回答。
        # 批量接口（Task 1）：对每个 action 读一条 confirm 消息，回 {id: (approved, remember)}。
        out: dict[str, tuple[bool, bool]] = {}
        for action in actions:
            ans = read_msg() or {}
            out[action.id] = (bool(ans.get("approved", False)), bool(ans.get("remember", False)))
        return out

    # 会话历史：仅真实模式默认落盘（fake/测试模式不污染本地文件）
    hist = history_file or (history_path() if use_real else None)

    agent = AgentLoop(
        provider=prov,
        skills=reg,
        classifier=RiskClassifier(),
        gate=Gate(GatePolicy(auto_below_or_equal=RiskLevel.L1_LOW)),  # L2+ 走确认
        memory=memory,
        log=AuditLog(db_path),
        confirmer=confirmer or default_confirmer,
        host=host,
        history=ConversationHistory(hist) if hist else None,
        focus_provider=lambda: _FOCUS["value"],
        active_plugins=active_plugins,
        feed=feed,
    )
    if use_real and not skills_factory:
        agent.reminder_store = reminder_store  # serve 的调度循环经它触发提醒
    return agent


def _load_plugins_safe(reg, memory, prov, host, reminders=None, emit_event=None) -> None:
    """加载 <repo>/plugins 下的插件（env YIBAO_PLUGINS_DIR 可覆盖）。

    只在 use_real 且无自定义 skills_factory 时调用（测试不碰真实文件系统）；
    整个加载过程再兜一层 try：插件系统任何问题都不许拖垮底座启动。
    """
    try:
        from pathlib import Path

        from .plugins import HttpClient, LlmChat, load_plugins

        # sidecar/src/yibao_brain/server.py → 上四级即仓库根
        default_dir = Path(__file__).resolve().parents[3] / "plugins"
        plugins_dir = os.environ.get("YIBAO_PLUGINS_DIR") or str(default_dir)
        results = load_plugins(
            plugins_dir, reg,
            memory=memory, http=HttpClient(), llm=LlmChat(prov),
            host_available=host is not None, reminders=reminders,
            emit_event=emit_event,
        )
        for pid, status in results.items():
            log(f"插件 {pid}: {status}")
    except Exception as e:
        log(f"插件加载失败（已跳过）：{e}")


def _run_and_emit(loop: AgentLoop, text: str, write_msg: WriteMsg, rid, voice=None) -> None:
    for event in loop.run(text):
        write_msg({"type": "event", "event": event.model_dump(mode="json")})
        if voice is not None and event.kind == "final_reply" and event.text:
            write_msg({"type": "event", "event": {"kind": "speaking"}})
            try:
                voice.speak(event.text)
            except Exception as e:
                write_msg({"type": "event", "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
    write_msg({"type": "run_done", "id": rid})


def serve(loop: AgentLoop, read_msg: ReadMsg, write_msg: WriteMsg, voice=None) -> None:
    while True:
        req = read_msg()
        if req is None:
            return
        rtype = req.get("type")
        if rtype == "run":
            _run_and_emit(loop, req.get("text", ""), write_msg, req.get("id"), voice)
        elif rtype == "voice_start" and voice is not None:
            write_msg({"type": "event", "event": {"kind": "listening"}})
            try:
                text = voice.listen()
            except Exception as e:
                write_msg({"type": "event", "event": {"kind": "error", "text": f"语音识别失败：{e}"}})
                write_msg({"type": "run_done", "id": req.get("id")})
                continue
            write_msg({"type": "event", "event": {"kind": "listening_done", "text": text}})
            if text:
                _run_and_emit(loop, text, write_msg, req.get("id"), voice)
            else:
                write_msg({"type": "run_done", "id": req.get("id")})


async def serve_async(
    read_msg: ReadMsg,
    write_msg: WriteMsg,
    *,
    use_real: bool = False,
    db_path: str = "audit.db",
    voice=None,
    provider=None,
    vision_client=None,  # 测试注入：生产 None 时按 YIBAO_VISION_* 配置建 ComputerUseClient
    skills_factory=None,
    perception_store=None,
    perception_sensors=None,
    invoke_context_text: str | None = None,  # 测试注入：生产 None 时 invoke_context 走真实截图+vision
    http_enabled: bool | None = None,  # 浏览器扩展桥：None → 取 use_real（测试默认关，生产默认开）
) -> None:
    """异步控制平面：stdin 读线程 → asyncio.Queue → 分发；支持 interrupt 打断。

    与同步 serve 的关键差异：读消息在独立线程，故生成/TTS 进行中仍能收到 interrupt，
    cancel_event 一键"三连取消"（停 TTS + 终止 LLM 生成 + 清 TTS 队列）。
    槽位 per-会话（run_slots）：同会话新 run 抢占并打断未完成的旧 run；跨会话真并行。
    """
    ai_loop = asyncio.get_running_loop()
    tap = EventTap(write_msg)  # 事件分接头：stdio 照发 + SSE 广播（Task 9 起替换 write_msg）
    write_msg = tap  # 重绑：本函数内所有闭包（_stream_agent/dispatcher/reader 等）经分接头
    # ——stdio 输出字节不变（tap 透传），event/run_done 额外复制进 SSE 环形缓冲
    # 共享状态容器（R-13 第二步）：runtime/* 各域函数经 ctx 访问；各对象在装配段
    # 逐个注入。write_msg 注入重绑后的 tap（分接头）——别传原始 write_msg。
    ctx = RuntimeCtx()
    ctx.write_msg = write_msg
    ctx.ai_loop = ai_loop
    ctx.voice = voice
    _LOOP_TICK["t"] = time.monotonic()

    async def _tick() -> None:
        """主循环存活刻度：只要循环还能调度就每秒前进；读线程据此判断忙/死。"""
        while True:
            _LOOP_TICK["t"] = time.monotonic()
            await asyncio.sleep(1)

    tick_task = asyncio.ensure_future(_tick())
    queue: asyncio.Queue = asyncio.Queue()
    # 确认多槽（spec §3.2）：按 confirmation_id 索引的 future + 早到答案缓存。
    # 早到：confirm_batch 可能先于 batch_confirmer 注册 future 到达（读线程瞬时投递
    # run+confirm，主循环先处理 confirm），直接丢会死锁——存 early_answers 待取。
    # 多槽不假设并发数：多 run 真并行后（并发对话 spec）这层不用改，确认等待响应的是
    # 本 run 槽位的 cancel（见 batch_confirmer 内 _run_ctx 读取）。
    pending_confirms: dict[str, asyncio.Future] = {}
    early_answers: dict[str, tuple[bool, bool]] = {}
    confirm_meta: dict[str, dict] = {}  # cid -> {tool_id, summary, risk, created_at, conversation_id, surface}：手机 /v1/state 待批列表
    _confirm_done: deque[str] = deque(maxlen=100)  # 已处理确认（防手机重复点击 404）
    # run_slots：per-会话槽位表（并发对话 spec §A）。键 = conversation_id（空串归 default
    # 槽，兼容无会话 id 的遗留调用）。每槽 {task, cancel, preempt_gen, surface, running_surface}，
    # 字段语义同旧全局 run_state：
    # - preempt_gen：抢占代数。同槽新请求到来即 +1；排队中的任务启动时发现自己落后 →
    #   一启动即置 cancel（快速跳过），保证「同会话只有最新请求真正执行」。
    # - surface：该槽最近一次受理请求的窗口（pet=主窗 / 面板 id，dispatch 受理即写入）；
    #   running_surface：实际在跑的（排队结束才写），手机 interrupt 按它判域。
    # - 同会话（含同会话跨 surface）新请求 → 抢占；跨会话 → 各自槽位真并行，
    #   不再排队、不再发「另一个窗口还在说」notice；仅 default 槽保留旧的跨 surface 排队。
    run_slots: dict[str, dict] = {}

    class _SlotsIdleView:
        """proactive/提醒的 idle 判定适配（spec §F）：done() = 所有槽均空闲。

        包成 task 形状（done()），proactive.py/background.py 的 run_state["task"] 读取不变。"""

        @staticmethod
        def done() -> bool:
            return all(s["task"] is None or s["task"].done() for s in run_slots.values())

    # 传给 ProactiveDispatcher 与 _reminder_loop 的「全局空闲」视图：任一槽在跑即非 idle
    slots_idle_state: dict = {"task": _SlotsIdleView()}

    # 当前 run 的归属上下文（batch_confirmer 读本槽 cancel、confirm_meta 记会话归属）：
    # 槽位任务启动时 set，经 await 链一路传进 arun → invoker → confirmer，不串槽。
    _run_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("yibao_run_ctx", default=None)

    # runs 调度域（R-13 第二步拆分序 4）：_slot/_preempt_* /_schedule_run/_chain_start
    # 已迁 runtime/runs.py。_run_ctx 传原 ContextVar 对象（任务级隔离，不收进 ctx 属性）；
    # 各函数绑回局部名——_h_run/_h_panel_action/_h_interrupt 的原引用零改动，
    # mobile 域经 ctx.schedule_run/ctx.preempt_current 拿到的是同一批方法。
    ctx.run_slots = run_slots
    runs = RunsDomain(ctx, _run_ctx, preempt_grace_s=_PREEMPT_GRACE_S)
    _slot = runs.slot
    _preempt_current = runs.preempt_current
    _preempt_if_same_surface = runs.preempt_if_same_surface
    _schedule_run = runs.schedule_run
    _chain_start = runs.chain_start
    ctx.schedule_run = _schedule_run
    ctx.preempt_current = _preempt_current

    # voice 域（R-13 第二步拆分序 3）：tts_lock/_tts_chunks/_pump_tts/_drive_voice_start
    # 已迁 runtime/voice.py。tts_lock/_pump_tts 绑回局部名——run 流（_stream_agent，
    # 暂留 serve_async）与 _h_run 的原引用零改动。
    voice_domain = VoiceDomain(ctx)
    tts_lock = voice_domain.tts_lock
    _pump_tts = voice_domain.pump_tts

    # 并发的 L0 只读面板调用（不占槽位）：跟踪起来，stdin 关闭时一起收尾
    readonly_tasks: set[asyncio.Task] = set()

    # 会话内记住的「免确认」技能集合：用户勾选「本会话不再询问」并批准后记入；
    # 只活在内存，大脑重启即失效（C-4：会话级，不落盘）。
    # 安全语义（并发对话 spec §B）：remember 保持进程级共享——它是对「动作」的信任，
    # 不是对会话的信任；会话 A 记住的「不再询问」对会话 B 同样生效。
    remembered_confirm: set[str] = set()

    # 用户设置是运行期共享状态，主动分发器、感知与 watch service 都读同一字典。
    settings = load_settings()

    async def batch_confirmer(actions) -> dict[str, tuple[bool, bool]]:
        """批量确认（Task 3 多槽）：list[Action] -> {action.id: (approved, remember)}。

        每个 confirmation_id 独立 future，互不阻挡；早到答案（confirm_batch 先于
        batch_confirmer 注册 future 到达）存 early_answers，取时命中。等待期间响应
        cancel（抢占/打断）：否则新请求 join 一个永不结束的确认 → 派发循环卡死、
        ping 不应答、看门狗误杀（2026-07-19 复现确认）。

        remember 的 session_allowed 写入由 loop / handle_panel_action 拿到 verdict 后
        统一调 invoker.apply_verdict 处理（不在 confirmer 内做）。

        注意：remembered_confirm 短路已移除——gate.session_allowed 命中后 decide() 直接
        AUTO，根本进不到 CONFIRM；此处见到的 action 必然需要真确认。
        """
        out: dict[str, tuple[bool, bool]] = {}
        for action in actions:
            cid = action.id
            tool_id = getattr(action, "tool_id", "?")
            # 早到的 confirm_batch 直接兑现（future 还没建）
            if cid in early_answers:
                out[cid] = early_answers.pop(cid)
                continue
            fut = pending_confirms.setdefault(cid, ai_loop.create_future())
            _ctx = _run_ctx.get() or {}
            confirm_meta[cid] = {
                "tool_id": tool_id,
                # 可读摘要：params 非空 dict → k=v 逗号形式（手机审批页直接展示）；
                # 空 dict/非 dict → 回落 tool_id。截 120 字防长参数刷屏。
                "summary": (", ".join(f"{k}={v}" for k, v in (getattr(action, "params", None) or {}).items())[:120]
                            if isinstance(getattr(action, "params", None), dict) and action.params else tool_id),
                "risk": int(getattr(getattr(action, "risk", None), "value", getattr(action, "risk", 0)) or 0),
                "created_at": int(time.time()),
                # 会话归属（并发对话 spec §B）：/v1/state pending 与壳端确认卡按它过滤展示
                "conversation_id": _ctx.get("conversation_id") or "",
                "surface": _ctx.get("surface"),
            }
            # 确认等待必须响应本槽的抢占/打断：否则新请求 join 一个永不结束的确认 →
            # 派发循环卡死、ping 不应答、看门狗误杀（2026-07-19 复现确认）。
            # 绑本 run 自己槽位的 cancel（_run_ctx），不绑别槽——A 会话打断不得误取消
            # B 会话的确认等待（并发对话 spec §B）。
            cancel = _ctx.get("cancel")
            cancel_wait = ai_loop.create_task(cancel.wait()) if cancel is not None else None
            log(f"等待用户确认：{tool_id}")
            try:
                waiters: set = {fut}
                if cancel_wait is not None:
                    waiters.add(cancel_wait)
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                if fut in done:
                    approved, remember = fut.result()
                    log(f"确认结果：{'允许' if approved else '拒绝'}（{tool_id}）")
                    out[cid] = (bool(approved), bool(remember))
                else:
                    log(f"确认被抢占取消：{tool_id}")
                    out[cid] = (False, False)
            finally:
                if cancel_wait is not None:
                    cancel_wait.cancel()
                if pending_confirms.get(cid) is fut:
                    del pending_confirms[cid]
                confirm_meta.pop(cid, None)
        return out

    # 主屏 Feed 存储（OS 感 §4.2）：任务播报/提醒触发在此落库，主屏查询时一次拿回。
    # 与审计库同目录；FeedStore 写失败只 print，不拖垮主链路。
    feed = FeedStore(os.path.join(os.path.dirname(db_path), "feed.db"))
    proactive_dispatcher = ProactiveDispatcher(
        settings=settings,
        feed=feed,
        write_msg=write_msg,
        voice=voice,
        run_state=slots_idle_state,
        loop=ai_loop,
    )
    _emit_event = proactive_dispatcher.emit
    agent = build_loop(
        read_msg, use_real, db_path, provider, skills_factory, confirmer=batch_confirmer,
        emit_event=_emit_event,
        feed=feed,
    )
    agent.invoker.emit_event = _emit_event  # 真实技能（watch_command）后台通知走同一条 gated 通道
    # watch_command 跨重启恢复：任务落 jobs.db；上代进程的 running 孤儿重跑或标失败，全部 Feed 记账
    jobs_store = JobsStore(os.path.join(os.path.dirname(db_path), "jobs.db"))
    _recover_background_jobs(feed, getattr(agent.skills, "background_jobs", None),
                             jobs_store, _emit_event)
    # 截图唤起（v1.1）：invoke_context 暂存屏幕描述，下一次 run 注入后清空
    invoke_ctx = {"text": invoke_context_text, "ts": time.time() if invoke_context_text else 0.0}
    # 免确认集合接到闸门：命中后 decide 直接 AUTO（连 confirmation_needed 都不发）
    agent.invoker.gate.session_allowed = remembered_confirm

    # 工具域（R-13 第二步拆分序 1）：_running_tasks/_feed_stats/_collect_widgets/_mem_list
    # 已迁 runtime/helpers.py——共享状态（agent/feed）经 ctx 注入，此处绑回原闭包名
    # （handler 层与 mobile 域调用点零改动）。
    ctx.agent = agent
    ctx.feed = feed
    _running_tasks = functools.partial(helpers._running_tasks, ctx)
    _feed_stats = functools.partial(helpers._feed_stats, ctx)
    _collect_widgets = functools.partial(helpers._collect_widgets, ctx)
    _mem_list = functools.partial(helpers._mem_list, ctx)

    # 感知是增强面：Keychain/SQLite 不可用时保持关闭，绝不降级明文或拖垮大脑。
    pstore = perception_store
    if pstore is None and use_real:
        try:
            from .perception import PerceptionStore

            pstore = PerceptionStore(perception_db_path())
        except Exception as e:
            settings["perception.master"] = False
            log(f"感知不可用（保持关闭）：{e}")

    # Distiller（感知 v3）：离线深加工层。perception.distill 关闭时调度循环直接跳过，零出站。
    distiller = None
    if pstore is not None:
        try:
            distiller = Distiller(
                store=DistillerStore(os.path.join(os.path.dirname(db_path), "distill.db")),
                pstore=pstore,
                provider=agent.provider,
                memory=agent.memory,
                feed=feed,
                memories_fn=lambda: agent.memory.recall("作息 使用习惯 工作模式", "default"),
                # 提炼器总结用 default 桶（无会话维度；不掺具体会话对话，避免串台）
                history_fn=lambda: agent.history.messages()[-10:],
            )
        except Exception as e:
            log(f"提炼器初始化失败（不影响主链路）：{e}")
            distiller = None

    if pstore is not None:
        from .perception import LoadScreenContentTool, LoadUserActivityTool

        # 与 sensors 共用同一 store、与 settings_set 共用同一可变字典，开关即时生效。
        agent.skills.register(LoadUserActivityTool(pstore, settings))
        agent.skills.register(LoadScreenContentTool(pstore, settings))

    perception_stop = threading.Event()
    perception_thread = None
    if pstore is not None:
        try:
            pstore.purge()
        except Exception as e:
            log(f"感知过期清理失败：{e}")
        if use_real or perception_sensors is not None:
            try:
                if perception_sensors is None:
                    from .perception import (
                        PerceptionSensors,
                        sample_frontmost_details,
                        serialize_tree_text,
                    )

                    # B 源采样器：a11y 树优先；树空 → 截图留 path 待概括；secure input 查 Quartz。
                    # 前台 app/bundle/title 与 A 源同款取法（sample_frontmost_details）。
                    def _screen_sampler():
                        if agent.host is None:
                            return None
                        try:
                            details = sample_frontmost_details()
                            if details is None:
                                return None
                            app, bundle_id, title = details
                            tree = agent.host.a11y.frontmost_tree()
                            if tree and serialize_tree_text(tree):
                                return ("tree", tree, None, app, bundle_id, title)
                            shot = agent.host.screenshotter.capture()
                            return ("empty", None, shot, app, bundle_id, title)
                        except Exception:
                            return None

                    def _vision_summarizer(path: str):
                        # 整体容错：线程先于下方 _wvision 赋值启动时引用会抛 NameError
                        try:
                            if _wvision is None:
                                return None
                            import base64

                            with open(path, "rb") as f:
                                b64 = "data:image/png;base64," + base64.b64encode(f.read()).decode()
                            from .llm import summarize_screen

                            return summarize_screen(_wvision, b64)
                        except Exception:
                            return None

                    def _secure_input_checker() -> bool:
                        try:
                            import Quartz

                            return bool(Quartz.IsSecureEventInputEnabled())
                        except Exception:
                            return False

                    perception_sensors = PerceptionSensors(
                        pstore, settings,
                        screen_sampler=_screen_sampler if agent.host is not None else None,
                        vision_summarizer=_vision_summarizer,
                        secure_input_checker=_secure_input_checker,
                    )
                perception_thread = threading.Thread(
                    target=perception_sensors.run,
                    args=(perception_stop,),
                    daemon=True,
                    name="yibao-perception",
                )
                perception_thread.start()
            except Exception as e:
                settings["perception.master"] = False
                log(f"感知采样器启动失败（保持关闭）：{e}")

    perception_cleanup_task = asyncio.ensure_future(_perception_cleanup_loop(pstore, distiller))
    distiller_task = asyncio.ensure_future(_distiller_loop(settings, distiller))

    _wvision = None
    if vision_client is not None:
        _wvision = vision_client  # 测试注入（provider= 注入先例）
    elif vision_api_key() and computer_use_enabled():
        try:
            from .llm import ComputerUseClient

            _wvision = ComputerUseClient()
        except Exception as e:
            log(f"watch 视觉不可用（主动搭话禁用）：{e}")
    try:
        from .perception import sample_frontmost_bundle_id
    except Exception:
        sample_frontmost_bundle_id = None
    watch_service = WatchService(
        store=pstore,
        settings=settings,
        dispatcher=proactive_dispatcher,
        host=agent.host,
        vision=_wvision,
        frontmost=sample_frontmost_bundle_id,
        live_state=getattr(perception_sensors, "watch_state", None),
    )
    await watch_service.apply_settings()

    # mem0 降级（如多实例争 qdrant 锁）→ 显式推到壳，别让「失忆」无声发生
    mem = getattr(agent, "memory", None)
    if hasattr(mem, "set_status_callback"):
        mem.set_status_callback(
            lambda text: ai_loop.call_soon_threadsafe(
                write_msg, {"type": "event", "event": {"kind": "error", "text": text}}
            )
        )
    # 启动握手：壳靠它确认大脑上线（守护重启后也靠它判断已恢复）
    write_msg({"type": "hello", "version": 1, "permissions": _permissions_status()})

    reminder_task = asyncio.ensure_future(_reminder_loop(
        agent=agent, settings=settings, feed=feed, voice=voice,
        run_state=slots_idle_state, write_msg=write_msg, dispatcher=proactive_dispatcher))

    if http_enabled is None:
        http_enabled = use_real  # 测试默认关；生产（use_real=True）默认开
    bridge_server = None  # aiohttp runner（Task 4 起）：deps 闭包依赖后文定义的 _drive_run 等，启动挪到主循环前

    def _reader():
        while True:
            msg = read_msg()
            # 看门狗心跳：读线程直接答 pong（循环被长任务占住时也不误杀）；
            # 循环 _TICK_FRESH_S 秒未调度 = 真卡死 → 扣住 pong 让看门狗杀掉重启
            if isinstance(msg, dict) and msg.get("type") == "ping":
                lag = time.monotonic() - _LOOP_TICK["t"]
                if lag < _TICK_FRESH_S:
                    write_msg({"type": "pong"})
                else:
                    log(f"主循环 {lag:.0f}s 未调度，扣住 pong 待看门狗处置")
                continue
            try:
                ai_loop.call_soon_threadsafe(queue.put_nowait, msg)
            except RuntimeError:
                return  # 事件循环已关（进程退出中），daemon 读者线程随之结束
            if msg is None:
                return

    threading.Thread(target=_reader, daemon=True).start()

    async def _stream_agent(text: str, rid, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = "", emit_done: bool = True):
        t0 = time.monotonic()
        tts_q: asyncio.Queue | None = asyncio.Queue() if voice is not None else None
        tts_holds_lock = False
        if tts_q is not None:
            if tts_lock.locked():
                # 另一会话正在播报（spec §D 单声道）：本 run 静默不播（文字流式照出），
                # 不排队——排队念旧话比不念更怪。tts_q 置空后 speaking/chunk 进队逻辑整体跳过。
                tts_q = None
                write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id,
                           "event": {"kind": "notice", "text": "正在播报另一段对话，这段不念了"}})
            else:
                # 抢锁→持锁整轮（finally 里 TTS 排干后释放）：打断三连取消只作用本槽，
                # 全局播放器的停止仅限持锁者——A 的打断掐不掉 B 正在播的音。
                await tts_lock.acquire()
                tts_holds_lock = True
        tts_task = asyncio.create_task(_pump_tts(tts_q, cancel, surface, conversation_id)) if tts_q is not None else None
        started_speaking = False
        saw_interrupted = False
        try:
            async for event in agent.arun(text, cancel, surface=surface, conversation_id=conversation_id or None):
                if event.kind == "interrupted":
                    saw_interrupted = True
                write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": event.model_dump(mode="json")})
                if (
                    tts_q is not None
                    and event.kind == "final_reply_chunk"
                    and event.text
                ):
                    if not started_speaking:
                        started_speaking = True
                        write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "speaking"}})
                    await tts_q.put(event.text)
        except Exception as e:
            # arun 抛异常（如 provider 400）→ 发 error + 停 TTS，别让前端卡死
            cancel.set()
            write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "error", "text": f"大脑出错：{e}"}})
        finally:
            if tts_q is not None:
                await tts_q.put(None)  # 收尾哨兵，唤醒可能在 get() 上等待的 _pump_tts
            if tts_task is not None:
                await tts_task
            if tts_holds_lock:
                tts_lock.release()  # 本槽播报排干后再放锁，下一会话才能开念
            # LLM 已吐完 final_reply 后打断只停 TTS，arun 不再 yield interrupted；
            # 前端靠 interrupted 回 idle，不发则停止按钮停在「说话中」。
            if cancel.is_set() and not saw_interrupted:
                write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "interrupted"}})
            if emit_done:  # 连续语音会话里 run_done 由 _drive_voice_start 在会话结束时统一发
                write_msg(_run_done_msg(rid, conversation_id))
            log(f"run 完成 rid={rid}（{time.monotonic() - t0:.1f}s）")

    async def _drive_run(text: str, rid, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = ""):
        await _stream_agent(text, rid, cancel, surface, conversation_id)

    # voice 域续：_drive_voice_start 已迁 runtime/voice.py——其回调的 run 流
    # （_stream_agent）暂留 serve_async，经 ctx.stream_agent 注入。
    ctx.stream_agent = _stream_agent
    _drive_voice_start = voice_domain.drive_voice_start

    # mobile/HTTP 域（R-13 第二步拆分序 2）：_submit_run/_interrupt_mobile/_confirm_mobile/
    # _mobile_state/_mobile_feed/_register_push + _http_deps 装配已迁 runtime/mobile.py。
    # 共享状态注入已随各域上移（见 ctx 构造/runs 域/settings 段）。

    # HTTP 面（扩展桥+移动 API）：deps 里的绑定依赖上文 _drive_run 等，故在主循环前才组装启动
    ctx.settings = settings
    ctx.pending_confirms = pending_confirms
    ctx.early_answers = early_answers
    ctx.confirm_meta = confirm_meta
    ctx.confirm_done = _confirm_done
    ctx.drive_run = _drive_run
    mobile = MobileDomain(ctx)
    _mobile_feed = mobile.feed  # _h_feed handler 与手机 /v1/feed 端点共用同一组装
    if http_enabled:
        bridge_server = await mobile.start_http()

    # ---- 消息分发：handlers 查表（R-13 拆分；分支体原样搬运，continue→return） ----
    _handlers: dict[str, object] = {}

    async def _h_run(msg: dict) -> None:
        rtype = msg.get("type")
        if rtype == "voice_start" and voice is None:
            # 语音不可用（未启用/初始化失败）：不许静默吞掉——前端会永远卡「聆听中」
            rid = msg.get("id")
            log("voice_start 收到但语音栈不可用")
            write_msg({"type": "event", "event": {"kind": "error", "text": "语音不可用：麦克风初始化失败或被禁用"}})
            write_msg(_run_done_msg(rid, str(msg.get("conversation_id") or "")))
            return
        surface = str(msg.get("surface") or "pet")  # 会话分流：随 run 贯穿事件流与历史
        conversation_id = str(msg.get("conversation_id") or "")  # M3：会话归属随 run 贯穿（sidecar 单流，事件带归属）
        if rtype == "run":
            text, rid = msg.get("text", ""), msg.get("id")
            # 截图唤起：新鲜（<60s）的屏幕描述注入本次 run，一次性消费
            ctx_text = _consume_invoke_context(invoke_ctx)
            if ctx_text:
                text = f"[屏幕上下文] {ctx_text}\n\n{text}"

            async def _start(c, t=text, r=rid, s=surface, ci=conversation_id):
                # 附件图片（粘贴截图落盘 chip）：【附件：path】指向图片 → vision 描述注入，
                # 主模型不必多模态；未配置/无图/失败一律静默。挪进调度任务里做——
                # 串行 vision HTTP 堵的是本 run，不是主消息循环（审批/其他 run 不被卡）
                if _wvision is not None and "【" in t:
                    att_desc = await _offload(_describe_image_attachments, t, _wvision)
                    if att_desc:
                        t = f"[附件图片内容]\n{att_desc}\n\n{t}"
                await _drive_run(t, r, c, s, ci)

            log(f"run 受理 rid={rid} surface={surface} conv={conversation_id}：{text[:30]!r}")
            _schedule_run(surface, rid, _start, conversation_id)
        elif voice is not None:
            rid = msg.get("id")
            cont = bool(msg.get("continuous"))
            start = lambda c, r=rid, s=surface, ci=conversation_id, ct=cont: _drive_voice_start(r, c, s, ci, ct)
            log(f"voice_start 受理 rid={rid} surface={surface} conv={conversation_id} continuous={cont}")
            _schedule_run(surface, rid, start, conversation_id)
        else:
            return

    async def _h_panel_action(msg: dict) -> None:
        if _is_readonly_direct(msg, agent):
            # L0 只读直调：独立任务并发跑，不占槽位、不抢占在跑的 run（编辑器/面板加载数据不该踩对话）
            async def _ro(m=msg):
                try:
                    await handle_panel_action(m, agent, write_msg, run_text=_readonly_no_run)
                except Exception as e:
                    log(f"只读面板调用异常：{type(e).__name__}: {e}")

            t = asyncio.ensure_future(_ro())
            readonly_tasks.add(t)
            t.add_done_callback(readonly_tasks.discard)
            return
        # 面板写操作/意图方法：与 run 同槽位（按 conversation_id 取槽：同会话抢占 /
        # 跨会话并行；default 槽内跨 surface 仍排队，主循环不阻塞）
        surface = str(msg.get("surface") or "pet")
        conversation_id = str(msg.get("conversation_id") or "")
        slot = _slot(conversation_id)
        _preempt_if_same_surface(slot, surface, conversation_id)
        prev = slot["task"]
        slot["surface"] = surface
        start = lambda c, m=msg, s=surface, ci=conversation_id: handle_panel_action(
            m, agent, write_msg, run_text=lambda text, rid, c=c, s=s, ci=ci: _stream_agent(text, rid, c, s, ci)
        )

        async def _marked_panel(cancel, s=start, sf=surface, sl=slot, ci=conversation_id):
            sl["running_surface"] = sf
            _run_ctx.set({"cancel": cancel, "surface": sf, "conversation_id": ci})
            await s(cancel)

        slot["task"] = asyncio.ensure_future(
            _chain_start(slot, prev, _marked_panel, slot["preempt_gen"])
        )

    async def _h_interrupt(msg: dict) -> None:
        # 用户主动打断（spec §E 定向化）：带 conversation_id → 只打断该会话槽
        # （A 会话的打断不伤 B 会话）；不带 → 全停（旧行为，兼容无会话维度的调用方）。
        _int_cid = str(msg.get("conversation_id") or "")
        if _int_cid:
            _int_slot = run_slots.get(_int_cid)
            if _int_slot is not None:
                _preempt_current(_int_slot)
        else:
            for _int_slot in run_slots.values():
                _preempt_current(_int_slot)

    async def _h_panel_context(msg: dict) -> None:
        # 壳上面板焦点变化：存下来，下次 run 注入 LLM 上下文
        _FOCUS["value"] = msg.get("focus")

    async def _h_confirm_batch(msg: dict) -> None:
        # 批量确认回执（spec §3.2）：对每个 item 兑现 future 或存 early_answers
        # （future 没建 = batch_confirmer 还没注册，confirm_batch 先到）。
        # 单 confirm 走 size=1 的 items（旧 confirm IPC 已退役，统一 batch）。
        for item in (msg.get("items") or []):
            cid = str(item.get("id") or "")
            if not cid:
                continue
            approved = bool(item.get("approved", False))
            remember = bool(item.get("remember", False))
            if cid.startswith("perm_"):
                # coding 工具审批：双通道幂等兑现（另一通道 coding.decide 备用），
                # 不占 pending_confirms/early_answers（runner 不经 batch_confirmer）
                _fulfill_coding_perm(cid, approved)
                _confirm_done.append(cid)
                continue
            fut = pending_confirms.get(cid)
            if fut is not None and not fut.done():
                fut.set_result((approved, remember))
            else:
                # confirmer 还没注册（消息先于 run 任务到达）→ 缓存，由 batch_confirmer 兑现
                early_answers[cid] = (approved, remember)
            # 跨端防重：壳已处理的 cid 记入 _confirm_done——手机端随后点同一确认
            # → _confirm_mobile 判已处理 → 404，答案也不会滞留在 early_answers。
            _confirm_done.append(cid)
        write_msg({"type": "confirm_batched", "ok": True})

    async def _h_feed(msg: dict) -> None:
        # 主屏查询：动态列表（倒序）+ 问候统计（组装在 _mobile_feed，与手机 /v1/feed 同源）
        try:
            limit = int(msg.get("limit") or 60)
        except (TypeError, ValueError):
            limit = 60
        write_msg({"type": "feed", **_mobile_feed(limit)})

    async def _h_distill_now(msg: dict) -> None:
        # 设置页「立即提炼昨日」：master/distill 任一关闭直接拒绝（零出站）；运行可长达 60s，挪线程池
        if distiller is None or not (settings.get("perception.master") and settings.get("perception.distill")):
            write_msg({"type": "distill_now", "ok": False, "reason": "disabled"})
        else:
            result = await _offload(distiller.run_yesterday, "manual")
            write_msg({"type": "distill_now", "ok": True, "result": result})

    async def _h_recap_check(msg: dict) -> None:
        # 晨报（fire-and-forget）：闸门→去重→选材(昨日反刍+今日提醒)→emit reminder(morning_recap)→标记今天
        try:
            import datetime as _dt
            today = _dt.date.today().isoformat()
            # 今日提醒（晨报第二段）：pending 里 fire_at 落在今天的项，按时间升序
            #（reminder_store 由 build_loop 建并挂在 agent 上——serve_async 作用域里没有这个名字）
            now = time.time()
            lt = time.localtime(now)
            day_start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            todays: list[dict] = []
            rstore = getattr(agent, "reminder_store", None)
            if rstore is not None:
                todays = sorted(
                    ({"fire_at": float(r["fire_at"]), "text": str(r.get("text") or "")}
                     for r in rstore.list_pending()
                     if day_start <= float(r.get("fire_at", 0)) < day_start + 86400),
                    key=lambda r: r["fire_at"],
                )
            decide = _recap_decide(
                settings=settings,
                last_recap_day=distiller.store.recap_last_day() if distiller else None,
                today=today,
                yesterday_items=(distiller.store.day_items(
                    (_dt.date.today() - _dt.timedelta(days=1)).isoformat())
                    if distiller else []),
                hour=lt.tm_hour,
                todays_reminders=todays,
            )
            if decide is not None:
                _emit_event({"kind": "reminder", "type": "morning_recap",
                             "text": decide["text"], "day": decide["day"]})
                if distiller is not None:
                    distiller.store.set_recap_day(today)
        except Exception as e:
            log(f"recap_check 失败：{e}")

    async def _h_distill_timeline(msg: dict) -> None:
        # 设置页/回顾视图：近 N 天提炼聚合（distiller 不在则空数组）
        try:
            days = int(msg.get("days") or 14)
            write_msg({"type": "distill_timeline",
                       "days": distiller.store.recent_days(days) if distiller else []})
        except Exception as e:
            log(f"distill_timeline 失败：{e}")
            write_msg({"type": "distill_timeline", "days": []})

    async def _h_feed_stats(msg: dict) -> None:
        # 设置页「主动行为统计」：近 N 天主动行为聚合（默认 7 天）
        try:
            days = int(msg.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        write_msg({"type": "feed_stats", "stats": feed.stats(days=days)})

    async def _h_invoke_context(msg: dict) -> None:
        # 截图唤起（v1.1）：抓屏 → vision 一句话描述 → 暂存待下次 run 注入。
        # 无截图能力/无视觉配置/描述失败一律静默跳过；测试可注入 invoke_context_text。
        if invoke_context_text is None and agent.host is not None and _wvision is not None:
            try:
                shot = agent.host.screenshotter.capture()
                with open(shot, "rb") as f:
                    import base64 as _b64mod
                    b64 = "data:image/png;base64," + _b64mod.b64encode(f.read()).decode()
                desc = _describe_screen(_wvision, b64)
                if desc:
                    invoke_ctx.update({"text": desc, "ts": time.time()})
            except Exception as e:
                log(f"唤起抓屏失败（已跳过）：{e}")

    async def _h_snip_capture(msg: dict) -> None:
        # 截图即问（E）：壳侧 overlay 选区（物理像素）→ 区域截图 → b64 暂存待 vision_query。
        # 无截图能力/失败一律静默跳过。
        if agent.host is not None:
            try:
                shot = agent.host.screenshotter.capture_region(
                    int(msg.get("left", 0)), int(msg.get("top", 0)),
                    int(msg.get("width", 1)), int(msg.get("height", 1)),
                )
                import base64 as _b64snip

                with open(shot, "rb") as f:
                    snip_ctx.update({
                        "b64": "data:image/png;base64," + _b64snip.b64encode(f.read()).decode(),
                        "ts": time.time(),
                    })
            except Exception as e:
                # 失败清旧暂存：别让下次 vision_query 拿上一次的截图回答
                snip_ctx.update({"b64": None})
                log(f"区域截图失败（已跳过）：{e}")

    async def _h_vision_query(msg: dict) -> None:
        # 截图即问：暂存区域截图 + 问题 → vision 直答（不走 run，不占对话历史）。
        # 复用 run 的事件/run_done 协议（id 与 run_input 同为 0），壳侧状态机零改动。
        rid_vq = msg.get("id", 0)
        question = str(msg.get("question") or "").strip()[:500]

        def _vq_emit(ev: Event) -> None:
            write_msg({"type": "event", "surface": "pet", "event": ev.model_dump(mode="json")})

        if not question:
            _vq_emit(Event(kind="error", text="空问题"))
        elif _wvision is None:
            _vq_emit(Event(kind="error", text="视觉端点未配置（YIBAO_VISION_*），无法截图问答"))
        else:
            b64 = _peek_snip(snip_ctx)
            if b64 is None:
                _vq_emit(Event(kind="error", text="截图已过期或尚未框选，请 ⌘⇧I 重新框选"))
            else:
                from .llm import answer_image_query

                ans = await _offload(answer_image_query, _wvision, b64, question)
                if ans:
                    _vq_emit(Event(kind="final_reply", text=ans))
                else:
                    _vq_emit(Event(kind="error", text="截图问答失败，请重试"))
        write_msg({"type": "run_done", "id": rid_vq})

    async def _h_feed_mark_read(msg: dict) -> None:
        # 主屏点掉单条：feed.mark_read 容错（坏 id 返回 False，不抛）
        fid = int(msg.get("id", 0))
        ok = feed.mark_read(fid)
        write_msg({"type": "feed_marked_read", "id": fid, "ok": ok})

    async def _h_feed_mark_all_read(msg: dict) -> None:
        # 主屏「全部已读」：返回受影响行数
        n = feed.mark_all_read()
        write_msg({"type": "feed_all_read", "n": n})

    async def _h_feed_mark_status(msg: dict) -> None:
        # C 子项目：处置态（follow/ignore/none），与 read 正交
        fid = int(msg.get("id", 0))
        status = str(msg.get("status", "none"))
        ok = feed.set_status(fid, status)
        write_msg({"type": "feed_status_set", "id": fid, "status": status, "ok": ok})

    async def _h_feed_feedback(msg: dict) -> None:
        # 误报反馈（信任仪表写侧）：👍/👎 落 meta.feedback，同类降频由 dispatcher 执行
        fid = int(msg.get("id", 0))
        ok = feed.set_feedback(fid, str(msg.get("feedback", "none")))
        write_msg({"type": "feed_feedback_set", "id": fid, "ok": ok})

    async def _h_widgets(msg: dict) -> None:
        # 主屏查询：插件 widget 卡片逐个取数（panel_payload 形状 + open 跳转方法）
        write_msg({"type": "widgets", "widgets": await _collect_widgets()})

    async def _h_mem_list(msg: dict) -> None:
        # 记忆管理页：全命名空间分组列出 + 记忆后端状态（未就绪/降级时前端给提示）
        mem = agent.memory
        write_msg({"type": "mem_list", "items": await _mem_list(),
                   "ready": bool(getattr(mem, "ready", True)), "failed": bool(getattr(mem, "failed", False))})

    async def _h_mem_delete(msg: dict) -> None:
        mid = str(msg.get("mem_id") or "")  # 信封 id 字段被请求序号占用，记忆 id 走 mem_id
        try:
            await _offload(agent.memory.delete_by_id, mid)
            write_msg({"type": "mem_deleted", "id": mid, "ok": True})
        except Exception as e:
            write_msg({"type": "mem_deleted", "id": mid, "ok": False, "error": str(e)})

    async def _h_mem_edit(msg: dict) -> None:
        # 记忆管理「可改」：按 mem_id 替换文本；空文本/坏 id 明确报错，不静默
        mid = str(msg.get("mem_id") or "")
        text = str(msg.get("text") or "").strip()
        if not mid or not text:
            write_msg({"type": "mem_edited", "id": mid, "ok": False,
                       "error": "记忆 id 与新文本都不能为空"})
            return
        try:
            ok = await _offload(agent.memory.update, mid, text)
            if ok:
                write_msg({"type": "mem_edited", "id": mid, "ok": True})
            else:
                write_msg({"type": "mem_edited", "id": mid, "ok": False,
                           "error": "记忆不存在或后端更新失败"})
        except Exception as e:
            write_msg({"type": "mem_edited", "id": mid, "ok": False, "error": str(e)})

    async def _h_settings_get(msg: dict) -> None:
        write_msg({"type": "settings", "values": {**settings, "watch.status": watch_service.status()}})

    async def _h_http_pair_info(msg: dict) -> None:
        # 配对信息（手机设置页扫码/手输 URL 用）：内网 IP + 实际监听口/绑定地址
        write_msg({"type": "http_pair_info", "lan_ip": _lan_ip(),
                   "port": http_port(), "bind": str(settings.get("http.bind") or "127.0.0.1")})

    async def _h_settings_set(msg: dict) -> None:
        vals = msg.get("values")
        if isinstance(vals, dict):
            try:
                accepted = dict(vals)
                if accepted.get("watch.enabled"):
                    accepted["perception.master"] = True
                    accepted["perception.activity"] = True
                if accepted.get("watch.screen_enabled"):
                    accepted["perception.master"] = True
                    accepted["perception.app"] = True
                if pstore is None and accepted.get("perception.master"):
                    accepted["perception.master"] = False
                save_settings(accepted)  # 只落已知键；写盘成功后重读合并
                settings.update(load_settings())
                await watch_service.apply_settings()
            except Exception as e:
                log(f"设置保存失败：{e}")
        write_msg({"type": "settings", "values": {**settings, "watch.status": watch_service.status()}})

    async def _h_dock_list(msg: dict) -> None:
        # 主屏 Dock 查询：pinned 优先 + 频率补齐（详见 _dock_list）
        write_msg({"type": "dock_list", "dock": _dock_list(agent.log, _plugin_summaries_list())})

    async def _h_set_dock_pin(msg: dict) -> None:
        # 主屏 Dock 固定/取消固定：改 settings.dock_pinned，写入侧 enforce 上限 _DOCK_MAX
        pid = str(msg.get("pid") or "").strip()
        on = bool(msg.get("on"))
        cur_list = list(load_settings().get("dock_pinned") or [])
        # 去重去空（脏数据兜底）
        seen: set[str] = set()
        cleaned = []
        for x in cur_list:
            xs = str(x).strip()
            if xs and xs not in seen:
                seen.add(xs)
                cleaned.append(xs)
        cur_list = cleaned
        ok = True
        if on:
            if pid and pid not in seen:
                if len(cur_list) >= _DOCK_MAX:
                    ok = False  # 超上限拒绝（config 层不校验长度，这里拦）
                else:
                    cur_list.append(pid)
        else:
            cur_list = [x for x in cur_list if x != pid]
        if ok and pid:
            try:
                save_settings({"dock_pinned": cur_list})
                settings["dock_pinned"] = list(cur_list)
            except Exception as e:
                log(f"dock_pinned 写入失败：{e}")
                ok = False
        write_msg({"type": "dock_pin_set", "pid": pid, "ok": ok,
                   "dock": _dock_list(agent.log, _plugin_summaries_list())})

    async def _h_perception_list(msg: dict) -> None:
        if pstore is None:
            write_msg({"type": "perception", "items": [], "sources": [], "available": False})
            return
        try:
            limit = max(1, min(int(msg.get("limit") or 60), 200))
            before_raw = msg.get("before_id")
            before_id = int(before_raw) if before_raw is not None else None
            items = pstore.list(limit=limit, before_id=before_id)
            sources = pstore.sources()
            write_msg({"type": "perception", "items": items, "sources": sources, "available": True})
        except Exception as e:
            write_msg({"type": "perception", "items": [], "sources": [], "available": True, "error": str(e)})

    async def _h_perception_delete(msg: dict) -> None:
        per_raw = msg.get("per_id")
        if pstore is None or per_raw is None:
            write_msg({"type": "perception_deleted", "id": per_raw, "ok": False, "error": "感知存储不可用"})
            return
        try:
            per_id = int(per_raw)
            write_msg({"type": "perception_deleted", "id": per_id, "ok": bool(pstore.delete(per_id))})
        except Exception as e:
            write_msg({"type": "perception_deleted", "id": per_raw, "ok": False, "error": str(e)})

    async def _h_perception_clear(msg: dict) -> None:
        if pstore is None:
            write_msg({"type": "perception_cleared", "count": 0, "error": "感知存储不可用"})
            return
        try:
            write_msg({"type": "perception_cleared", "count": int(pstore.clear())})
        except Exception as e:
            write_msg({"type": "perception_cleared", "count": 0, "error": str(e)})

    async def _h_check_permissions(msg: dict) -> None:
        write_msg({"type": "permissions", "permissions": _permissions_status()})

    async def _h_prompt_permission(msg: dict) -> None:
        which = msg.get("which")
        if which == "ax":
            permissions.prompt_ax()
        elif which == "screen":
            permissions.prompt_screen()
        elif which == "input":
            permissions.prompt_input()
        write_msg({"type": "permissions", "permissions": _permissions_status()})

    _handlers["run"] = _h_run
    _handlers["voice_start"] = _h_run  # 复合分支：原 if rtype in ("run", "voice_start") 共用一体
    _handlers["panel_action"] = _h_panel_action
    _handlers["interrupt"] = _h_interrupt
    _handlers["panel_context"] = _h_panel_context
    _handlers["confirm_batch"] = _h_confirm_batch
    _handlers["feed"] = _h_feed
    _handlers["distill_now"] = _h_distill_now
    _handlers["recap_check"] = _h_recap_check
    _handlers["distill_timeline"] = _h_distill_timeline
    _handlers["feed_stats"] = _h_feed_stats
    _handlers["invoke_context"] = _h_invoke_context
    _handlers["snip_capture"] = _h_snip_capture
    _handlers["vision_query"] = _h_vision_query
    _handlers["feed_mark_read"] = _h_feed_mark_read
    _handlers["feed_mark_all_read"] = _h_feed_mark_all_read
    _handlers["feed_mark_status"] = _h_feed_mark_status
    _handlers["feed_feedback"] = _h_feed_feedback
    _handlers["widgets"] = _h_widgets
    _handlers["mem_list"] = _h_mem_list
    _handlers["mem_delete"] = _h_mem_delete
    _handlers["mem_edit"] = _h_mem_edit
    _handlers["settings_get"] = _h_settings_get
    _handlers["http_pair_info"] = _h_http_pair_info
    _handlers["settings_set"] = _h_settings_set
    _handlers["dock_list"] = _h_dock_list
    _handlers["set_dock_pin"] = _h_set_dock_pin
    _handlers["perception_list"] = _h_perception_list
    _handlers["perception_delete"] = _h_perception_delete
    _handlers["perception_clear"] = _h_perception_clear
    _handlers["check_permissions"] = _h_check_permissions
    _handlers["prompt_permission"] = _h_prompt_permission

    while True:
        msg = await queue.get()
        if msg is None:
            # stdin 关闭（壳退出）：不再接新活。遍历所有槽（并发对话 spec §F），
            # 每个在跑任务给 5s 自然收尾；
            # 超时说明它卡死了（确认未答/hung）→ 取消 + 2s 清场 → 强 cancel。
            # 不能无限等：否则大脑变孤儿占着 qdrant 锁/麦
            # （2026-07-19 实测孤儿 brain 存活 3 小时，新 brain 被迫记忆降级）。
            for _slot in run_slots.values():
                task = _slot["task"]
                if task is None or task.done():
                    continue
                done, _ = await asyncio.wait({task}, timeout=5)
                if not done:
                    if _slot["cancel"] is not None:
                        _slot["cancel"].set()
                    done, _ = await asyncio.wait({task}, timeout=2)
                    if not done:
                        task.cancel()
            # 并发的只读面板调用都是快查询，给 3s 收尾；超时直接取消（进程要退了）
            if readonly_tasks:
                _, pending_ro = await asyncio.wait(readonly_tasks, timeout=3)
                for t in pending_ro:
                    t.cancel()
            tick_task.cancel()
            reminder_task.cancel()
            perception_cleanup_task.cancel()
            if bridge_server is not None:
                tap.close()  # 先给 SSE 订阅者投哨兵：handler 立即退出，cleanup 不用等 30s 心跳
                await bridge_server.cleanup()  # aiohttp AppRunner（cleanup 是协程，漏 await = 端口/连接不释放）
            await watch_service.stop()
            jobs = getattr(agent.skills, "background_jobs", None)
            if jobs is not None:
                jobs.shutdown()
            jobs_store.close()
            perception_stop.set()
            if perception_thread is not None:
                perception_thread.join(timeout=1)
            if pstore is not None:
                try:
                    pstore.close()
                except Exception:
                    pass
            return
        rtype = msg.get("type")
        _handler = _handlers.get(rtype)
        if _handler is not None:
            await _handler(msg)


def _build_voice_or_none():
    if not (voice_enabled() and sys.platform == "darwin"):
        return None
    try:
        from .voice import build_voice

        return build_voice(
            stt_model_dir(),
            vad_model_path(),
            tts_voice(),
            min_silence=vad_min_silence(),
            max_seconds=vad_max_seconds(),
        )
    except Exception as e:
        log(f"语音不可用，已禁用：{e}")
        return None


def _watch_parent() -> None:
    """父进程存活看门狗（守护线程）：ppid 变化（壳死后被 reparent 到 launchd）→ 自我了断。

    覆盖壳被 kill -9/崩溃时 stdin EOF 之外的遗漏路径——孤儿 brain 会长期占着 qdrant 锁，
    新 brain 被迫记忆降级（2026-07-21 实测两个昨日孤儿并存）。os._exit 保证循环卡死时也能死。
    """
    parent = os.getppid()
    while True:
        time.sleep(10)
        if os.getppid() != parent:
            log("父进程已退出（ppid 变化），自我了断")
            os._exit(0)


def main() -> int:
    reader, writer = line_reader(), line_writer()
    voice = _build_voice_or_none()
    threading.Thread(target=_watch_parent, daemon=True).start()
    # 数据目录分离：仓库时代的用户数据一次性迁走（sidecar/ → 应用数据目录）
    from . import config as _cfg

    _cfg.migrate_legacy_data(os.path.join(os.path.dirname(__file__), "..", ".."))
    os.makedirs(_cfg.data_dir(), exist_ok=True)  # sqlite/qdrant 不会自建父目录
    # 单实例锁 + 孤儿回收：壳被强杀时旧大脑可能活着独占 qdrant 锁，
    # 新大脑取锁前先把它们收掉；锁 fd 活到进程结束（OS 级，死即释）
    from .instance import ensure_single_instance


    try:
        _instance_lock_fd = ensure_single_instance(os.path.join(_cfg.data_dir(), "brain.lock"))
    except Exception as e:
        log(f"大脑单实例锁获取失败：{e}")
        return 1
    asyncio.run(
        serve_async(
            reader,
            writer,
            use_real=True,
            db_path=_cfg.audit_db_path(),
            voice=voice,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
