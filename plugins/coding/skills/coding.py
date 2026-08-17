"""coding 插件 skills：start（建 session + 后台流式）/ stop（race-safe 取消）/ list。

三件事：
1. start_session：纯函数，往 sessions 表插一行 running（测试直接打）。
2. _spawn_stream：起 daemon 线程，线程内开自己的 asyncio loop 跑 ClaudeCodeRunner，
   每条 SDK 事件 normalize 后经 ctx.emit_event → panel_data 推 coding:chat 面板。
3. _stop_session：race-safe 取消——先 db.update(stopped) 再 set cancel（仿 agents.task_stop），
   保证收尾线程的 done/failed 落库不覆盖用户主动停止。

与 agents 插件的关键差异：agents 跑子进程（Popen + proc.wait + 单条 reminder），
coding 在 daemon 线程的 asyncio loop 里跑 SDK runner，按 chunk 流式推 panel_data。
cancel 用 threading.Event——runner 在自己的 loop 里 .is_set() 轮询，跨线程读安全。
ctx.emit_event 本身线程安全（proactive_dispatcher → call_soon_threadsafe），daemon 线程直调。
"""
from __future__ import annotations

import asyncio
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


def _sibling(stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules（仿 agents._sibling）。

    插件加载器按文件路径 import 本模块、名挂在 yibao_plugin_coding_coding；
    兄弟 helper（_runner）不是包内模块，普通 import 拿不到，只能 spec_from_file_location。
    先挂 sys.modules 再 exec：重复触发也拿到同一实例。
    """
    name = f"yibao_plugin_coding_{stem}"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name(f"{stem}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_runner = _sibling("_runner")
ClaudeCodeRunner = _runner.ClaudeCodeRunner   # 生产默认 runner factory（claude-code/cc）
_codex_runner = _sibling("_codex_runner")
CodexCliRunner = _codex_runner.CodexCliRunner  # codex CLI 子进程 runner factory
_PERM = _runner._PERM                         # can_use_tool 裁决注册表（rid → {event, allow}；DecideSkill 消费）


def _runner_for(agent: str):
    """agent id → runner 实例：claude-code/cc → ClaudeCodeRunner；codex → CodexCliRunner；
    未知 → ValueError（调用方转 ActionResult 清晰文案）。"""
    a = str(agent or "claude-code").strip() or "claude-code"
    if a in ("claude-code", "cc"):
        return ClaudeCodeRunner()
    if a == "codex":
        return CodexCliRunner()
    raise ValueError(f"未知智能体：{a}（仅支持 claude-code / codex）")

# codex_reader / _brief / _cc_reader 也是同目录兄弟模块（非包内），经 _sibling 加载（同 _runner）。
# _build_brief / _codex_sessions_root 做模块级间接：测试 monkeypatch 这两个属性即可
# 隔离真实文件系统与 LLM，不污染 yibao_plugin_coding__brief / codex_reader 模块本身。
_codex = _sibling("_codex_reader")
_brief_mod = _sibling("_brief")
_build_brief = _brief_mod.build_brief
_cc_reader = _sibling("_cc_reader")   # _text_of/_MAX_LINE_KB 复用；测试 monkeypatch HOME 改道


def _codex_sessions_root() -> str:
    """Codex session JSONL 根目录（间接层，供测试 monkeypatch 改道到 tmp 目录）。"""
    return os.path.expanduser("~/.codex/sessions")


# sid -> {"cancel": threading.Event}。stop 经此拿 cancel 信号；线程收尾后 pop。
# 跨进程丢失（底座重启）无碍——sessions 表 status 仍 running，但 cancel 信号没了，
# C7 集成验收时再加对账（仿 agents._reconcile_orphans）；v1 不做。
_SESSIONS: dict[str, dict] = {}


def start_session(db, *, agent: str, cwd: str, prompt: str, source: str = "",
                  mode: str = "acceptEdits") -> str:
    """纯函数：往 sessions 表插一行 running，返回 sid。不碰线程/runner（测试可直打）。

    source：会话来源标记——""=用户直起；"codex:<sid>"=从 codex 交接切过来（HandoffSkill 起）。
    透传落库，便于后续面板/审计按来源过滤；不参与 runner 行为。
    mode：权限模式（acceptEdits=自动改文件 / plan=只读规划）落 mode 列，
    后续 send 不带 mode 时沿用库值。
    """
    sid = uuid.uuid4().hex[:12]
    db.insert("sessions", {
        "id": sid, "agent": agent, "cwd": cwd, "prompt": prompt,
        "status": "running", "created_at": int(time.time()), "finished_at": 0,
        "source": source, "mode": mode,
    })
    return sid


class _AsyncShield:
    """把 threading.Event 适配成 runner 期望的 cancel_event（.is_set()）。

    runner 在自己的 asyncio loop 里轮询 .is_set()——threading.Event 的 is_set 是原子读，
    跨线程安全。包一层是为了语义显式（取消信号源是外部 threading.Event，不是 loop 内 asyncio.Event）。
    """
    def __init__(self, ev: threading.Event): self._ev = ev
    def is_set(self) -> bool: return self._ev.is_set()


def _spawn_stream(db, sid: str, cwd: str, prompt: str, runner, emit_event,
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits",
                  agent: str = "claude-code") -> None:
    """起 daemon 线程跑 runner（线程内自带 asyncio loop）。

    emit_event 已线程安全（proactive_dispatcher.emit → call_soon_threadsafe），
    daemon 线程直调即可。db 经参数链一路传到 _stream（落最终状态用）。
    resume_session_id：非 None 时透传 runner.run，续上同一 CC 会话历史（多轮）；
        None（StartSkill 路径）→ 全新会话。
    permission_mode：透传 runner.run（acceptEdits/plan），进 SDK options / codex sandbox。
    agent：会话引擎 id（claude-code/cc/codex），透传 _stream → panel_data data 平级键
        （chat.html 实时更新引擎徽标）。
    _SESSIONS[sid] entry 同时是运行中切模式的通道（coding.mode 写 mode_pending，
    runner 每条消息前消费 → client.set_permission_mode）。
    """
    cancel = threading.Event()
    _SESSIONS[sid] = {"cancel": cancel}

    def _thread():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _stream(db, sid, cwd, prompt, runner, emit_event, cancel,
                        resume_session_id=resume_session_id,
                        permission_mode=permission_mode, agent=agent))
        except Exception as e:  # 兜底：流式线程任何意外都不许炸出来
            print(f"[yibao/coding] session {sid} stream 线程崩：{type(e).__name__}: {e}",
                  file=sys.stderr)
            try:
                db.update("sessions", sid, {"status": "failed", "finished_at": int(time.time())})
            except Exception:
                pass
        finally:
            loop.close()
            _SESSIONS.pop(sid, None)

    threading.Thread(target=_thread, daemon=True, name=f"yibao-coding-{sid}").start()


async def _stream(db, sid: str, cwd: str, prompt: str, runner, emit_event, cancel,
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits",
                  agent: str = "claude-code") -> None:
    """跑 runner；每条事件转 panel_data 推面板 + 落 messages 表；结束按 cancel/error/done 落最终状态。

    transcript 落库：user_msg（带 CC uuid，rewind 锚点）/ text_delta / done·stopped 终态 marker，
    seq 跨轮续号（每轮流式开始时从库里查本 sid 当前 max seq 续起，多轮不交错；
    HistorySkill 按 seq 取最近 40 条）。
    落库 try/except 隔离——transcript 丢失绝不许炸断流式。
    落最终状态前先查当前 status——用户主动 stop 时 _stop_session 已先写 stopped，
    这里保留 stopped 不被 done/failed 覆盖（race-safe，仿 agents._common._wait:66-73）。
    resume_session_id：非 None 时透传 runner.run，续上同一会话历史（多轮；CC=SDK resume，
    codex=exec resume thread_id）。
    permission_mode：透传 runner.run（acceptEdits/plan）。
    agent：会话引擎 id，panel_data data 加平级 "agent" 键（data={session_id, agent, event}，
    chat.html 按此实时更新引擎徽标）。
    session_entry：_SESSIONS[sid] live entry 透传 runner.run——coding.mode 写入
    mode_pending 后，runner 每条消息前消费并 client.set_permission_mode（运行中切模式）。
    can_use_tool：每轮新建权限回调桥（make_permission_callback(sid, on_event, emit_event=…)）——
    SDK 触发权限询问时发 permission_request 进面板流 + confirmation_needed 进 L2 确认体系，
    阻塞等 confirm_batched 路由 / coding.decide 备用通道裁决（双通道幂等，超时默认 deny）。
    """
    state = {"error": False}
    cc_sid: str | None = None   # runner.run 返回值（ResultMessage.session_id）；None=取消/失败
    # seq 跨轮续号：从库里本 sid 当前 max seq 续起（每轮重计会让多轮消息在 ORDER BY seq 下交错）
    try:
        prev = db.query("messages", where={"session_id": sid}, order="seq DESC", limit=1)
        seq = {"n": int(prev[0]["seq"]) if prev else 0}
    except Exception:
        seq = {"n": 0}

    def _persist(role: str, text: str, uuid: str = "") -> None:
        if not text:
            return
        seq["n"] += 1
        try:
            db.insert("messages", {
                "session_id": sid, "role": role, "text": text,
                "ts": int(time.time()), "seq": seq["n"], "uuid": uuid,
            })
        except Exception as e:
            print(f"[yibao/coding] transcript 落库失败（跳过）：{e}", file=sys.stderr)

    def on_event(ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "user_msg":
            _persist("user", str(ev.get("text") or ""), str(ev.get("uuid") or ""))
        elif kind == "text_delta":
            _persist("assistant", str(ev.get("text") or ""))
        elif kind == "done":
            _persist("marker", "完成")
            state["usage"] = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
        elif kind == "stopped":
            _persist("marker", str(ev.get("text") or "已中断"))
        if emit_event is not None:
            # panel/data 必须包在 payload 下：PanelApp.vue 的 panel_data 处理读
            # e.payload?.panel / e.payload?.data，shell proactive→Rust→PanelApp 不再加包装。
            emit_event({"kind": "panel_data",
                        "payload": {"panel": "coding:chat",
                                    "data": {"session_id": sid, "agent": agent, "event": ev}}})
        if ev.get("kind") == "error":
            state["error"] = True

    try:
        cc_sid = await runner.run(prompt, cwd, on_event=on_event, cancel_event=_AsyncShield(cancel),
                                  resume_session_id=resume_session_id,
                                  permission_mode=permission_mode,
                                  can_use_tool=_runner.make_permission_callback(
                                      sid, on_event, emit_event=emit_event),
                                  session_entry=_SESSIONS.get(sid))
    except Exception as e:  # runner 内部应已吞异常→error 事件；框架级异常兜底
        print(f"[yibao/coding] session {sid} runner 框架异常：{type(e).__name__}: {e}",
              file=sys.stderr)
        state["error"] = True

    # 定最终状态：stopped（用户主动停）> error > done
    try:
        prev = db.query("sessions", where={"id": sid})
    except Exception:
        prev = []
    cur = prev[0]["status"] if prev else "running"
    if cur == "stopped":
        final = "stopped"        # 用户已主动停：保留，不被覆盖
    elif cancel.is_set():
        final = "stopped"        # cancel 已 set（_stop_session 先写 stopped 再 set，正常走上一分支；
                                 # 此分支兜底 db.update 失败的极端情形，意图与 stop 一致）
    elif state["error"]:
        final = "failed"
    else:
        final = "done"
    try:
        # cc_session_id 一并落库：即便 final=stopped（用户主动停）也要记录 cc_sid，
        # 后续多轮 resume 仍需它；cc_sid 非空才更新该列——runner 未捕获（取消/失败，
        # 如 codex 静默失败 resume 不存在 thread_id）时保留老行既有值，不抹成 ""
        # （否则后续 send 永远报「cc_session_id 为空」）。CC 路径同款：拿不到也原样不写。
        fields = {"status": final, "finished_at": int(time.time())}
        if cc_sid:
            fields["cc_session_id"] = cc_sid
        db.update("sessions", sid, fields)
    except Exception as e:
        print(f"[yibao/coding] session {sid} 落最终状态失败：{type(e).__name__}: {e}",
              file=sys.stderr)
    _report_final(emit_event, sid, prompt, final, state.get("usage"))


def _usage_suffix(usage) -> str:
    """done 事件 usage → 成本摘要（如「（耗时 12s · $0.0312 · 1540 tok）」）；无数据 → ""。"""
    if not isinstance(usage, dict) or not usage:
        return ""
    parts: list[str] = []
    ms = usage.get("duration_ms")
    if isinstance(ms, (int, float)) and ms > 0:
        parts.append(f"耗时 {ms / 1000:.0f}s")
    cost = usage.get("cost_usd")
    if isinstance(cost, (int, float)) and cost > 0:
        parts.append(f"${cost:.4f}")
    tokens = sum(int(usage[k]) for k in ("input_tokens", "output_tokens")
                 if isinstance(usage.get(k), (int, float)))
    if tokens:
        parts.append(f"{tokens} tok")
    return f"（{' · '.join(parts)}）" if parts else ""


def _report_final(emit_event, sid: str, prompt: str, final: str, usage) -> None:
    """会话终态汇报（P2 督导）：done/failed → reminder（宠物气泡 + Feed 任务卡）；
    stopped → event（仅 Feed 任务卡，不弹气泡防打扰）。task meta 供 Feed 任务卡与
    后续点击路由（plugin:"coding"）；status 发英文键（done/failed/stopped——HomeFeed
    徽章/图标/tag CSS 只认英文，对齐 agents 先例），中文文案在 text；done 的 text
    带成本摘要（usage 不落库，只此一播）。"""
    if emit_event is None:
        return
    try:
        label = prompt[:30]
        if final == "done":
            kind, text = "reminder", f"✅ 编码任务完成：{label}{_usage_suffix(usage)}"
        elif final == "failed":
            kind, text = "reminder", f"❌ 编码任务失败：{label}"
        else:
            kind, text = "event", f"⏹ 编码任务已停止：{label}"
        emit_event({"kind": kind, "text": text,
                    "task": {"id": sid, "status": final, "label": label,
                             "prompt": prompt, "plugin": "coding"},
                    "plugin": "coding"})
    except Exception as e:  # 汇报是增强面：失败只 print，绝不拖垮收尾
        print(f"[yibao/coding] session {sid} 终态汇报失败（跳过）：{e}", file=sys.stderr)


def _stop_session(db, registry, sid: str) -> bool:
    """race-safe 取消：先 db.update(stopped)，再 set cancel（仿 agents.task_stop:343-345）。

    先落 stopped：收尾线程（_stream 末尾的落库）读到 stopped 会保留它；
    反过来先 set cancel，runner 早退 → _stream 可能在本 update 前就把状态翻成 done/stopped
    （竞态）。registry 鸭式：生产 _SESSIONS（dict sid->{"cancel":Event}）或测试替身
    （.s 属性 / .get 方法，条目 {"cancel":Event} 或 {"cancelled":bool}）。
    """
    db.update("sessions", sid, {"status": "stopped", "finished_at": int(time.time())})
    entry = None
    if hasattr(registry, "s"):           # 仿 agents 测试替身形态 + 本插件生产 _SESSIONS 形态
        entry = registry.s.get(sid)
    if entry is None and hasattr(registry, "get"):
        entry = registry.get(sid)
    if entry is None:
        return False
    cancel = entry.get("cancel") if isinstance(entry, dict) else getattr(entry, "cancel", None)
    if cancel is not None and hasattr(cancel, "set"):
        cancel.set()                     # 生产：threading.Event
    elif isinstance(entry, dict):
        entry["cancelled"] = True        # 测试替身：{"cancelled": False}
    else:
        try:
            entry.cancelled = True
        except Exception:
            pass
    # 放行挂起的权限等待（deny 收场）：否则 cancel 要等权限 60s 超时才被消费，停止最长延迟 60s
    _runner.release_pending_permissions(sid)
    return True


class StartSkill(Skill):
    id = "coding.start"
    label = "开始编码会话"
    description = (
        "开始一个 coding 会话：选项目目录 + 引擎（Claude Code / Codex），提交任务后台流式跑，"
        "面板实时回显文本/文件改动。立即返回，完成主动推 panel_data。"
        "【需要】cwd（用户显式选的工作目录）、prompt（任务描述）。"
    )
    default_risk = RiskLevel.L1_LOW   # 会话启动/续聊本身不执行高危动作；文件改动由 SDK acceptEdits 管理

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cwd": {"type": "string", "description": "工作目录（用户显式选）"},
                        "prompt": {"type": "string", "description": "任务描述"},
                        "agent": {"type": "string", "description": "智能体引擎：claude-code（默认）/ codex"},
                        "source": {"type": "string", "description": "会话来源（可选）：用户直起留空；交接路径传 'codex:<sid>'"},
                        "background": {"type": "boolean", "description": "后台执行（可选）：true 时不打开编码面板，静默执行，完成后任务卡汇报；适合后台/并行编码任务"},
                    },
                    "required": ["cwd", "prompt"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cwd = str(params.get("cwd") or "").strip()
        if not cwd:
            return ActionResult(success=False, error="缺少工作目录 cwd（用户需显式选）")
        cwd = os.path.expanduser(cwd)  # 展开 ~（SDK/CLI 不自动展开，否则 ~/Work/x 字面找名为 ~ 的目录）
        if not os.path.isdir(cwd):
            return ActionResult(success=False, error=f"项目目录不存在：{cwd}")
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            return ActionResult(success=False, error="缺少任务描述 prompt")
        agent = str(params.get("agent") or "claude-code").strip() or "claude-code"
        source = str(params.get("source") or "").strip()
        mode = str(params.get("mode") or "acceptEdits").strip() or "acceptEdits"
        background = bool(params.get("background"))
        try:
            runner = _runner_for(agent)
        except ValueError as e:
            return ActionResult(success=False, error=str(e))
        sid = start_session(ctx.db, agent=agent, cwd=cwd, prompt=prompt, source=source, mode=mode)
        # 生产默认 runner（_runner_for 按 agent 选）；测试经 monkeypatch _spawn_stream 不真起线程
        # resume_session_id 不传 → None → 全新会话（首条消息）
        _spawn_stream(ctx.db, sid, cwd, prompt, runner, ctx.emit_event,
                      permission_mode=mode, agent=agent)
        return ActionResult(success=True, data={
            "session_id": sid,
            # background=true → panel=None（loop 判空不开面板），静默执行靠终态任务卡汇报
            "panel": None if background else "coding:chat",
            "human": (f"已开始后台编码会话 {sid}，完成会汇报" if background
                      else f"已开始编码会话 {sid}，面板实时回显"),
        })


class SendSkill(Skill):
    id = "coding.send"
    label = "接续编码会话"
    description = (
        "向既有 coding 会话追加一条消息（多轮）：按会话引擎用 cc_session_id resume 同一会话历史"
        "（Claude Code 走 SDK resume；Codex 走 codex exec resume thread_id），"
        "继续在同一上下文里干活，面板实时回显。立即返回，完成主动推 panel_data。"
        "【需要】id（coding.start 返回的 session_id）、prompt（本轮任务描述）。"
    )
    default_risk = RiskLevel.L1_LOW   # 会话启动/续聊本身不执行高危动作；文件改动由 SDK acceptEdits 管理
    refresh = "coding.list"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "会话 id（coding.start 返回的 session_id）"},
                        "prompt": {"type": "string", "description": "本轮任务描述"},
                    },
                    "required": ["id", "prompt"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        if not sid:
            return ActionResult(success=False, error="缺少会话 id")
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            return ActionResult(success=False, error="缺少任务描述 prompt")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        row = rows[0]
        if row.get("status") == "running":
            return ActionResult(success=False, error="会话正在运行中，请先中断或等待完成")
        if sid in _SESSIONS:  # check-then-act 缝：stop 落 stopped 但 runner 线程未退（长工具中）→ 同样拒
            return ActionResult(success=False, error="会话正在收尾中，请稍候")
        cc = row.get("cc_session_id") or ""
        if not cc:
            return ActionResult(
                success=False,
                error="该会话尚未建立上下文（cc_session_id 为空），请先用首条消息开始",
            )
        cwd = row.get("cwd") or ""
        # mode 跨轮沿用：send 不带 mode → 用库值（start/coding.mode 落的）；带 → 覆盖回写
        mode = str(params.get("mode") or row.get("mode") or "acceptEdits")
        agent = str(row.get("agent") or "claude-code")   # 按会话落库引擎选驱动（codex 行走 exec resume）
        try:
            runner = _runner_for(agent)
        except ValueError as e:
            return ActionResult(success=False, error=str(e))
        # 重置 running 状态：resume 是新一轮流式，finished_at 归零；mode 一并回写
        ctx.db.update("sessions", sid, {"status": "running", "finished_at": 0, "mode": mode})
        _spawn_stream(ctx.db, sid, cwd, prompt, runner, ctx.emit_event,
                      resume_session_id=cc, permission_mode=mode, agent=agent)
        return ActionResult(success=True, data={
            "session_id": sid,
            "panel": "coding:chat",
            "human": f"已接续会话 {sid}，面板实时回显",
        })


class StopSkill(Skill):
    id = "coding.stop"
    label = "停止编码会话"
    description = "停止一个还在运行的 coding 会话（race-safe 取消：先落 stopped 再发取消信号）。"
    default_risk = RiskLevel.L0_READONLY  # 只停，不改文件
    refresh = "coding.list"

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "会话 id（coding.start 返回的 session_id）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        if not sid:
            return ActionResult(success=False, error="缺少会话 id")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        if rows[0].get("status") not in ("running",):
            return ActionResult(success=False, error=f"会话已结束（{rows[0].get('status')}），无需停止")
        ok = _stop_session(ctx.db, _SESSIONS, sid)
        if not ok:
            # 无 live runner（陈旧 running，如底座重启 mid-run）：db 已落 stopped——
            # 补发终态事件让面板复位，否则发送键永久锁死
            emit = getattr(ctx, "emit_event", None)
            if emit is not None:
                emit({"kind": "panel_data",
                      "payload": {"panel": "coding:chat",
                                  "data": {"session_id": sid,
                                           "event": {"kind": "stopped", "text": "已中断"}}}})
        return ActionResult(success=True, data={
            "id": sid,
            "human": f"已停止会话 {sid}",
        })


def _live_state(sid: str) -> str:
    """会话活体状态：waiting（_PERM 有该 sid 挂起审批）> running（_SESSIONS 有 live entry）> idle。"""
    prefix = f"perm_{sid}_"
    for rid, entry in list(_PERM.items()):
        if rid.startswith(prefix) and entry.get("allow") is None:
            return "waiting"
    if sid in _SESSIONS:
        return "running"
    return "idle"


class ListSkill(Skill):
    id = "coding.list"
    label = "编码会话列表"
    description = "列出 coding 会话（按创建时间倒序；每行带 live 活体状态：waiting/running/idle）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        rows = ctx.db.query("sessions", order="created_at DESC")
        sessions = [{**dict(row), "live": _live_state(str(row.get("id") or ""))} for row in rows]
        return ActionResult(success=True, data={"sessions": sessions, "panel": "coding:chat"})


class AttachSkill(Skill):
    """打开 coding 面板并恢复指定会话（任务卡/会话墙「接管」点击路由）。

    只校验会话存在，真正的恢复在面板侧：api.toml 声明 panel="coding:chat"，
    直调成功后 panel_payload 把 data 原样透传进面板 init 数据（{session_id, agent, attach: true}），
    chat.html 的 handleInitData 见 attach 标志自动 resumeSession（P1 接管链路自然生效）；
    data.agent 让前端接管跨引擎会话时徽标立即正确，不用等首条流事件。
    """
    id = "coding.attach"
    label = "接管编码会话"
    description = (
        "打开 coding 面板并恢复指定会话的上下文（点击 Feed 任务卡/会话墙「接管」的路由）。"
        "【需要】session_id（coding.start 返回的会话 id）。"
    )
    default_risk = RiskLevel.L0_READONLY  # 只校验存在 + 打开面板，不改任何状态

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"session_id": {"type": "string", "description": "会话 id（coding.start 返回的 session_id）"}},
                    "required": ["session_id"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("session_id") or "").strip()
        if not sid:
            return ActionResult(success=False, error="缺少会话 session_id")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        # attach 标志逐字对齐 chat.html handleInitData 的判别（data.attach === true）；
        # agent 取库行值（老行缺省按 claude-code，同 _stream/_runner_for 缺省）——
        # 前端接管跨引擎会话时引擎徽标立即正确，不等首条流事件
        return ActionResult(success=True, data={
            "session_id": sid,
            "agent": str(rows[0].get("agent") or "claude-code"),
            "attach": True,
            "human": f"已打开编码会话 {sid}",
        })


_LIVE_TEXT = {"waiting": "等待审批", "running": "运行中", "idle": "空闲"}


def _rel_time(now: int, ts: int) -> str:
    """unix 秒 → 中文相对时间（会话墙副标题）：<1 分钟 刚刚；<1 小时 N 分钟前；<1 天 N 小时前；否则 N 天前。"""
    d = max(0, now - ts)
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{d // 60} 分钟前"
    if d < 86400:
        return f"{d // 3600} 小时前"
    return f"{d // 86400} 天前"


class WallDataSkill(Skill):
    """会话墙数据（coding:wall 面板的 list schema 数据源）。

    每会话一张卡：title=「{cwd basename} · {prompt 前 20 字}」，subtitle=「{live 文案} · {相对时间}」，
    按 created_at DESC。相对时间一律取 created_at（与排序同基准；v1 不跟 finished_at）。
    行内行动作在 wall.schema.json 静态声明：「接管」coding.attach 恒显示；「停止」coding.wall_stop
    同显——schema list 不支持按行条件显隐，idle 会话点停止由 coding.stop 返回清晰提示兜底。
    result.panel 直接置 coding:wall：coding.wall_stop 的 refresh 通道（server._emit_refresh_panel）
    走 invoker 直执行本 skill，panel_payload 只认 result.panel（api.toml 的 panel 覆盖管不到那条路）。
    """
    id = "coding.wall_data"
    label = "编码会话墙"
    description = "列出全部 coding 会话的总览卡片（会话墙）：目录·任务摘要 + 活体状态（等待审批/运行中/空闲）·相对时间，按创建时间倒序。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        rows = ctx.db.query("sessions", order="created_at DESC")
        now = int(time.time())
        items: list[dict] = []
        for row in rows:
            sid = str(row.get("id") or "")
            live = _live_state(sid)
            cwd = str(row.get("cwd") or "")
            base = (os.path.basename(os.path.normpath(cwd)) or cwd) if cwd else "?"
            prompt = str(row.get("prompt") or "")[:20]
            title = f"{base} · {prompt}" if prompt else base
            created = int(row.get("created_at") or 0)
            engine = "Codex" if str(row.get("agent") or "") == "codex" else "CC"   # 引擎徽标前缀（无 agent 的老行按 CC）
            subtitle = f"{engine} · {_LIVE_TEXT[live]} · {_rel_time(now, created)}"
            items.append({"id": sid, "live": live, "title": title, "subtitle": subtitle})
        return ActionResult(success=True, data={"rows": items}, panel="coding:wall")


class HandoffListSkill(Skill):
    """列指定项目下 Codex 的会话（跨 agent 交接入口；只读）。

    经模块级 `_codex_sessions_root()` 取 session 根 → `_codex.list_sessions(cwd)`，
    返回按时间倒序的 session 列表（含 session_id / path / first_line）。L0 只读。
    """
    id = "coding.handoff_list"
    label = "Codex 会话列表"
    description = (
        "列出指定项目目录下 Codex 的会话记录（按时间倒序），用于跨 agent 交接。"
        "返回 {sessions: [{session_id, cwd, timestamp, path, first_line}]}。"
        "【需要】cwd（项目目录）。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cwd": {"type": "string", "description": "项目目录（用户显式选）"},
                    },
                    "required": ["cwd"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cwd = str(params.get("cwd") or "").strip()
        if not cwd:
            return ActionResult(success=False, error="缺少工作目录 cwd（用户需显式选）")
        try:
            sessions = _codex.list_sessions(cwd, root=_codex_sessions_root())
        except Exception as e:
            return ActionResult(success=False, error=f"读取 Codex 会话失败：{type(e).__name__}: {e}")
        return ActionResult(success=True, data={"sessions": sessions})


class HandoffBriefSkill(Skill):
    """针对某条 Codex 会话生成交接 Brief（LLM 凝练 → Claude Code 接续上下文）。

    流程：list_sessions 匹配 session_id → read_conversation + git_summary →
    `_build_brief(ctx.llm, turns, git)`。brief=None（LLM 失败）仍 success=True，
    data.brief=None → 前端走手动粘贴兜底。L0 只读。
    """
    id = "coding.handoff_brief"
    label = "生成交接 Brief"
    description = (
        "针对某条 Codex 会话生成「交接 Brief」（任务/已完成/卡点/下一步），"
        "供 Claude Code 接续上下文。返回 {brief, session_id, incomplete}；"
        "brief 为 None 表示 LLM 生成失败，前端走手动粘贴兜底。"
        "【需要】session_id（Codex 会话 id）、cwd（项目目录）。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Codex 会话 id"},
                        "cwd": {"type": "string", "description": "项目目录（用户显式选）"},
                    },
                    "required": ["session_id", "cwd"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("session_id") or "").strip()
        if not sid:
            return ActionResult(success=False, error="缺少 session_id")
        cwd = str(params.get("cwd") or "").strip()
        if not cwd:
            return ActionResult(success=False, error="缺少工作目录 cwd（用户需显式选）")
        # 找 session path：经 list_sessions（已按 cwd 过滤 + 排序）；命中第一条同 sid 的
        try:
            sessions = _codex.list_sessions(cwd, root=_codex_sessions_root())
        except Exception as e:
            return ActionResult(success=False, error=f"读取 Codex 会话失败：{type(e).__name__}: {e}")
        match = next((s for s in sessions if s.get("session_id") == sid), None)
        if match is None:
            return ActionResult(success=False, error=f"未找到 session：{sid}")
        # llm capability 守卫：H3 后 manifest 已声明，正常 ctx.llm 非 None；防御性检查
        if getattr(ctx, "llm", None) is None:
            return ActionResult(success=False, error="未声明 llm capability（无法生成 Brief）")
        try:
            conv = _codex.read_conversation(match["path"])
            git = _codex.git_summary(cwd)
        except Exception as e:
            return ActionResult(success=False, error=f"读取会话内容失败：{type(e).__name__}: {e}")
        # brief 可能 None（provider 失败 / 空响应）→ 仍 success=True，前端走手动粘贴兜底
        brief = _build_brief(ctx.llm, conv["turns"], git)
        return ActionResult(success=True, data={
            "brief": brief,
            "session_id": sid,
            "incomplete": conv["incomplete"],
        })


class SessionBriefSkill(Skill):
    """针对会话库里的任一会话生成交接 Brief（引擎 chip 跨引擎切换：摘要移植到另一引擎）。

    与 handoff_brief（读 codex rollout 文件，单向 Codex→CC）不同：本 skill 读插件 messages 表
    （CC/Codex 会话双向通用），源引擎取 sessions 行 agent，目标引擎由 target 参数给出
    （缺省取源的另一端）。LLM 未声明/生成失败时退化为最近消息原文节选——前端恒有可用
    交接上下文，不被 LLM 故障挡路。L0 只读。
    """

    id = "coding.session_brief"
    label = "生成会话交接 Brief"
    description = (
        "针对会话库里的任一会话生成「交接 Brief」（任务/已完成/卡点/下一步），"
        "供另一个 coding 引擎接续上下文（chip 跨引擎切换用，双向通用）。"
        "返回 {brief, session_id}；LLM 失败时 brief 为最近消息原文节选。"
        "【需要】id（会话 id）。【可选】target（目标引擎 codex/claude-code，缺省取源引擎的另一端）。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "会话 id"},
                        "target": {"type": "string", "description": "目标引擎 codex/claude-code（缺省取源的另一端）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        if not sid:
            return ActionResult(success=False, error="缺少会话 id")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        row = rows[0]
        src = "Codex" if str(row.get("agent") or "") == "codex" else "Claude Code"
        target = str(params.get("target") or "").strip()
        dst = ("Codex" if target == "codex" else "Claude Code") if target \
            else ("Claude Code" if src == "Codex" else "Codex")
        # 同 HistorySkill 的取尾窗口：seq DESC LIMIT 40 再反转回正序
        msgs = ctx.db.query("messages", where={"session_id": sid}, order="seq DESC", limit=40)
        msgs.reverse()
        turns = [{"role": m["role"], "text": m["text"]} for m in msgs]
        brief = None
        if getattr(ctx, "llm", None) is not None and turns:
            cwd = str(row.get("cwd") or "")
            try:
                git = _codex.git_summary(cwd) if cwd else ""
            except Exception:
                git = ""   # git 摘要失败不挡路（非 git 目录等），brief 仍有对话内容
            brief = _build_brief(ctx.llm, turns, git, src, dst)
        if not brief:
            # 兜底：LLM 未配置/失败/空历史 → 原文节选，前端恒有交接上下文可用
            excerpt = "\n".join(f"{t['role']}: {str(t['text'])[:500]}" for t in turns[-10:]) or "（无历史消息）"
            brief = f"（摘要生成失败，以下为最近对话节选）\n{excerpt}"
        return ActionResult(success=True, data={"brief": brief, "session_id": sid})


class HistorySkill(Skill):
    """读某个 coding 会话的信息与最近消息（历史抽屉恢复旧会话用）。

    消息优先读本插件 messages 表（_stream 流式落库的 transcript，取最近 40 条——
    seq DESC LIMIT 40 再反转回正序，含 user 消息的 CC uuid 供 rewind 用）；库里空才 fallback `_sibling("_cc_reader")`
    读 Claude Code 本地 transcript（老会话没有落库数据）。都读不到 messages 静默为空——
    恢复不了就当新会话，不报错。L0 只读。
    """
    id = "coding.history"
    label = "读取会话历史"
    description = "读取某个 coding 会话的信息与最近消息（恢复旧会话用）：优先读插件 messages 表，空则回退 Claude Code 本地 transcript，失败静默为空。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "string", "description": "会话 id"}},
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        row = rows[0]
        cc = row.get("cc_session_id") or ""
        # 取最近 40 条：seq DESC LIMIT 40 拿尾部，再反转回时间正序（ASC LIMIT 会拿到会话头部）
        rows = ctx.db.query("messages", where={"session_id": sid}, order="seq DESC", limit=40)
        if rows:
            rows.reverse()
            messages = [{"role": r["role"], "text": r["text"], "uuid": r.get("uuid") or ""}
                        for r in rows]
        else:
            reader = _sibling("_cc_reader")
            messages = reader.read_transcript(cc, limit=40) if cc else []
        return ActionResult(success=True, data={
            "session_id": sid, "cwd": row.get("cwd") or "", "cc_session_id": cc,
            "prompt": row.get("prompt") or "", "messages": messages,
        })


class ModeSkill(Skill):
    id = "coding.mode"
    label = "切换权限模式"
    description = "切换某个 coding 会话的权限模式（acceptEdits=自动改文件 / plan=只读规划）：落库下轮生效；会话在跑则通知 runner 运行中切换。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"id": {"type": "string"}, "mode": {"type": "string"}},
                    "required": ["id", "mode"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        mode = str(params.get("mode") or "").strip()
        if mode not in ("acceptEdits", "plan"):
            return ActionResult(success=False, error=f"不支持的模式：{mode}（仅 acceptEdits/plan）")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        ctx.db.update("sessions", sid, {"mode": mode})
        entry = _SESSIONS.get(sid)   # .get 防御 KeyError 缝：stop/收尾线程 pop entry 与 check-then-act 竞态（T3 评审）
        live = entry is not None
        if live:
            entry["mode_pending"] = mode
        return ActionResult(success=True, data={"ok": True, "mode": mode, "live": live})


def _rewind_fresh_client(cc_sid: str, cwd: str, uuid: str) -> None:
    """非 live 路径：新开 client（resume + checkpointing on）执行 rewind_files。CLI 侧 checkpoint 持久，跨实例应可；
    失败抛给调用方（RewindSkill 降级成 error 事件）。"""
    runner = ClaudeCodeRunner()
    factory = runner._default_factory  # 复用 options（checkpointing/replay 已开）

    async def _do() -> None:
        client = factory(cwd, ["Read", "Write", "Edit", "MultiEdit", "Bash", "Glob", "Grep"], resume=cc_sid)
        await client.connect()
        try:
            await client.rewind_files(uuid)
        finally:
            await client.disconnect()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
    finally:
        loop.close()


class RewindSkill(Skill):
    id = "coding.rewind"
    label = "回滚文件检查点"
    description = "把会话改过的文件回滚到某条用户消息时的状态（Claude Code 文件检查点）。会话在跑则下条消息前执行；已结束则新开 client 执行。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"id": {"type": "string"}, "user_msg_id": {"type": "string"}},
                    "required": ["id", "user_msg_id"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        sid = str(params.get("id") or "").strip()
        uuid = str(params.get("user_msg_id") or "").strip()
        if not uuid:
            return ActionResult(success=False, error="缺少回滚锚点 user_msg_id")
        rows = ctx.db.query("sessions", where={"id": sid})
        if not rows:
            return ActionResult(success=False, error=f"会话不存在：{sid}")
        row = rows[0]
        agent = str(row.get("agent") or "claude-code")
        if agent not in ("claude-code", "cc"):   # 文件检查点是 CC 能力；codex 会话无锚点可回滚
            return ActionResult(success=False, error="⏪ 回滚仅支持 Claude Code 会话")
        emit = getattr(ctx, "emit_event", None)

        def _emit(ev: dict) -> None:
            if emit is not None:
                emit({"kind": "panel_data", "payload": {"panel": "coding:chat",
                      "data": {"session_id": sid, "event": ev}}})

        # 在跑：下条消息前由 runner 执行（消费 rewind_pending）。
        # .get 防御 KeyError 缝：stop/收尾线程 pop entry 与 check-then-act 竞态（T3 评审 mode_pending 同款）
        entry = _SESSIONS.get(sid)
        live = entry is not None
        if live:
            entry["rewind_pending"] = uuid
            return ActionResult(success=True, data={"ok": True, "live": True})
        cc = row.get("cc_session_id") or ""
        if not cc:
            return ActionResult(success=False, error="该会话无检查点可回滚（cc_session_id 为空）")
        try:
            _rewind_fresh_client(cc, row.get("cwd") or "", uuid)
        except Exception as e:
            _emit({"kind": "error", "text": f"回滚失败：{e}"})
            return ActionResult(success=False, error=f"回滚失败：{e}")
        _emit({"kind": "rewind_ok", "text": "已回滚到此前的文件状态"})
        return ActionResult(success=True, data={"ok": True, "live": False})


class DecideSkill(Skill):
    id = "coding.decide"
    label = "裁决工具权限"
    description = "对 can_use_tool 弹出的权限请求做允许/拒绝裁决（rid 来自 permission_request 事件）。"
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"rid": {"type": "string"}, "allow": {"type": "boolean"}},
                    "required": ["rid", "allow"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        rid = str(params.get("rid") or "").strip()
        allow = bool(params.get("allow"))
        entry = _PERM.get(rid)
        if entry is None:
            return ActionResult(success=False, error="权限请求不存在或已超时")
        entry["allow"] = allow
        entry["event"].set()
        return ActionResult(success=True, data={"ok": True})


_FILES_EXCLUDE = {".git", "node_modules", "dist", "target", ".venv", "build", "out", "__pycache__", ".next", ".cache"}


class FilesSkill(Skill):
    id = "coding.files"
    label = "项目文件模糊搜索"
    description = "在 cwd 下按文件名模糊匹配（@ 补全用）：限深 6 层、限 200 条、排除依赖/构建目录。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"cwd": {"type": "string"}, "q": {"type": "string"}},
                    "required": ["cwd"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cwd = os.path.expanduser(str(params.get("cwd") or "").strip())
        q = str(params.get("q") or "").strip().lower()
        out: list[dict] = []
        if not os.path.isdir(cwd):
            return ActionResult(success=True, data={"files": []})
        try:
            for root, dirs, files in os.walk(cwd):
                rel_root = os.path.relpath(root, cwd)
                depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
                dirs[:] = [d for d in dirs if d not in _FILES_EXCLUDE and not d.startswith(".")]
                if depth >= 6:
                    dirs[:] = []
                for name in files:
                    if name.startswith("."):
                        continue
                    rel = name if rel_root == "." else f"{rel_root}/{name}"
                    if q and q not in rel.lower():
                        continue
                    out.append({"path": os.path.join(root, name), "rel": rel})
                    if len(out) >= 200:
                        return ActionResult(success=True, data={"files": out})
        except Exception as e:
            print(f"[yibao/coding] files 遍历失败（截断返回）：{e}", file=sys.stderr)
        return ActionResult(success=True, data={"files": out})


# ---------- 统一接续 popover（C1）：跨源上次会话检测 + DB 外 cc 会话导入 ----------


def _cc_projects_base() -> Path:
    """CC transcript 根目录 ~/.claude/projects（测试 monkeypatch HOME 改道 tmp，同 _cc_reader）。"""
    return Path(os.path.expanduser("~/.claude/projects"))


def _cc_project_dir(cwd: str) -> Path:
    """cwd → CC 项目目录 ~/.claude/projects/<slug>。

    slug 规则：非字母数字/连字符一律转 -（实测 /Users/denny/.codex → -Users-denny--codex，
    即 / 与 . 都转 -）。对不上就当无记录（上游 None 兜底，不报错）。
    """
    return _cc_projects_base() / re.sub(r"[^A-Za-z0-9-]", "-", cwd)


def _iso_ts(value) -> int | None:
    """ISO 8601 时间串（CC/codex transcript 行 timestamp）→ unix 秒；解析失败 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except (ValueError, OSError, OverflowError):
        return None


def _cc_transcript_rows(path: Path) -> list[dict]:
    """解析 CC transcript → [{role, text, uuid, ts}]（有文本的 user/assistant 行，时间正序）。

    防御语义同 _cc_reader.read_transcript：坏行跳过、任何失败 → []（检测/导入都以空降级，绝不抛）。
    """
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line[: _cc_reader._MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                role = row.get("type")
                if role not in ("user", "assistant"):
                    continue
                if row.get("isMeta"):  # 机器行（local-command-caveat 等）非用户内容，摘要/导入/计数一律跳过
                    continue
                text = _cc_reader._text_of(row.get("message"))
                if not text:
                    continue
                if text.startswith(("<local-command", "<command-")):  # 本地命令回声块（无 isMeta 标记的）
                    continue
                rows.append({"role": role, "text": text,
                             "uuid": str(row.get("uuid") or ""),
                             "ts": _iso_ts(row.get("timestamp"))})
    except Exception:
        return []
    return rows


# codex rollout 里 CLI 注入的机器条目（环境上下文/指令/插件推荐/中断标记等），非用户对话
# 内容——对齐 attach_cc 的 isMeta/本地命令回声过滤精神，导入/摘要一律跳过（实测 91 份 rollout 归纳）
_CODEX_META_PREFIXES = ("<environment_context", "<user_instructions", "<recommended_plugins",
                        "<in-app-browser-context", "<codex_internal_context", "<turn_aborted")


def _codex_transcript_rows(path: str) -> list[dict]:
    """解析 codex rollout → [{role, text, ts}]（user/assistant 对话行，时间正序）。

    只取 type=response_item 且 payload.role ∈ _codex._DIALOG_ROLES 的行（session_meta/
    turn_context/event_msg/reasoning/function_call/developer 指令注入天然排除）；文本提取
    复用 _codex._text（content 兼容字符串与块列表），ts 取行顶层 timestamp。防御语义同
    _cc_transcript_rows：坏行跳过、任何失败 → []（导入以空降级，绝不抛）。
    """
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line[: _cc_reader._MAX_LINE_KB * 1024])
                except json.JSONDecodeError:
                    continue
                pl = o.get("payload") or {}
                if o.get("type") != "response_item" or pl.get("role") not in _codex._DIALOG_ROLES:
                    continue
                text = _codex._text(pl.get("content"))
                if not text or text.startswith(_CODEX_META_PREFIXES):  # 机器条目（见上常量）
                    continue
                rows.append({"role": pl["role"], "text": text, "ts": _iso_ts(o.get("timestamp"))})
    except Exception:
        return []
    return rows


def _cc_latest_session(cwd: str) -> dict | None:
    """~/.claude/projects/<slug>/ 顶层 mtime 最新 .jsonl → cc 上次会话卡数据。

    只扫 slug 目录顶层（不递归）：<uuid>/subagents/ 与 tool-results/ 天然排除。
    无记录/任何失败 → None（前端不显示该卡）。
    """
    try:
        files = [p for p in _cc_project_dir(cwd).glob("*.jsonl") if p.is_file()]
        if not files:
            return None
        latest = max(files, key=lambda p: p.stat().st_mtime)
        rows = _cc_transcript_rows(latest)
        first_user = next((r["text"] for r in rows if r["role"] == "user"), "")
        return {"cc_session_id": latest.stem,
                "ts": int(latest.stat().st_mtime),
                "summary": first_user[:60],
                "message_count": len(rows)}
    except Exception:
        return None


def _codex_latest_session(cwd: str) -> dict | None:
    """~/.codex/sessions 按 cwd 过滤的最新一条 → codex 上次会话卡数据。

    复用 handoff_list 同款扫描（_codex.list_sessions：日期树混存，读首行 session_meta.cwd
    过滤，已按 timestamp 倒序），取 [0]；session_id 形态与 handoff_brief 入参对齐。
    """
    try:
        sessions = _codex.list_sessions(cwd, root=_codex_sessions_root())
    except Exception:
        return None
    if not sessions:
        return None
    latest = sessions[0]
    ts = _iso_ts(latest.get("timestamp"))
    if ts is None:
        try:
            ts = int(os.path.getmtime(latest.get("path") or ""))
        except Exception:
            ts = 0
    return {"session_id": str(latest.get("session_id") or ""),
            "ts": ts,
            "summary": str(latest.get("first_line") or "")}


def _find_cc_transcript(cc_session_id: str, cwd: str) -> Path | None:
    """定位 cc transcript 文件：先 cwd slug 目录精确命中，再全 projects 顶层 glob 兜底
    （都不递归——subagents/ 在 <uuid>/ 下深度 ≥2，天然排除）。找不到 → None。"""
    if not cc_session_id or not re.fullmatch(r"[A-Za-z0-9_-]+", cc_session_id):
        return None  # 白名单挡 ../ 路径逃逸与 / 分段（同 _cc_reader.read_transcript）
    direct = _cc_project_dir(cwd) / f"{cc_session_id}.jsonl"
    if direct.is_file():
        return direct
    hits = sorted(p for p in _cc_projects_base().glob(f"*/{cc_session_id}.jsonl") if p.is_file())
    return hits[-1] if hits else None


def attach_cc_session(db, *, cc_session_id: str, cwd: str) -> str | None:
    """DB 外 cc 会话导入落库（幂等），返回译宝 session_id；找不到 transcript 返回 None。

    幂等：cc_session_id 已在 sessions 表 → 直接返回既有 id，不重复导入。
    sessions 行：agent="cc"、source="import"、status="done"；created_at/finished_at 取
    transcript 内容时间（首/末消息行 timestamp），兜底文件 mtime。messages 表写整段
    transcript（seq 1..n；user 行带 CC uuid 作 rewind 锚点，对齐 _stream._persist 先例）。
    导入的会话 send 时走既有 resume 链路（SendSkill 读库 cc_session_id → SDK resume），无需特殊处理。
    """
    rows = db.query("sessions", where={"cc_session_id": cc_session_id})
    if rows:
        return str(rows[0]["id"])
    path = _find_cc_transcript(cc_session_id, cwd)
    if path is None:
        return None
    messages = _cc_transcript_rows(path)
    mtime = int(path.stat().st_mtime)
    times = [m["ts"] for m in messages if m["ts"] is not None]
    first_user = next((m["text"] for m in messages if m["role"] == "user"), "")
    sid = uuid.uuid4().hex[:12]
    db.insert("sessions", {
        "id": sid, "agent": "cc", "cwd": cwd,
        "prompt": first_user[:200] or "（导入的 Claude Code 会话）",
        "status": "done",
        "created_at": min(times) if times else mtime,
        "finished_at": max(times) if times else mtime,
        "cc_session_id": cc_session_id, "source": "import", "mode": "acceptEdits",
    })
    for i, m in enumerate(messages, start=1):
        try:
            db.insert("messages", {
                "session_id": sid, "role": m["role"], "text": m["text"],
                "ts": m["ts"] if m["ts"] is not None else mtime, "seq": i,
                "uuid": m["uuid"] if m["role"] == "user" else "",
            })
        except Exception as e:  # 单行落库失败不拖垮整段导入（仿 _stream._persist 隔离）
            print(f"[yibao/coding] attach_cc transcript 落库失败（跳过）：{e}", file=sys.stderr)
    return sid


class LastSessionsSkill(Skill):
    """统一接续 popover 区 1「上次会话」：跨源检测（cc + codex 每源最新一条，源不存在 → null）。"""
    id = "coding.last_sessions"
    label = "上次会话检测"
    description = (
        "检测指定项目目录的上次编码会话（跨源，每源最新一条）："
        "cc 读 ~/.claude/projects/<slug>/ mtime 最新 transcript（含译宝 DB 外会话），"
        "codex 读 ~/.codex/sessions 按 cwd 过滤最新。"
        "返回 {cc: {cc_session_id, ts, summary, message_count}|null, codex: {session_id, ts, summary}|null}。"
        "【需要】cwd（项目目录）。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"cwd": {"type": "string", "description": "项目目录"}},
                    "required": ["cwd"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cwd = os.path.expanduser(str(params.get("cwd") or "").strip())
        if not cwd:
            return ActionResult(success=False, error="缺少工作目录 cwd")
        return ActionResult(success=True, data={
            "cc": _cc_latest_session(cwd),
            "codex": _codex_latest_session(cwd),
        })


class AttachCcSkill(Skill):
    """把译宝 DB 外的 Claude Code 会话导入 DB（统一接续 popover 的 cc 卡「继续」前置）。

    导入后前端走现有 resumeSession（coding.history 读本 DB id 的 messages 表）；
    发新消息时 SendSkill 拿库里的 cc_session_id 走 SDK resume 原生续，上下文完整。
    """
    id = "coding.attach_cc"
    label = "导入 cc 会话"
    description = (
        "把指定 Claude Code 会话（译宝 DB 外）导入 coding 会话库：读本地 transcript 落 "
        "sessions/messages 表，返回译宝 session_id，之后按普通会话恢复/续聊（SDK 原生 resume "
        "cc_session_id）。幂等：cc_session_id 已在库直接返回既有 session_id。"
        "【需要】cc_session_id（Claude Code 会话 id）、cwd（项目目录）。"
    )
    default_risk = RiskLevel.L0_READONLY  # 只写本插件 DB（无文件/系统副作用），导入幂等

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"cc_session_id": {"type": "string", "description": "Claude Code 会话 id"},
                                   "cwd": {"type": "string", "description": "项目目录"}},
                    "required": ["cc_session_id", "cwd"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cc = str(params.get("cc_session_id") or "").strip()
        if not cc:
            return ActionResult(success=False, error="缺少 cc_session_id")
        cwd = os.path.expanduser(str(params.get("cwd") or "").strip())
        if not cwd:
            return ActionResult(success=False, error="缺少工作目录 cwd")
        try:
            sid = attach_cc_session(ctx.db, cc_session_id=cc, cwd=cwd)
        except Exception as e:
            return ActionResult(success=False, error=f"导入失败：{type(e).__name__}: {e}")
        if sid is None:
            return ActionResult(success=False, error=f"未找到 Claude Code 会话 transcript：{cc}")
        return ActionResult(success=True, data={"session_id": sid})


def _detect_drivers() -> list[dict]:
    """引擎可用性探测：claude-code 恒可用（SDK 内嵌）；codex 看二进制存在性 + 版本。
    auth 不检测（未登录时 exec 秒败走 error 事件，文案引导 codex login——留档）；
    任何探测失败 → codex unavailable（绝不抛）。"""
    drivers = [{"id": "claude-code", "available": True}]
    codex: dict = {"id": "codex", "available": False, "version": None}
    try:
        path = shutil.which("codex")
        if path:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
            if out.returncode == 0 and out.stdout.strip():
                codex = {"id": "codex", "available": True,
                         "version": out.stdout.strip().split()[-1]}   # "codex-cli 0.137.0" → 版本号
    except Exception:
        pass
    drivers.append(codex)
    return drivers


class DriversSkill(Skill):
    """编码引擎探测（ctx-row 引擎 chip 数据源）：claude-code 恒可用；codex 探测二进制+版本。
    L0 quiet（api.toml 无 panel）——chip 初始化/刷新时直调，不开面板。"""
    id = "coding.drivers"
    label = "编码引擎探测"
    description = (
        "探测可用编码引擎：claude-code 恒可用；codex 检测 CLI 二进制存在性与版本"
        "（shutil.which + codex --version，3s 容错）。返回 {drivers: [{id, available, version?}]}。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        return ActionResult(success=True, data={"drivers": _detect_drivers()})


def _find_codex_session(thread_id: str) -> dict | None:
    """~/.codex/sessions 全树扫首行 session_meta 匹配 thread_id → {cwd, timestamp, path, first_line}。

    attach_codex 只有 thread_id 没有 cwd（list_sessions 必须按 cwd 过滤，用不上）——
    直接扫 rollout 首行 session_meta（cwd/timestamp 都在 payload 里），再读首条 user 摘要。
    thread_id 白名单校验（只作等值比对，防路径逃逸意图）；任何失败 → None。
    """
    if not thread_id or not re.fullmatch(r"[A-Za-z0-9_-]+", thread_id):
        return None
    root = os.path.expanduser(_codex_sessions_root())
    try:
        candidates = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    except Exception:
        return None
    for path in candidates:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                meta = json.loads(f.readline())
        except Exception:
            continue
        if meta.get("type") != "session_meta":
            continue
        p = meta.get("payload") or {}
        if p.get("session_id") != thread_id:
            continue
        first_line = None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    pl = o.get("payload") or {}
                    if o.get("type") == "response_item" and pl.get("role") == "user":
                        first_line = _codex._text(pl.get("content"))[:60] or None
                        break
        except Exception:
            pass
        return {"cwd": p.get("cwd") or "", "timestamp": p.get("timestamp") or "",
                "path": path, "first_line": first_line}
    return None


def _import_codex_messages(db, sid: str, path: str, *, fallback_ts: int) -> None:
    """rollout 对话行落 messages 表（seq 1..n；ts 缺则 fallback_ts 兜底；codex 无 uuid 锚点留 ""）。

    单行落库失败不拖垮整段导入（仿 attach_cc/_stream._persist 隔离）。
    """
    for i, m in enumerate(_codex_transcript_rows(path), start=1):
        try:
            db.insert("messages", {
                "session_id": sid, "role": m["role"], "text": m["text"],
                "ts": m["ts"] if m["ts"] is not None else fallback_ts,
                "seq": i, "uuid": "",
            })
        except Exception as e:  # 单行落库失败不拖垮整段导入（仿 _stream._persist 隔离）
            print(f"[yibao/coding] attach_codex rollout 落库失败（跳过）：{e}", file=sys.stderr)


def attach_codex_session(db, *, thread_id: str) -> str | None:
    """codex 原生续导入落库（幂等），返回译宝 session_id；找不到 rollout 返回 None。

    幂等：cc_session_id（=thread_id）已在 sessions 表 → 直接返回既有 id，不重复导入；
    但既有行 messages 为空（v2 初期 attach 只建 sessions 空行）→ 补导 rollout 消息。
    sessions 行：agent="codex"、source="native"、status="done"；cwd 取 rollout session_meta，
    prompt=首条 user 摘要截 60；created_at/finished_at 取 session_meta timestamp，兜底文件 mtime。
    messages 表写整段 rollout 对话（seq 1..n、role user/assistant、ts 有则带），
    resumeSession 经 coding.history 读回完整对话；导入后 send 走既有 resume 链路
    （SendSkill 按 agent=codex → codex exec resume thread_id）。
    """
    rows = db.query("sessions", where={"cc_session_id": thread_id})
    if rows:
        sid = str(rows[0]["id"])
        try:
            has_msgs = bool(db.query("messages", where={"session_id": sid}, limit=1))
        except Exception:
            has_msgs = True   # 查询失败不冒险重插（保持幂等直返语义）
        if not has_msgs:
            meta = _find_codex_session(thread_id)
            if meta is not None:   # rollout 已删 → 无源可补，静默直返
                fb = rows[0].get("created_at")
                if not isinstance(fb, int):
                    fb = _iso_ts(meta.get("timestamp")) or int(time.time())
                _import_codex_messages(db, sid, meta["path"], fallback_ts=fb)
        return sid
    meta = _find_codex_session(thread_id)
    if meta is None:
        return None
    ts = _iso_ts(meta.get("timestamp"))
    if ts is None:
        try:
            ts = int(os.path.getmtime(meta["path"]))
        except Exception:
            ts = int(time.time())
    sid = uuid.uuid4().hex[:12]
    db.insert("sessions", {
        "id": sid, "agent": "codex", "cwd": meta["cwd"],
        "prompt": (meta.get("first_line") or "")[:60] or "（导入的 Codex 会话）",
        "status": "done", "created_at": ts, "finished_at": ts,
        "cc_session_id": thread_id, "source": "native", "mode": "acceptEdits",
    })
    _import_codex_messages(db, sid, meta["path"], fallback_ts=ts)
    return sid


class AttachCodexSkill(Skill):
    """codex 会话原生续导入（接续 popover codex 卡「原生续」前置）。

    建 DB 行（agent="codex" + cc_session_id=thread_id）并导入 rollout 对话消息进 messages 表，
    前端走现有 resumeSession（coding.history 读回完整对话）；发新消息时 SendSkill 按 agent=codex
    走 codex exec resume 原生续，上下文完整。幂等按 cc_session_id；既有空消息行补导。
    """
    id = "coding.attach_codex"
    label = "导入 codex 会话"
    description = (
        "把指定 Codex 会话（thread_id）导入 coding 会话库：读 ~/.codex/sessions 的 rollout，"
        "落 sessions 行（agent=codex）+ 对话消息进 messages 表，返回译宝 session_id，之后按普通会话"
        "恢复/续聊（codex exec resume 原生续）。幂等：thread_id 已在库直接返回既有 session_id"
        "（既有行 messages 为空时补导）。【需要】session_id（Codex thread_id）。"
    )
    default_risk = RiskLevel.L0_READONLY  # 只写本插件 DB（无文件/系统副作用），导入幂等

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id, "description": self.description,
                "parameters": {"type": "object",
                    "properties": {"session_id": {"type": "string", "description": "Codex thread_id"}},
                    "required": ["session_id"]}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        tid = str(params.get("session_id") or "").strip()
        if not tid:
            return ActionResult(success=False, error="缺少 session_id")
        try:
            sid = attach_codex_session(ctx.db, thread_id=tid)
        except Exception as e:
            return ActionResult(success=False, error=f"导入失败：{type(e).__name__}: {e}")
        if sid is None:
            return ActionResult(success=False, error=f"未找到 Codex 会话：{tid}")
        return ActionResult(success=True, data={"session_id": sid})


def make_tools(ctx: Any) -> list[Skill]:
    """插件加载器入口（_load_code_tools 遍历 skills/*.py 调本函数）。"""
    return [StartSkill(), SendSkill(), StopSkill(), ListSkill(), AttachSkill(),
            WallDataSkill(), HandoffListSkill(), HandoffBriefSkill(), HistorySkill(), ModeSkill(),
            RewindSkill(), DecideSkill(), FilesSkill(), LastSessionsSkill(), AttachCcSkill(),
            DriversSkill(), AttachCodexSkill(), SessionBriefSkill()]
