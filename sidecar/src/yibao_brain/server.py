"""stdio 行分隔 JSON 服务：把 AgentLoop 接到桌面壳（Phase B 的 Tauri 侧）。

协议（脑→壳）：hello（启动握手，含权限状态）、pong、permissions、event、run_done、feed（主屏动态+统计）。
协议（壳→脑）：run、confirm、voice_start、interrupt、ping、check_permissions、prompt_permission、panel_context、feed。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from collections.abc import Callable

from . import permissions
from .audit import AuditLog
from .config import a11y_enabled, computer_use_enabled, history_path, llm_api_key, load_settings, perception_db_path, plugin_data_dir, save_settings, screenshot_dir, stt_model_dir, tts_voice, vad_max_seconds, vad_min_silence, vad_model_path, voice_enabled
from .feed import FeedStore
from .history import ConversationHistory
from .ipc import Action, Event, RiskLevel
from .llm import FakeProvider, GLMProvider, ToolCall
from .loop import AgentLoop, _offload
from .memory import FakeMemory, LazyMem0Memory
from .plugins import LlmChat, get_api, get_mem_namespaces, get_plugin_summaries, get_widgets, panel_payload
from .safety import Decision, Gate, GatePolicy, RiskClassifier
from .skills import EchoSkill, SkillRegistry
from .skills_composite import register_composite_skills
from .skills_real import ComputerUseSkill, register_real_skills

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


def _permissions_status() -> dict:
    """检测辅助功能/屏幕录制权限；检测本身失败时乐观返回 True（不出误报 banner）。"""
    try:
        return {"ax": permissions.check_ax(), "screen": permissions.check_screen()}
    except Exception:
        return {"ax": True, "screen": True}


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
            register_real_skills(reg)
            register_composite_skills(reg)
            if llm_api_key() and computer_use_enabled():
                try:
                    from .llm import ComputerUseClient

                    reg.register(ComputerUseSkill(ComputerUseClient()))
                except Exception as e:
                    print(f"[yibao] computer-use 兜底未启用：{e}", file=sys.stderr)

    if provider is not None:
        prov = provider
    else:
        prov = GLMProvider() if (use_real and llm_api_key()) else FakeProvider(text="(未配置 LLM key，使用 fake 回复)")

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
    action = agent.invoker.propose(ToolCall(id=f"pa_refresh_{id(emit)}", skill_id=refresh_tool, params={}))
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

    def emit(event: Event) -> None:
        write_msg({"type": "event", "surface": surface, "event": event.model_dump(mode="json")})

    rid = msg.get("id")
    method = ""
    tag = Action(id=f"pa_{rid}", skill_id="?")  # 错误事件归属标签：壳侧桥按 pa_<rid> 认领，不误杀其他调用
    try:
        method = str(msg.get("method", ""))
        tag = Action(id=f"pa_{rid}", skill_id=method or "?")
        params = msg.get("params") or {}
        api = get_api(method)
        if api is None:  # 白名单外：拒绝执行
            emit(Event(kind="error", text=f"面板方法未在白名单：{method}", action=tag))
            write_msg({"type": "run_done", "id": rid})
            return
        if not api.direct:
            await run_text(_render_intent(api, params), rid)
            return

        action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
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
        result = await _offload(agent.invoker.execute, action, params)  # 与 arun 一致挪线程池
        emit(Event(kind="action_result", action=action, result=result))
        if result.success and api.refresh is not None:
            # 声明式刷新：删除类操作后跟一次查询，面板拿新数据而不是操作回执
            await _emit_refresh_panel(agent, emit, api.refresh)
        else:
            if result.success and api.panel is not None:
                result.panel = api.panel  # method 声明的面板优先于 tool 自带引用（如 webview 编辑器）
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
        ToolCall(id=f"pa_{msg.get('id')}", skill_id=api.handler, params=msg.get("params") or {})
    )
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    return action.risk <= RiskLevel.L0_READONLY


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


# ---------- 自主权旋钮（OS 感 §4.4：主动触达三档；只管触达强度，Feed/历史照落）----------


def _proactive_level(settings: dict) -> str:
    """旋钮当前档位；缺省/非法值按 full（兼容旧行为）。"""
    lv = settings.get("proactive.level", "full")
    return lv if lv in ("quiet", "bubble", "full") else "full"


def _gate_proactive_event(ev: dict, settings: dict) -> dict | None:
    """插件主动事件过旋钮：播报类（kind=reminder）quiet 吞掉、其余档标注 level；
    error 等信任信息不受管辖，原样放行。"""
    if not isinstance(ev, dict) or ev.get("kind") != "reminder":
        return ev
    level = _proactive_level(settings)
    if level == "quiet":
        return None
    return {**ev, "level": level}


async def _dispatch_reminder(r: dict, *, settings: dict, feed, history, voice,
                             run_state: dict, write_msg) -> None:
    """到期提醒分发：Feed/历史照落（可追溯底线）；气泡广播与 TTS 受 proactive.level 管辖。"""
    text = str(r.get("text", ""))
    level = _proactive_level(settings)
    feed.add("reminder", text, {"rid": r.get("id")})  # 主屏动态：quiet 档也照落
    if level != "quiet":
        write_msg({"type": "event", "surface": "pet",
                   "event": {"kind": "reminder", "text": text, "level": level}})
    if history is not None:  # 落历史：用户回「知道了」时大脑有上下文
        try:
            await _offload(history.record_messages,
                           [{"role": "assistant", "content": f"⏰ 到点提醒：{text}"}])
        except Exception:
            pass
    # TTS 只在完整档且「主动开口」开着；有任务在跑不打断在播的语音
    task = run_state["task"]
    if level == "full" and voice is not None and settings.get("proactive_voice", True) \
            and (task is None or task.done()):
        async def _once(t=text):
            yield f"提醒：{t}"
        try:
            write_msg({"type": "event", "surface": "pet", "event": {"kind": "speaking"}})
            await voice.speak_stream(_once(), asyncio.Event())
        except Exception as e:
            print(f"[yibao] 提醒播报失败：{e}", file=sys.stderr)
        write_msg({"type": "event", "surface": "pet", "event": {"kind": "speaking_done"}})


# ---------- 主屏 Dock 组装（OS 感 §5：固定优先 + 频率补齐，上限 5） ----------

# Dock 最大条目数：pinned + 频率补齐合计上限。写入侧（set_dock_pin）也以此 enforce，
# 避免配置层（config.save_settings）散落校验。
_DOCK_MAX = 5


def _dock_list(log, plugins: list[dict]) -> list[dict]:
    """主屏 Dock 列表：pinned 优先（保序、上限 _DOCK_MAX）+ 未固定按调用频次降序补齐；
    全空（无 pinned 且无任何频次）退化为字母序前 _DOCK_MAX。每项带 pinned 标记。

    log: AuditLog 实例（取 plugin_call_counts；None 时按零频次处理）。
    plugins: 已加载插件摘要 [{id, name}, ...]（serve_async 内来自 get_plugin_summaries）。
    已固定但插件已卸载的 id 仍保留（name 退化为 id），让用户能看到并主动取消固定。
    """
    pinned_raw = list(load_settings().get("dock_pinned") or [])
    # enforce 上限（脏数据/旧上限放宽的兜底）：写入侧已拦，这里再防一道展示溢出
    pinned: list[str] = []
    for pid in pinned_raw:
        pid = str(pid).strip()
        if pid and pid not in pinned:  # 去空、去重
            pinned.append(pid)
    pinned = pinned[:_DOCK_MAX]

    try:
        counts = log.plugin_call_counts() if log is not None else {}
    except Exception:
        counts = {}  # audit 读失败不拖垮 dock 组装

    by_id = {p["id"]: p.get("name", p["id"]) for p in plugins}
    dock = [{"id": pid, "name": by_id.get(pid, pid), "pinned": True} for pid in pinned]
    pinned_set = {pid for pid in pinned}

    unpinned = [p for p in plugins if p["id"] not in pinned_set]
    # 频次降序；同频次按 name 升序保稳定（避免依赖 dict 迭代序）
    unpinned.sort(key=lambda p: (-counts.get(p["id"], 0), p.get("name", p["id"])))
    for p in unpinned:
        if len(dock) >= _DOCK_MAX:
            break
        dock.append({"id": p["id"], "name": p.get("name", p["id"]), "pinned": False})

    if not dock and plugins:
        # 全空退化：无固定、无任何频次数据 → 字母序前 _DOCK_MAX（稳定默认展示）
        alpha = sorted(plugins, key=lambda p: p.get("name", p["id"]))[:_DOCK_MAX]
        dock = [{"id": p["id"], "name": p.get("name", p["id"]), "pinned": False}
                for p in alpha]
    return dock


def _plugin_summaries_list() -> list[dict]:
    """get_plugin_summaries → [{id, name}, ...]（_dock_list 与 IPC 共用）。"""
    return [{"id": pid, "name": info.get("name") or pid}
            for pid, info in get_plugin_summaries().items()]


async def serve_async(
    read_msg: ReadMsg,
    write_msg: WriteMsg,
    *,
    use_real: bool = False,
    db_path: str = "audit.db",
    voice=None,
    provider=None,
    skills_factory=None,
    perception_store=None,
    perception_sensors=None,
) -> None:
    """异步控制平面：stdin 读线程 → asyncio.Queue → 分发；支持 interrupt 打断。

    与同步 serve 的关键差异：读消息在独立线程，故生成/TTS 进行中仍能收到 interrupt，
    cancel_event 一键"三连取消"（停 TTS + 终止 LLM 生成 + 清 TTS 队列）。
    新 run 到来会抢占并打断未完成的旧 run。
    """
    ai_loop = asyncio.get_running_loop()
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
    # 多槽不假设并发数：当前单 run 抢占（dict 通常 1 entry），未来多 run 并发这层不用改。
    pending_confirms: dict[str, asyncio.Future] = {}
    early_answers: dict[str, tuple[bool, bool]] = {}
    # preempt_gen：抢占代数。新请求到来即 +1；排队中的任务启动时发现自己落后 →
    # 一启动即置 cancel（快速跳过），保证「只有最新请求真正执行」。
    # surface：最近一次受理请求的窗口（pet=主窗 / 面板 id，dispatch 受理即写入）。
    # 同 surface 新请求抢占；跨 surface 不抢占，排队等对方说完
    # （子 agent 在面板里干活不该被主窗一句话顶掉）。
    run_state: dict = {"task": None, "cancel": None, "preempt_gen": 0, "surface": None}
    # 并发的 L0 只读面板调用（不占槽位）：跟踪起来，stdin 关闭时一起收尾
    readonly_tasks: set[asyncio.Task] = set()

    # 会话内记住的「免确认」技能集合：用户勾选「本会话不再询问」并批准后记入；
    # 只活在内存，大脑重启即失效（C-4：会话级，不落盘）。
    remembered_confirm: set[str] = set()

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
            # 确认等待必须响应抢占/打断：否则新请求 join 一个永不结束的确认 →
            # 派发循环卡死、ping 不应答、看门狗误杀（2026-07-19 复现确认）
            cancel = run_state["cancel"]
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
        return out

    # 主屏 Feed 存储（OS 感 §4.2）：任务播报/提醒触发在此落库，主屏查询时一次拿回。
    # 与审计库同目录；FeedStore 写失败只 print，不拖垮主链路。
    feed = FeedStore(os.path.join(os.path.dirname(db_path), "feed.db"))

    def _on_plugin_event(ev: dict) -> None:
        """插件主动事件：先落 Feed 再转发壳（surface=None 壳侧按 pet 处理）；播报类受旋钮管辖。"""
        try:
            task_meta = ev.get("task") if isinstance(ev, dict) else None
            feed.add("task" if task_meta else "event", str(ev.get("text", "")), task_meta or {})
        except Exception:
            pass
        gated = _gate_proactive_event(ev, settings)
        if gated is not None:
            write_msg({"type": "event", "surface": None, "event": gated})

    # 插件后台线程 → 壳的主动事件通道（如 agents 插件任务完成播报）：
    # 与下面 memory 状态回调同一跨线程先例（call_soon_threadsafe + write_msg）。
    agent = build_loop(
        read_msg, use_real, db_path, provider, skills_factory, confirmer=batch_confirmer,
        emit_event=lambda ev: ai_loop.call_soon_threadsafe(_on_plugin_event, ev),
        feed=feed,
    )
    # 免确认集合接到闸门：命中后 decide 直接 AUTO（连 confirmation_needed 都不发）
    agent.invoker.gate.session_allowed = remembered_confirm

    def _running_tasks(limit: int = 20) -> list[dict]:
        """读取 agents 权威任务表，只返回 Home 需要的 running 摘要。"""
        adb_file = os.path.join(plugin_data_dir("agents"), "data.db")
        if not os.path.exists(adb_file):
            return []
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
            print(f"[yibao] 进行中任务查询失败（已降级为空）：{e}", file=sys.stderr)
            return []

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
        return out

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

    # 用户设置（自主权旋钮等，数据目录 settings.json）：运行期可调，settings_set 即时生效免重启
    settings = load_settings()

    # 感知是增强面：Keychain/SQLite 不可用时保持关闭，绝不降级明文或拖垮大脑。
    pstore = perception_store
    if pstore is None and use_real:
        try:
            from .perception import PerceptionStore

            pstore = PerceptionStore(perception_db_path())
        except Exception as e:
            settings["perception.master"] = False
            print(f"[yibao] 感知不可用（保持关闭）：{e}", file=sys.stderr)

    if pstore is not None:
        from .perception import LoadUserActivitySkill

        # 与 sensors 共用同一 store、与 settings_set 共用同一可变字典，开关即时生效。
        agent.skills.register(LoadUserActivitySkill(pstore, settings))

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
                    from .perception import PerceptionSensors

                    perception_sensors = PerceptionSensors(pstore, settings)
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

    async def _perception_cleanup_loop() -> None:
        while True:
            await asyncio.sleep(3600)
            if pstore is not None:
                try:
                    await _offload(pstore.purge)
                except Exception as e:
                    print(f"[yibao] 感知过期清理失败：{e}", file=sys.stderr)

    perception_cleanup_task = asyncio.ensure_future(_perception_cleanup_loop())

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

    async def _reminder_loop() -> None:
        """主动能力：每 10s 扫到期提醒 → 按自主权档位分发（广播/气泡/语音）。"""
        store = getattr(agent, "reminder_store", None)
        if store is None:
            return
        while True:
            await asyncio.sleep(10)
            try:
                due = await _offload(store.pop_due, time.time())
            except Exception as e:
                print(f"[yibao] 提醒扫描失败：{e}", file=sys.stderr)
                continue
            for r in due:
                print(f"[yibao] 提醒触发 id={r.get('id')}：{str(r.get('text', ''))[:30]!r}", file=sys.stderr)
                await _dispatch_reminder(r, settings=settings, feed=feed, history=agent.history,
                                         voice=voice, run_state=run_state, write_msg=write_msg)

    reminder_task = asyncio.ensure_future(_reminder_loop())

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

    async def _pump_tts(tts_q: asyncio.Queue, cancel: asyncio.Event, surface: str = "pet"):
        if voice is None:
            return
        try:
            await voice.speak_stream(_tts_chunks(tts_q), cancel)
        except asyncio.CancelledError:
            return  # 打断命中合成/播放的正常取消，不是播报失败
        except Exception as e:
            write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"语音播报失败：{e}"}})
            return
        if not cancel.is_set():
            write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking_done"}})

    async def _stream_agent(text: str, rid, cancel: asyncio.Event, surface: str = "pet", emit_done: bool = True):
        t0 = time.monotonic()
        tts_q: asyncio.Queue | None = asyncio.Queue() if voice is not None else None
        tts_task = asyncio.create_task(_pump_tts(tts_q, cancel, surface)) if tts_q is not None else None
        started_speaking = False
        try:
            async for event in agent.arun(text, cancel, surface=surface):
                write_msg({"type": "event", "surface": surface, "event": event.model_dump(mode="json")})
                if (
                    tts_q is not None
                    and event.kind == "final_reply_chunk"
                    and event.text
                ):
                    if not started_speaking:
                        started_speaking = True
                        write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking"}})
                    await tts_q.put(event.text)
        except Exception as e:
            # arun 抛异常（如 provider 400）→ 发 error + 停 TTS，别让前端卡死
            cancel.set()
            write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"大脑出错：{e}"}})
        finally:
            if tts_q is not None:
                await tts_q.put(None)  # 收尾哨兵，唤醒可能在 get() 上等待的 _pump_tts
            if tts_task is not None:
                await tts_task
            if emit_done:  # 连续语音会话里 run_done 由 _drive_voice_start 在会话结束时统一发
                write_msg({"type": "run_done", "id": rid})
            print(f"[yibao] run 完成 rid={rid}（{time.monotonic() - t0:.1f}s）", file=sys.stderr)

    async def _drive_run(text: str, rid, cancel: asyncio.Event, surface: str = "pet"):
        await _stream_agent(text, rid, cancel, surface)

    async def _drive_voice_start(rid, cancel: asyncio.Event, surface: str = "pet", continuous: bool = False):
        # 连续对话（长按团子进入）：答完接着听。退出：退出语 / 连续两次没听清 / 打断。
        if continuous:
            write_msg({"type": "event", "surface": surface, "event": {
                "kind": "notice", "text": _VOICE_SESSION_HINT}})
        empties = 0
        while True:
            write_msg({"type": "event", "surface": surface, "event": {"kind": "listening"}})

            async def _watch_cancel():
                await cancel.wait()
                voice.stop_listen()  # 打断（interrupt）→ 录音循环下一拍退出

            watcher = asyncio.ensure_future(_watch_cancel())
            t0 = time.monotonic()
            try:
                text = await ai_loop.run_in_executor(None, voice.listen)
            except Exception as e:
                write_msg({"type": "event", "surface": surface, "event": {"kind": "error", "text": f"语音识别失败：{e}"}})
                write_msg({"type": "run_done", "id": rid})
                return
            finally:
                watcher.cancel()
            print(f"[yibao] 聆听结束（{time.monotonic() - t0:.1f}s）：{text[:30]!r}", file=sys.stderr)
            if cancel.is_set():  # 聆听被打断：不走 listening_done（避免误进 think 态）
                write_msg({"type": "event", "surface": surface, "event": {"kind": "interrupted"}})
                write_msg({"type": "run_done", "id": rid})
                return
            write_msg({"type": "event", "surface": surface, "event": {"kind": "listening_done", "text": text}})
            if not text:
                if continuous:
                    empties += 1
                    if empties < _VOICE_SESSION_MAX_EMPTY:
                        continue  # 没听清：会话中不打岔，直接再听一轮
                    write_msg({"type": "event", "surface": surface, "event": {
                        "kind": "notice", "text": "一会儿没说话，先退下啦，叫我随时来～"}})
                    write_msg({"type": "run_done", "id": rid})
                    return
                write_msg({"type": "run_done", "id": rid})
                return
            empties = 0
            if continuous and _is_exit_phrase(text):
                # 退出语：固定告别（不过 LLM，确定性收尾）
                write_msg({"type": "event", "surface": surface, "event": {"kind": "final_reply", "text": _VOICE_SESSION_BYE}})
                write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking"}})

                async def _bye():
                    yield _VOICE_SESSION_BYE

                try:
                    await voice.speak_stream(_bye(), cancel)
                except Exception as e:
                    print(f"[yibao] 会话告别播报失败：{e}", file=sys.stderr)
                if not cancel.is_set():
                    write_msg({"type": "event", "surface": surface, "event": {"kind": "speaking_done"}})
                write_msg({"type": "run_done", "id": rid})
                return
            # 连续会话的 run_done 由本函数在会话结束时发（每轮结束就发会让前端以为请求完结）
            await _stream_agent(text, rid, cancel, surface, emit_done=not continuous)
            if not continuous:
                return
            if cancel.is_set():
                write_msg({"type": "run_done", "id": rid})
                return
            # 答完接着听下一轮

    def _preempt_current():
        run_state["preempt_gen"] += 1
        if run_state["cancel"] is not None:
            run_state["cancel"].set()

    def _preempt_if_same_surface(surface: str) -> None:
        """同 surface 新请求 → 抢占在跑任务；跨 surface → 不抢占，走链式排队。

        跨 surface 排队时给新请求的 surface 发个轻提示（notice），
        让用户知道「受理了，在等另一个窗口那轮说完」，而不是点了没反应。

        比较对象是 run_state["surface"] = 最近一次受理的 surface（dispatch 时即写入，
        无「chain 任务还没跑起来」的调度竞态）。取舍：A(pet) 在跑、B(panel) 排队中又来
        C(pet) 时，C 会被判成跨 surface 而排队而非顶掉 A——三消息交替跨窗的极端场景，
        排队自愈、不会卡死，不为它引入 per-surface 代数。
        """
        prev = run_state["task"]
        if prev is None or prev.done():
            return
        if run_state["surface"] == surface:
            _preempt_current()
        else:
            print(f"[yibao] 跨 surface 请求排队（在跑={run_state['surface']}，新={surface}）", file=sys.stderr)
            write_msg({"type": "event", "surface": surface, "event": {
                "kind": "notice", "text": "另一个窗口还在说，等它说完就轮到你…"}})

    async def _chain_start(prev, start, queued_gen: int) -> None:
        """槽位串行：等上一任务收尾再启动；主循环不在这里阻塞（ping 照答，看门狗不误杀）。

        排队期间又来了更新的请求（preempt_gen 前进）→ 本任务一启动即置 cancel 快速跳过。
        上一任务被抢占后超过 _PREEMPT_GRACE_S 仍不收尾（LLM/TTS hung 等）→ 强制取消，
        槽位必须自愈，否则后续所有请求都静默排队（「点了没反应」）。

        注意：run_state["task"] 只在 dispatch 处写入（= 最新受理的 chain）。
        这里绝不能再写——chain 启动晚于 dispatch，旧 chain 后启动会把 task 回写成自己，
        stdin 清理/打断看到的就是已收尾的旧任务，排队中的新任务被孤儿化（2026-07-25
        实测：测试里 asyncio.run 收尾顺手 cancel 孤儿 chain → 偶发丢 final_reply）。
        run_state["cancel"] 由这里写（= 当前真正在跑任务的取消闸）：抢占经 gen 代数
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
        if run_state["preempt_gen"] > queued_gen:
            cancel.set()
        run_state["cancel"] = cancel
        try:
            await start(cancel)
        except Exception as e:  # 兜底：任务未预期的异常不能毒死槽位
            print(f"[yibao] 任务异常收尾：{type(e).__name__}: {e}", file=sys.stderr)

    while True:
        msg = await queue.get()
        if msg is None:
            # stdin 关闭（壳退出）：不再接新活。给在跑任务 5s 自然收尾；
            # 超时说明它卡死了（确认未答/hung）→ 取消 + 2s 清场 → 强 cancel。
            # 不能无限等：否则大脑变孤儿占着 qdrant 锁/麦
            # （2026-07-19 实测孤儿 brain 存活 3 小时，新 brain 被迫记忆降级）。
            task = run_state["task"]
            if task is not None and not task.done():
                done, _ = await asyncio.wait({task}, timeout=5)
                if not done:
                    if run_state["cancel"] is not None:
                        run_state["cancel"].set()
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
                write_msg({"type": "run_done", "id": rid})
                continue
            surface = str(msg.get("surface") or "pet")  # 会话分流：随 run 贯穿事件流与历史
            _preempt_if_same_surface(surface)
            prev = run_state["task"]
            run_state["surface"] = surface  # 受理即记录：下次 dispatch 判断同/跨 surface 无调度竞态
            if rtype == "run":
                text, rid = msg.get("text", ""), msg.get("id")
                start = lambda c, t=text, r=rid, s=surface: _drive_run(t, r, c, s)
                print(f"[yibao] run 受理 rid={rid} surface={surface}：{text[:30]!r}", file=sys.stderr)
            elif voice is not None:
                rid = msg.get("id")
                cont = bool(msg.get("continuous"))
                start = lambda c, r=rid, s=surface, ct=cont: _drive_voice_start(r, c, s, ct)
                print(f"[yibao] voice_start 受理 rid={rid} surface={surface} continuous={cont}", file=sys.stderr)
            else:
                continue
            run_state["task"] = asyncio.ensure_future(
                _chain_start(prev, start, run_state["preempt_gen"])
            )
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
            # 面板写操作/意图方法：与 run 同槽位（同 surface 抢占 / 跨 surface 排队，主循环不阻塞）
            surface = str(msg.get("surface") or "pet")
            _preempt_if_same_surface(surface)
            prev = run_state["task"]
            run_state["surface"] = surface
            start = lambda c, m=msg, s=surface: handle_panel_action(
                m, agent, write_msg, run_text=lambda text, rid: _stream_agent(text, rid, c, s)
            )
            run_state["task"] = asyncio.ensure_future(
                _chain_start(prev, start, run_state["preempt_gen"])
            )
        elif rtype == "interrupt":
            # 用户主动打断：无条件停一切。interrupt 消息不带 surface（壳上只有一个打断入口），
            # 跨 surface 排队中的任务也会被 gen 前进顶掉——取舍：打断就是「全都停」。
            _preempt_current()
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
                fut = pending_confirms.get(cid)
                if fut is not None and not fut.done():
                    fut.set_result((approved, remember))
                else:
                    # confirmer 还没注册（消息先于 run 任务到达）→ 缓存，由 batch_confirmer 兑现
                    early_answers[cid] = (approved, remember)
            write_msg({"type": "confirm_batched", "ok": True})
        elif rtype == "feed":
            # 主屏查询：动态列表（倒序）+ 问候统计
            try:
                limit = int(msg.get("limit") or 60)
            except (TypeError, ValueError):
                limit = 60
            running_tasks = _running_tasks()
            write_msg({
                "type": "feed",
                "items": feed.recent(limit=limit),
                "stats": _feed_stats(running_tasks),
                "running_tasks": running_tasks,
            })
        elif rtype == "feed_mark_read":
            # 主屏点掉单条：feed.mark_read 容错（坏 id 返回 False，不抛）
            fid = int(msg.get("id", 0))
            ok = feed.mark_read(fid)
            write_msg({"type": "feed_marked_read", "id": fid, "ok": ok})
        elif rtype == "feed_mark_all_read":
            # 主屏「全部已读」：返回受影响行数
            n = feed.mark_all_read()
            write_msg({"type": "feed_all_read", "n": n})
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
            write_msg({"type": "settings", "values": dict(settings)})
        elif rtype == "settings_set":
            vals = msg.get("values")
            if isinstance(vals, dict):
                try:
                    accepted = dict(vals)
                    if pstore is None and accepted.get("perception.master"):
                        accepted["perception.master"] = False
                    save_settings(accepted)  # 只落已知键；写盘成功后重读合并
                    settings.update(load_settings())
                except Exception as e:
                    print(f"[yibao] 设置保存失败：{e}", file=sys.stderr)
            write_msg({"type": "settings", "values": dict(settings)})
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
