"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done、feed（主屏动态+统计）。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission、panel_context、feed。
"""
from __future__ import annotations

import asyncio
import contextvars
import itertools
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
from .config import a11y_enabled, computer_use_enabled, computer_use_max_steps, history_path, http_port, llm_api_key, load_settings, perception_db_path, plugin_data_dir, save_settings, screenshot_dir, stt_model_dir, tts_voice, vad_max_seconds, vad_min_silence, vad_model_path, vision_api_key, voice_enabled
from .feed import FeedStore
from .distiller import Distiller, DistillerStore
from .jobstore import JobsStore
from .history import ConversationHistory
from .http_api import EventTap, MobileDeps
from .ipc import Event, RiskLevel
from .llm import FakeProvider, OpenAICompatProvider, ToolCall
from .loop import AgentLoop, _offload
from .memory import FakeMemory, LazyMem0Memory
from .proactive import ProactiveDispatcher
from .plugins import LlmChat, get_mem_namespaces, get_plugin_summaries, get_widgets, panel_payload
from .safety import Decision, Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_composite import register_composite_skills
from .skills_real import ComputerUseSkill, register_real_skills
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

ReadMsg = Callable[[], dict | None]
WriteMsg = Callable[[dict], None]

# 面板焦点（v2 §5）：壳侧 panel_context 消息维护，run 时注入 LLM 上下文（「这个/它」有解）
_FOCUS: dict = {"value": None}

# 被抢占任务的收尾宽限（秒）：超时强制取消，防 hung 任务把槽位卡死（「点了没反应」的根）
_PREEMPT_GRACE_S = 8.0

# 连续语音会话（voice_start continuous）：退出语（确定性匹配，不过 LLM）、告别语、
# 开场提示、连续没听清几轮自动退（防无人时麦克风空转）。
_VOICE_EXIT_PHRASES = {"退出", "退出对话", "没事了", "没事", "不用了", "再见", "拜拜", "谢谢你", "谢谢", "先这样"}
_VOICE_SESSION_BYE = "好的，先聊到这儿，叫我随时来～"
_VOICE_SESSION_HINT = "连续对话中：你说完我答，答完接着听；说「退出」或点团子结束"
_VOICE_SESSION_MAX_EMPTY = 2
_EXIT_STRIP = "，。！？、~～… .!?,，"


def _is_exit_phrase(text: str) -> bool:
    """退出语判定：剥掉语气标点/空白后整句命中词表（「先这样了谢谢」这类混合句不拦，交给 LLM）。"""
    return text.strip(_EXIT_STRIP).strip() in _VOICE_EXIT_PHRASES

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
    reg = skills_factory() if skills_factory else SkillRegistry()
    if not skills_factory:
        reg.register(EchoSkill())
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
                    print(f"[yibao] computer-use 兜底未启用：{e}", file=sys.stderr)
                    cu_client = None
            register_real_skills(reg, describe=describe)
            register_composite_skills(reg)
            if cu_client is not None:
                try:
                    reg.register(ComputerUseSkill(cu_client, max_steps=computer_use_max_steps()))
                except Exception as e:
                    print(f"[yibao] computer-use 技能注册失败：{e}", file=sys.stderr)

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
            print(f"[yibao] MacHost 不可用，回退无基座：{e}", file=sys.stderr)

    active_plugins: set | None = None  # None=全量暴露（测试/兼容）；集合=路由式暴露
    reminder_store = None
    if use_real and not skills_factory:
        # 底座提醒存储先建：提醒管理插件（reminders capability）与底座技能共享同一实例
        from .reminders import ReminderStore, make_skills

        reminder_store = ReminderStore(os.path.join(os.path.dirname(db_path), "reminders.json"))
        _load_plugins_safe(reg, memory, prov, host, reminders=reminder_store, emit_event=emit_event)
        # 路由式暴露（§12-2）：插件 tool 默认隐藏，use_plugin 按需展开；
        # active 集合与 AgentLoop 共享（技能执行即改，下一步 LLM 调用即见新工具）
        from .plugins import get_plugin_summaries
        from .skills import UsePluginSkill

        active_plugins = set()
        reg.register(UsePluginSkill(reg, active_plugins, get_plugin_summaries()))
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
            print(f"[yibao] 插件 {pid}: {status}", file=sys.stderr)
    except Exception as e:
        print(f"[yibao] 插件加载失败（已跳过）：{e}", file=sys.stderr)


def _run_done_msg(rid, conversation_id: str = "") -> dict:
    """run_done 载荷（spec §E）：conversation_id 非空才带——空 = 无归属（旧路径），
    保持与旧客户端/旧断言的逐字节兼容（信封 _with_envelope 同样只带非空归属）。"""
    msg = {"type": "run_done", "id": rid}
    if conversation_id:
        msg["conversation_id"] = conversation_id
    return msg


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
    confirm_meta: dict[str, dict] = {}  # cid -> {skill_id, summary, risk, created_at, conversation_id, surface}：手机 /v1/state 待批列表
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

    def _slot(conversation_id: str) -> dict:
        """取（无则建）会话槽位；conversation_id 空 → default 槽。"""
        return run_slots.setdefault(conversation_id or "", {
            "task": None, "cancel": None, "preempt_gen": 0, "surface": None, "running_surface": None})

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

    # TTS 全局播报锁（spec §D）：物理声道只有一条，播报保持全局串行、不 per-会话化。
    # 持锁者独占播放器（打断三连取消只作用本槽，A 的打断掐不掉 B 正在播的音）；
    # 抢不到锁的 run 静默不播（文字流式照出）+ notice，不排队。
    tts_lock = asyncio.Lock()

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
            skill_id = getattr(action, "skill_id", "?")
            # 早到的 confirm_batch 直接兑现（future 还没建）
            if cid in early_answers:
                out[cid] = early_answers.pop(cid)
                continue
            fut = pending_confirms.setdefault(cid, ai_loop.create_future())
            _ctx = _run_ctx.get() or {}
            confirm_meta[cid] = {
                "skill_id": skill_id,
                # 可读摘要：params 非空 dict → k=v 逗号形式（手机审批页直接展示）；
                # 空 dict/非 dict → 回落 skill_id。截 120 字防长参数刷屏。
                "summary": (", ".join(f"{k}={v}" for k, v in (getattr(action, "params", None) or {}).items())[:120]
                            if isinstance(getattr(action, "params", None), dict) and action.params else skill_id),
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
            print(f"[yibao] 等待用户确认：{skill_id}", file=sys.stderr)
            try:
                waiters: set = {fut}
                if cancel_wait is not None:
                    waiters.add(cancel_wait)
                done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                if fut in done:
                    approved, remember = fut.result()
                    print(f"[yibao] 确认结果：{'允许' if approved else '拒绝'}（{skill_id}）", file=sys.stderr)
                    out[cid] = (bool(approved), bool(remember))
                else:
                    print(f"[yibao] 确认被抢占取消：{skill_id}", file=sys.stderr)
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

    def _running_tasks(limit: int = 20) -> list[dict]:
        """读取 agents 与后台命令，只返回 Home 需要的 running 摘要。"""
        adb_file = os.path.join(plugin_data_dir("agents"), "data.db")
        rows = []
        if os.path.exists(adb_file):
            try:
                from .plugindb import PluginDb

                adb = PluginDb("agents")
                try:
                    rows = adb.query(
                        "tasks", where={"status": "running"},
                        order="created_at DESC", limit=limit,
                    )
                finally:
                    adb.close()
            except Exception as e:
                print(f"[yibao] 进行中任务查询失败（已降级）：{e}", file=sys.stderr)

        out = []
        for row in rows:
            task_id = str(row.get("id") or "")
            if not task_id:
                continue
            kind = "script" if row.get("kind") == "script" else "agent"
            agent_name = str(row.get("agent") or "智能体")
            out.append({
                "id": task_id,
                "kind": kind,
                "label": "沙箱脚本" if kind == "script" else f"{agent_name} 任务",
                "prompt": str(row.get("prompt") or ""),
                "status": "running",
                "created_at": int(row.get("created_at") or 0),
            })
        jobs = getattr(agent.skills, "background_jobs", None)
        if jobs is not None:
            try:
                for job in jobs.list():
                    if job.get("status") != "running":
                        continue
                    out.append({
                        "id": str(job.get("task_id") or ""),
                        "kind": "script",
                        "label": str(job.get("name") or job.get("command") or "后台命令"),
                        "prompt": str(job.get("command") or ""),
                        "status": "running",
                        "created_at": int(job.get("started_at") or 0),
                    })
            except Exception as e:
                print(f"[yibao] 后台命令查询失败（已降级）：{e}", file=sys.stderr)
        # coding 插件运行中会话（P2 督导）：sessions 表 status=running 为准——_SESSIONS
        # 仅存于流式期间、重启即丢，陈旧 running 由 coding.stop 的陈旧兜底补发 stopped，
        # 这里照列让用户在主屏可见可停（与 agents 段同策略：只读表，不碰插件内存态）。
        cdb_file = os.path.join(plugin_data_dir("coding"), "data.db")
        if os.path.exists(cdb_file):
            try:
                from .plugindb import PluginDb

                cdb = PluginDb("coding")
                try:
                    crows = cdb.query(
                        "sessions", where={"status": "running"},
                        order="created_at DESC", limit=limit,
                    )
                finally:
                    cdb.close()
            except Exception as e:
                print(f"[yibao] coding 会话查询失败（已降级）：{e}", file=sys.stderr)
                crows = []
            perm = _coding_perm_registry()   # waiting 判定数据源（只读合并，同 _fulfill_coding_perm 先例）
            for row in crows:
                sid = str(row.get("id") or "")
                if not sid:
                    continue
                item = {
                    "id": sid,
                    "kind": "coding",
                    "label": "编码会话",
                    "prompt": str(row.get("prompt") or ""),
                    "status": "running",
                    "created_at": int(row.get("created_at") or 0),
                }
                # P2 督导补遗：_PERM 有该 sid 挂起审批（allow is None）→ waiting: true
                # （additive 字段，壳侧「正在跑」计数/展示可区分「等审批」；不消费则零改动）
                if perm and any(
                        rid.startswith(f"perm_{sid}_")
                        and isinstance(p, dict) and p.get("allow") is None
                        for rid, p in list(perm.items())):
                    item["waiting"] = True
                out.append(item)
        return sorted(out, key=lambda item: item.get("created_at", 0), reverse=True)[:limit]

    def _feed_stats(running_tasks: list[dict] | None = None) -> dict:
        """主屏问候统计：待办提醒 / 进行中任务 / 近 24h 完成任务。"""
        stats = {"pending_reminders": 0, "running_tasks": 0, "done_24h": 0}
        rstore = getattr(agent, "reminder_store", None)
        if rstore is not None:
            try:
                stats["pending_reminders"] = len(rstore.list_pending())
            except Exception:
                pass
        stats["running_tasks"] = len(running_tasks or [])
        stats["done_24h"] = feed.count_since("task", time.time() - 86400)
        stats["unread"] = feed.count_unread()
        stats["ignored"] = feed.count_ignored()
        return stats

    async def _collect_widgets() -> list[dict]:
        """主屏 widget 取数（OS 感 §4.2）：每个 widget 调其声明的 L0 method，
        返回 panel_payload 形状（+open 点击跳转方法）。单个失败/被拒只跳过，不拖垮其他。"""
        out = []
        for ref, decl in get_widgets().items():
            try:
                action = agent.invoker.propose(ToolCall(id=f"w_{ref}", skill_id=decl["method"], params={}))
                if agent.invoker.decide(action) != Decision.AUTO:  # 理论上是 L0 恒 AUTO；防御
                    continue
                result = await _offload(agent.invoker.execute, action, {})
                if not result.success:
                    print(f"[yibao] widget {ref} 取数失败（已跳过）：{result.error}", file=sys.stderr)
                    continue
                result.panel = ref
                payload = panel_payload(result)
                if payload is not None:
                    payload["open"] = decl.get("open")
                    out.append(payload)
            except Exception as e:
                print(f"[yibao] widget {ref} 取数异常（已跳过）：{e}", file=sys.stderr)
        return out

    # 感知是增强面：Keychain/SQLite 不可用时保持关闭，绝不降级明文或拖垮大脑。
    pstore = perception_store
    if pstore is None and use_real:
        try:
            from .perception import PerceptionStore

            pstore = PerceptionStore(perception_db_path())
        except Exception as e:
            settings["perception.master"] = False
            print(f"[yibao] 感知不可用（保持关闭）：{e}", file=sys.stderr)

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
            print(f"[yibao] 提炼器初始化失败（不影响主链路）：{e}", file=sys.stderr)
            distiller = None

    if pstore is not None:
        from .perception import LoadScreenContentSkill, LoadUserActivitySkill

        # 与 sensors 共用同一 store、与 settings_set 共用同一可变字典，开关即时生效。
        agent.skills.register(LoadUserActivitySkill(pstore, settings))
        agent.skills.register(LoadScreenContentSkill(pstore, settings))

    perception_stop = threading.Event()
    perception_thread = None
    if pstore is not None:
        try:
            pstore.purge()
        except Exception as e:
            print(f"[yibao] 感知过期清理失败：{e}", file=sys.stderr)
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
                print(f"[yibao] 感知采样器启动失败（保持关闭）：{e}", file=sys.stderr)

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
            print(f"[yibao] watch 视觉不可用（主动搭话禁用）：{e}", file=sys.stderr)
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

    async def _mem_list() -> list[dict]:
        """记忆管理页数据（OS 感 §4.4）：底座（译宝）+ 各插件命名空间分组列出。单空间失败不拖垮整体。"""
        groups = [("译宝", "", agent.user_id)]
        groups.extend((label, ns, f"{ns}:{agent.user_id}") for ns, label in get_mem_namespaces().items())
        out = []
        for label, ns, uid in groups:
            try:
                rows = await _offload(agent.memory.list_all, uid)
            except Exception as e:
                print(f"[yibao] 记忆列出失败（{label}，已跳过）：{e}", file=sys.stderr)
                continue
            for r in rows:
                out.append({"id": r["id"], "text": r["text"], "ns": ns, "label": label,
                            "created_at": r.get("created_at", "")})
        return out
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
                    print(f"[yibao] 主循环 {lag:.0f}s 未调度，扣住 pong 待看门狗处置", file=sys.stderr)
                continue
            try:
                ai_loop.call_soon_threadsafe(queue.put_nowait, msg)
            except RuntimeError:
                return  # 事件循环已关（进程退出中），daemon 读者线程随之结束
            if msg is None:
                return

    threading.Thread(target=_reader, daemon=True).start()

    async def _tts_chunks(tts_q: asyncio.Queue):
        while True:
            item = await tts_q.get()
            if item is None:
                return
            yield item

    async def _pump_tts(tts_q: asyncio.Queue, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = ""):
        if voice is None:
            return
        try:
            await voice.speak_stream(_tts_chunks(tts_q), cancel)
        except asyncio.CancelledError:
            return  # 打断命中合成/播放的正常取消，不是播报失败
        except Exception as e:
            write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": {"kind": "speaking_done"}})

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
            print(f"[yibao] run 完成 rid={rid}（{time.monotonic() - t0:.1f}s）", file=sys.stderr)

    async def _drive_run(text: str, rid, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = ""):
        await _stream_agent(text, rid, cancel, surface, conversation_id)

    async def _drive_voice_start(rid, cancel: asyncio.Event, surface: str = "pet", conversation_id: str = "", continuous: bool = False):
        # 连续对话（长按团子进入）：答完接着听。退出：退出语 / 连续两次没听清 / 打断。
        def _vev(event: dict) -> None:
            write_msg({"type": "event", "surface": surface, "conversation_id": conversation_id, "event": event})

        def _done() -> None:
            write_msg(_run_done_msg(rid, conversation_id))

        if continuous:
            _vev({"kind": "notice", "text": _VOICE_SESSION_HINT})
        empties = 0
        while True:
            _vev({"kind": "listening"})

            async def _watch_cancel():
                await cancel.wait()
                voice.stop_listen()  # 打断（interrupt）→ 录音循环下一拍退出

            watcher = asyncio.ensure_future(_watch_cancel())
            t0 = time.monotonic()
            try:
                text = await ai_loop.run_in_executor(None, voice.listen)
            except Exception as e:
                _vev({"kind": "error", "text": f"语音识别失败：{e}"})
                _done()
                return
            finally:
                watcher.cancel()
            print(f"[yibao] 聆听结束（{time.monotonic() - t0:.1f}s）：{text[:30]!r}", file=sys.stderr)
            if cancel.is_set():  # 聆听被打断：不走 listening_done（避免误进 think 态）
                _vev({"kind": "interrupted"})
                _done()
                return
            _vev({"kind": "listening_done", "text": text})
            if not text:
                if continuous:
                    empties += 1
                    if empties < _VOICE_SESSION_MAX_EMPTY:
                        continue  # 没听清：会话中不打岔，直接再听一轮
                    _vev({"kind": "notice", "text": "一会儿没说话，先退下啦，叫我随时来～"})
                    _done()
                    return
                _done()
                return
            empties = 0
            if continuous and _is_exit_phrase(text):
                # 退出语：固定告别（不过 LLM，确定性收尾）
                _vev({"kind": "final_reply", "text": _VOICE_SESSION_BYE})
                if tts_lock.locked():
                    # 另一会话正在播报（spec §D 单声道）：告别静默不抢声道、不排队
                    _done()
                    return
                _vev({"kind": "speaking"})

                async def _bye():
                    yield _VOICE_SESSION_BYE

                await tts_lock.acquire()
                try:
                    await voice.speak_stream(_bye(), cancel)
                except Exception as e:
                    print(f"[yibao] 会话告别播报失败：{e}", file=sys.stderr)
                finally:
                    tts_lock.release()
                if cancel.is_set():
                    _vev({"kind": "interrupted"})
                else:
                    _vev({"kind": "speaking_done"})
                _done()
                return
            # 连续会话的 run_done 由本函数在会话结束时发（每轮结束就发会让前端以为请求完结）
            await _stream_agent(text, rid, cancel, surface, conversation_id, emit_done=not continuous)
            if not continuous:
                return
            if cancel.is_set():
                _done()
                return
            # 答完接着听下一轮

    def _preempt_current(slot: dict) -> None:
        slot["preempt_gen"] += 1
        if slot["cancel"] is not None:
            slot["cancel"].set()

    def _preempt_if_same_surface(slot: dict, surface: str, conv_key: str) -> None:
        """同槽（= 同会话）新请求 → 抢占在跑任务。同会话跨 surface（同一
        conversation_id 从 Home 和 pet 同时发话）也视为同会话，照样抢占（spec §核心模型）。

        仅 default 槽（conv_key 为空 = 无 conversation_id 的遗留调用）保留旧的
        「跨 surface 不抢占、链式排队 + notice」（行为同现状）；跨会话不再走到这里——
        不同 conversation_id 落在不同槽位，直接真并行。

        比较对象是 slot["surface"] = 该槽最近一次受理的 surface（dispatch 时即写入，
        无「chain 任务还没跑起来」的调度竞态）。取舍：default 槽里 A(pet) 在跑、B(panel)
        排队中又来 C(pet) 时，C 会被判成跨 surface 而排队而非顶掉 A——三消息交替跨窗的
        极端场景，排队自愈、不会卡死，不为它引入 per-surface 代数。
        """
        prev = slot["task"]
        if prev is None or prev.done():
            return
        if conv_key or slot["surface"] == surface:
            _preempt_current(slot)
        else:
            print(f"[yibao] 跨 surface 请求排队（在跑={slot['surface']}，新={surface}）", file=sys.stderr)
            write_msg({"type": "event", "surface": surface, "event": {
                "kind": "notice", "text": "另一个窗口还在说，等它说完就轮到你…"}})

    def _schedule_run(surface: str, rid, start, conversation_id: str = "") -> None:
        """受理尾巴（run/voice_start/手机 chat 共用）：按 conversation_id 取槽——同槽
        新话顶旧话，跨槽真并行零等待。running_surface 在真正开跑时才写——手机 interrupt
        按它判域，排队窗口不误杀桌面轮。"""
        slot = _slot(conversation_id)
        _preempt_if_same_surface(slot, surface, conversation_id or "")
        prev = slot["task"]
        slot["surface"] = surface  # 受理即记录：下次 dispatch 判断同/跨 surface 无调度竞态

        async def _marked(cancel, s=start, sf=surface, sl=slot, ci=conversation_id or ""):
            sl["running_surface"] = sf
            # 归属上下文：batch_confirmer 读本槽 cancel / confirm_meta 记会话归属
            _run_ctx.set({"cancel": cancel, "surface": sf, "conversation_id": ci})
            await s(cancel)

        slot["task"] = asyncio.ensure_future(
            _chain_start(slot, prev, _marked, slot["preempt_gen"]))

    _MOB_SEQ = itertools.count(1)

    def _submit_run(text: str, conversation_id: str) -> dict:
        """手机 /v1/chat 受理：surface=mobile（不抢桌宠，桌宠也不抢手机）；会话槽位
        按 conversation_id 独立——手机与桌面、手机多会话之间真并行。
        不消费 invoke_ctx：那是桌面截图唤起的一次性上下文，留给桌面下一次 run。"""
        rid = f"mob_{next(_MOB_SEQ)}"
        start = lambda c, t=text, r=rid, ci=conversation_id: _drive_run(t, r, c, "mobile", ci)
        print(f"[yibao] run 受理 rid={rid} surface=mobile conv={conversation_id}：{text[:30]!r}", file=sys.stderr)
        _schedule_run("mobile", rid, start, conversation_id)
        return {"ok": True, "run_id": rid, "conversation_id": conversation_id}

    def _interrupt_mobile(conversation_id: str = "") -> bool:
        """手机打断：维持 surface 域限定（只动 running_surface=mobile 的槽），叠加
        conversation_id 定向（spec §E）——带 id 只打断该会话槽；不带 id 扫所有槽里
        在跑的 mobile 轮（旧行为）。
        task 判活对齐 _mobile_state：消掉「上轮收尾后陈旧的 running_surface=mobile」
        误报 True + 平白推进 preempt_gen 的跳窗口。壳 interrupt 是「全都停」或定向
        某槽；手机不该误伤桌面对话。"""
        if conversation_id:
            named = run_slots.get(conversation_id)
            slots = [named] if named is not None else []
        else:
            slots = list(run_slots.values())
        for slot in slots:
            task = slot["task"]
            if (slot.get("running_surface") == "mobile"
                    and task is not None and not task.done()
                    and slot["cancel"] is not None):
                _preempt_current(slot)
                return True
        return False

    def _confirm_mobile(cid: str, approved: bool, remember: bool) -> bool:
        """与壳 confirm_batch 同路径：兑现 future；confirmer 未注册（SSE 事件先到一步）
        存 early_answers 待兑现。重复点击 → False（404）。

        early_answers 设 32 条上界：真实竞态（SSE 事件先到一步）最多攒 1-2 条，
        存到 32 还没被 confirmer 取走，只可能是未知/垃圾 id——继续存就是无界堆积
        （持有 token 的客户端可无限灌条目），故满员后按未知处理 → False（404）。"""
        if cid in _confirm_done:
            return False
        if cid.startswith("perm_"):
            # coding 工具审批：与壳 confirm_batch 同路由（双通道幂等），不占 future/早到缓存；
            # 兑现失败（插件未加载/请求已清理）→ False（404），不假 ok
            if not _fulfill_coding_perm(cid, approved):
                return False
            _confirm_done.append(cid)
            return True
        fut = pending_confirms.get(cid)
        if fut is not None and not fut.done():
            fut.set_result((approved, remember))
        elif len(early_answers) < 32:
            early_answers[cid] = (approved, remember)
        else:
            return False  # 无 future 且早到缓存已满：未知 id，不当真
        _confirm_done.append(cid)
        return True

    def _mobile_state() -> dict:
        """/v1/state 载荷：running + pending。running = 任一槽在跑即非空闲（并发对话
        spec §F：多槽并行后 idle 判定看全局）。pending = confirm_meta 展开（L2 确认，
        条目带 conversation_id/surface 归属）
        + coding 审批只读合并 _PERM 挂起项（allow is None；confirmation_needed 只广播
        不落 confirm_meta，故在此补源）——生命周期随裁决/超时/stop 的 _PERM.pop 收敛，
        无需出队钩子。元素键名对齐 confirm_meta：id/skill_id/summary/risk/created_at。"""
        busy = next((s for s in run_slots.values()
                     if s["task"] is not None and not s["task"].done()), None)
        running = {"surface": busy["surface"]} if busy is not None else None
        pending = [{"id": cid, **meta} for cid, meta in confirm_meta.items()]
        perm = _coding_perm_registry()
        if perm:
            for rid, entry in list(perm.items()):
                if isinstance(entry, dict) and entry.get("allow") is None:
                    pending.append({"id": rid, "skill_id": "coding",
                                    "summary": str(entry.get("summary") or entry.get("tool") or "编码审批"),
                                    "risk": 1,
                                    "created_at": int(entry.get("created_at") or 0)})
        return {"running": running, "pending": pending}

    def _mobile_feed(limit: int = 60) -> dict:
        """/v1/feed 载荷（mobile M2）：与桌面 feed IPC 完全同形（items 倒序 + 问候统计 +
        进行中任务）。组装收敛在此，dispatch 的 feed 分支与手机端点共用一份。"""
        running_tasks = _running_tasks()
        return {"items": feed.recent(limit=limit),
                "stats": _feed_stats(running_tasks),
                "running_tasks": running_tasks}

    def _register_push(registration_id: str, platform: str) -> None:
        """推送设备登记（P4 极光发送消费）。同 registration_id 覆盖，防重复堆积。"""
        devices = [d for d in (settings.get("push.devices") or [])
                   if d.get("registration_id") != registration_id]
        devices.append({"registration_id": registration_id, "platform": platform, "added_at": int(time.time())})
        settings["push.devices"] = devices
        save_settings({"push.devices": devices})

    async def _chain_start(slot: dict, prev, start, queued_gen: int) -> None:
        """槽内串行：等本槽上一任务收尾再启动；主循环不在这里阻塞（ping 照答，看门狗
        不误杀）。「等上一任务」只在同槽内生效——跨槽（跨会话）零等待、真并行（spec §A）。

        排队期间本槽又来了更新的请求（preempt_gen 前进）→ 本任务一启动即置 cancel 快速跳过。
        上一任务被抢占后超过 _PREEMPT_GRACE_S 仍不收尾（LLM/TTS hung 等）→ 强制取消，
        槽位必须自愈，否则本槽后续所有请求都静默排队（「点了没反应」）。

        注意：slot["task"] 只在 dispatch 处写入（= 该槽最新受理的 chain）。
        这里绝不能再写——chain 启动晚于 dispatch，旧 chain 后启动会把 task 回写成自己，
        stdin 清理/打断看到的就是已收尾的旧任务，排队中的新任务被孤儿化（2026-07-25
        实测：测试里 asyncio.run 收尾顺手 cancel 孤儿 chain → 偶发丢 final_reply）。
        slot["cancel"] 由这里写（= 本槽当前真正在跑任务的取消闸）：抢占经 gen 代数
        传导，写晚了对齐的是「在跑」语义，不会误伤排队任务。
        """
        if prev is not None and not prev.done():
            t0 = time.monotonic()
            print("[yibao] 新请求排队，等上一任务收尾…", file=sys.stderr)
            try:
                # shield：wait_for 超时不许连带取消 prev，强制取消由我们自己控制
                await asyncio.wait_for(asyncio.shield(prev), timeout=_PREEMPT_GRACE_S)
            except asyncio.TimeoutError:
                print(f"[yibao] 上一任务 {_PREEMPT_GRACE_S:.0f}s 未收尾，强制取消", file=sys.stderr)
                prev.cancel()
                try:
                    await prev
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass  # prev 自身异常/被取消都算已收尾
            print(f"[yibao] 上一任务收尾完成（{time.monotonic() - t0:.1f}s）", file=sys.stderr)
        cancel = asyncio.Event()
        if slot["preempt_gen"] > queued_gen:
            cancel.set()
        slot["cancel"] = cancel
        try:
            await start(cancel)
        except Exception as e:  # 兜底：任务未预期的异常不能毒死槽位
            print(f"[yibao] 任务异常收尾：{type(e).__name__}: {e}", file=sys.stderr)

    # HTTP 面（扩展桥+移动 API）：deps 里的闭包依赖上文 _drive_run 等，故在主循环前才组装启动
    _http_deps = MobileDeps()
    if http_enabled:
        async def _http_save(body: dict) -> tuple[int, dict]:
            def _emit(action, result) -> None:
                ev = Event(kind="action_result", action=action.model_dump(mode="json") if hasattr(action, "model_dump") else action,
                           result=result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
                write_msg({"type": "event", "event": ev.model_dump(mode="json")})
            return await _bridge_save(agent, _emit, body)

        _http_deps.save = _http_save
        _http_deps.submit_run = _submit_run
        _http_deps.interrupt = _interrupt_mobile
        _http_deps.confirm = _confirm_mobile
        _http_deps.state = _mobile_state
        _http_deps.register_push = _register_push
        # 会话只读面（mobile M1）：/v1/conversations、/v1/history 直读 agent.history
        _http_deps.conversations = lambda: _conversations_payload(agent.history)
        _http_deps.history = lambda cid: _history_payload(agent.history, cid)

        # 信息浏览面（mobile M2）：feed 与桌面 IPC 同源；reminders 插件直连；memories 复用 _mem_list
        async def _mem_payload() -> dict:
            return {"ok": True, "items": await _mem_list()}

        _http_deps.feed = _mobile_feed
        _http_deps.reminders_list = lambda: _reminders_list_payload(agent)
        _http_deps.reminders_cancel = lambda rid: _reminders_cancel_payload(agent, rid)
        _http_deps.memories = _mem_payload
        bridge_server = await _start_http_api(agent, settings, tap, _http_deps)

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
        if rtype in ("run", "voice_start"):
            if rtype == "voice_start" and voice is None:
                # 语音不可用（未启用/初始化失败）：不许静默吞掉——前端会永远卡「聆听中」
                rid = msg.get("id")
                print("[yibao] voice_start 收到但语音栈不可用", file=sys.stderr)
                write_msg({"type": "event", "event": {"kind": "error", "text": "语音不可用：麦克风初始化失败或被禁用"}})
                write_msg(_run_done_msg(rid, str(msg.get("conversation_id") or "")))
                continue
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

                print(f"[yibao] run 受理 rid={rid} surface={surface} conv={conversation_id}：{text[:30]!r}", file=sys.stderr)
                _schedule_run(surface, rid, _start, conversation_id)
            elif voice is not None:
                rid = msg.get("id")
                cont = bool(msg.get("continuous"))
                start = lambda c, r=rid, s=surface, ci=conversation_id, ct=cont: _drive_voice_start(r, c, s, ci, ct)
                print(f"[yibao] voice_start 受理 rid={rid} surface={surface} conv={conversation_id} continuous={cont}", file=sys.stderr)
                _schedule_run(surface, rid, start, conversation_id)
            else:
                continue
        elif rtype == "panel_action":
            if _is_readonly_direct(msg, agent):
                # L0 只读直调：独立任务并发跑，不占槽位、不抢占在跑的 run（编辑器/面板加载数据不该踩对话）
                async def _ro(m=msg):
                    try:
                        await handle_panel_action(m, agent, write_msg, run_text=_readonly_no_run)
                    except Exception as e:
                        print(f"[yibao] 只读面板调用异常：{type(e).__name__}: {e}", file=sys.stderr)

                t = asyncio.ensure_future(_ro())
                readonly_tasks.add(t)
                t.add_done_callback(readonly_tasks.discard)
                continue
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
        elif rtype == "interrupt":
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
        elif rtype == "panel_context":
            # 壳上面板焦点变化：存下来，下次 run 注入 LLM 上下文
            _FOCUS["value"] = msg.get("focus")
        elif rtype == "confirm_batch":
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
        elif rtype == "feed":
            # 主屏查询：动态列表（倒序）+ 问候统计（组装在 _mobile_feed，与手机 /v1/feed 同源）
            try:
                limit = int(msg.get("limit") or 60)
            except (TypeError, ValueError):
                limit = 60
            write_msg({"type": "feed", **_mobile_feed(limit)})
        elif rtype == "distill_now":
            # 设置页「立即提炼昨日」：master/distill 任一关闭直接拒绝（零出站）；运行可长达 60s，挪线程池
            if distiller is None or not (settings.get("perception.master") and settings.get("perception.distill")):
                write_msg({"type": "distill_now", "ok": False, "reason": "disabled"})
            else:
                result = await _offload(distiller.run_yesterday, "manual")
                write_msg({"type": "distill_now", "ok": True, "result": result})
        elif rtype == "recap_check":
            # 晨间反刍（fire-and-forget）：闸门→去重→选材→emit reminder(morning_recap)→标记今天
            try:
                import datetime as _dt
                today = _dt.date.today().isoformat()
                decide = _recap_decide(
                    settings=settings,
                    last_recap_day=distiller.store.recap_last_day() if distiller else None,
                    today=today,
                    yesterday_items=(distiller.store.day_items(
                        (_dt.date.today() - _dt.timedelta(days=1)).isoformat())
                        if distiller else []),
                )
                if decide is not None:
                    _emit_event({"kind": "reminder", "type": "morning_recap",
                                 "text": decide["text"], "day": decide["day"]})
                    if distiller is not None:
                        distiller.store.set_recap_day(today)
            except Exception as e:
                print(f"[yibao] recap_check 失败：{e}", file=sys.stderr)
        elif rtype == "distill_timeline":
            # 设置页/回顾视图：近 N 天提炼聚合（distiller 不在则空数组）
            try:
                days = int(msg.get("days") or 14)
                write_msg({"type": "distill_timeline",
                           "days": distiller.store.recent_days(days) if distiller else []})
            except Exception as e:
                print(f"[yibao] distill_timeline 失败：{e}", file=sys.stderr)
                write_msg({"type": "distill_timeline", "days": []})
        elif rtype == "feed_stats":
            # 设置页「主动行为统计」：近 N 天主动行为聚合（默认 7 天）
            try:
                days = int(msg.get("days") or 7)
            except (TypeError, ValueError):
                days = 7
            write_msg({"type": "feed_stats", "stats": feed.stats(days=days)})
        elif rtype == "invoke_context":
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
                    print(f"[yibao] 唤起抓屏失败（已跳过）：{e}", file=sys.stderr)
        elif rtype == "snip_capture":
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
                    print(f"[yibao] 区域截图失败（已跳过）：{e}", file=sys.stderr)
        elif rtype == "vision_query":
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
        elif rtype == "feed_mark_read":
            # 主屏点掉单条：feed.mark_read 容错（坏 id 返回 False，不抛）
            fid = int(msg.get("id", 0))
            ok = feed.mark_read(fid)
            write_msg({"type": "feed_marked_read", "id": fid, "ok": ok})
        elif rtype == "feed_mark_all_read":
            # 主屏「全部已读」：返回受影响行数
            n = feed.mark_all_read()
            write_msg({"type": "feed_all_read", "n": n})
        elif rtype == "feed_mark_status":
            # C 子项目：处置态（follow/ignore/none），与 read 正交
            fid = int(msg.get("id", 0))
            status = str(msg.get("status", "none"))
            ok = feed.set_status(fid, status)
            write_msg({"type": "feed_status_set", "id": fid, "status": status, "ok": ok})
        elif rtype == "feed_feedback":
            # 误报反馈（信任仪表写侧）：👍/👎 落 meta.feedback，同类降频由 dispatcher 执行
            fid = int(msg.get("id", 0))
            ok = feed.set_feedback(fid, str(msg.get("feedback", "none")))
            write_msg({"type": "feed_feedback_set", "id": fid, "ok": ok})
        elif rtype == "widgets":
            # 主屏查询：插件 widget 卡片逐个取数（panel_payload 形状 + open 跳转方法）
            write_msg({"type": "widgets", "widgets": await _collect_widgets()})
        elif rtype == "mem_list":
            # 记忆管理页：全命名空间分组列出 + 记忆后端状态（未就绪/降级时前端给提示）
            mem = agent.memory
            write_msg({"type": "mem_list", "items": await _mem_list(),
                       "ready": bool(getattr(mem, "ready", True)), "failed": bool(getattr(mem, "failed", False))})
        elif rtype == "mem_delete":
            mid = str(msg.get("mem_id") or "")  # 信封 id 字段被请求序号占用，记忆 id 走 mem_id
            try:
                await _offload(agent.memory.delete_by_id, mid)
                write_msg({"type": "mem_deleted", "id": mid, "ok": True})
            except Exception as e:
                write_msg({"type": "mem_deleted", "id": mid, "ok": False, "error": str(e)})
        elif rtype == "mem_edit":
            # 记忆管理「可改」：按 mem_id 替换文本；空文本/坏 id 明确报错，不静默
            mid = str(msg.get("mem_id") or "")
            text = str(msg.get("text") or "").strip()
            if not mid or not text:
                write_msg({"type": "mem_edited", "id": mid, "ok": False,
                           "error": "记忆 id 与新文本都不能为空"})
                continue
            try:
                ok = await _offload(agent.memory.update, mid, text)
                if ok:
                    write_msg({"type": "mem_edited", "id": mid, "ok": True})
                else:
                    write_msg({"type": "mem_edited", "id": mid, "ok": False,
                               "error": "记忆不存在或后端更新失败"})
            except Exception as e:
                write_msg({"type": "mem_edited", "id": mid, "ok": False, "error": str(e)})
        elif rtype == "settings_get":
            write_msg({"type": "settings", "values": {**settings, "watch.status": watch_service.status()}})
        elif rtype == "http_pair_info":
            # 配对信息（手机设置页扫码/手输 URL 用）：内网 IP + 实际监听口/绑定地址
            write_msg({"type": "http_pair_info", "lan_ip": _lan_ip(),
                       "port": http_port(), "bind": str(settings.get("http.bind") or "127.0.0.1")})
        elif rtype == "settings_set":
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
                    print(f"[yibao] 设置保存失败：{e}", file=sys.stderr)
            write_msg({"type": "settings", "values": {**settings, "watch.status": watch_service.status()}})
        elif rtype == "dock_list":
            # 主屏 Dock 查询：pinned 优先 + 频率补齐（详见 _dock_list）
            write_msg({"type": "dock_list", "dock": _dock_list(agent.log, _plugin_summaries_list())})
        elif rtype == "set_dock_pin":
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
                    print(f"[yibao] dock_pinned 写入失败：{e}", file=sys.stderr)
                    ok = False
            write_msg({"type": "dock_pin_set", "pid": pid, "ok": ok,
                       "dock": _dock_list(agent.log, _plugin_summaries_list())})
        elif rtype == "perception_list":
            if pstore is None:
                write_msg({"type": "perception", "items": [], "sources": [], "available": False})
                continue
            try:
                limit = max(1, min(int(msg.get("limit") or 60), 200))
                before_raw = msg.get("before_id")
                before_id = int(before_raw) if before_raw is not None else None
                items = pstore.list(limit=limit, before_id=before_id)
                sources = pstore.sources()
                write_msg({"type": "perception", "items": items, "sources": sources, "available": True})
            except Exception as e:
                write_msg({"type": "perception", "items": [], "sources": [], "available": True, "error": str(e)})
        elif rtype == "perception_delete":
            per_raw = msg.get("per_id")
            if pstore is None or per_raw is None:
                write_msg({"type": "perception_deleted", "id": per_raw, "ok": False, "error": "感知存储不可用"})
                continue
            try:
                per_id = int(per_raw)
                write_msg({"type": "perception_deleted", "id": per_id, "ok": bool(pstore.delete(per_id))})
            except Exception as e:
                write_msg({"type": "perception_deleted", "id": per_raw, "ok": False, "error": str(e)})
        elif rtype == "perception_clear":
            if pstore is None:
                write_msg({"type": "perception_cleared", "count": 0, "error": "感知存储不可用"})
                continue
            try:
                write_msg({"type": "perception_cleared", "count": int(pstore.clear())})
            except Exception as e:
                write_msg({"type": "perception_cleared", "count": 0, "error": str(e)})
        elif rtype == "check_permissions":
            write_msg({"type": "permissions", "permissions": _permissions_status()})
        elif rtype == "prompt_permission":
            which = msg.get("which")
            if which == "ax":
                permissions.prompt_ax()
            elif which == "screen":
                permissions.prompt_screen()
            elif which == "input":
                permissions.prompt_input()
            write_msg({"type": "permissions", "permissions": _permissions_status()})


def _line_reader() -> ReadMsg:
    def _r() -> dict | None:
        line = sys.stdin.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    return _r


def _line_writer() -> WriteMsg:
    lock = threading.Lock()  # pong 由读线程直发，与主循环消息共享 stdout，防行交错

    def _w(msg: dict) -> None:
        with lock:
            sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    return _w


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
        print(f"[yibao] 语音不可用，已禁用：{e}", file=sys.stderr)
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
            print("[yibao] 父进程已退出（ppid 变化），自我了断", file=sys.stderr)
            os._exit(0)


def main() -> int:
    reader, writer = _line_reader(), _line_writer()
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
        print(f"[yibao] 大脑单实例锁获取失败：{e}", file=sys.stderr)
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
