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
import importlib.util
import os
import sys
import threading
import time
import uuid
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
ClaudeCodeRunner = _runner.ClaudeCodeRunner   # 生产默认 runner factory

# codex_reader / _brief 也是同目录兄弟模块（非包内），经 _sibling 加载（同 _runner）。
# _build_brief / _codex_sessions_root 做模块级间接：测试 monkeypatch 这两个属性即可
# 隔离真实文件系统与 LLM，不污染 yibao_plugin_coding__brief / codex_reader 模块本身。
_codex = _sibling("_codex_reader")
_brief_mod = _sibling("_brief")
_build_brief = _brief_mod.build_brief


def _codex_sessions_root() -> str:
    """Codex session JSONL 根目录（间接层，供测试 monkeypatch 改道到 tmp 目录）。"""
    return os.path.expanduser("~/.codex/sessions")


# sid -> {"cancel": threading.Event}。stop 经此拿 cancel 信号；线程收尾后 pop。
# 跨进程丢失（底座重启）无碍——sessions 表 status 仍 running，但 cancel 信号没了，
# C7 集成验收时再加对账（仿 agents._reconcile_orphans）；v1 不做。
_SESSIONS: dict[str, dict] = {}


def start_session(db, *, agent: str, cwd: str, prompt: str, source: str = "") -> str:
    """纯函数：往 sessions 表插一行 running，返回 sid。不碰线程/runner（测试可直打）。

    source：会话来源标记——""=用户直起；"codex:<sid>"=从 codex 交接切过来（HandoffSkill 起）。
    透传落库，便于后续面板/审计按来源过滤；不参与 runner 行为。
    """
    sid = uuid.uuid4().hex[:12]
    db.insert("sessions", {
        "id": sid, "agent": agent, "cwd": cwd, "prompt": prompt,
        "status": "running", "created_at": int(time.time()), "finished_at": 0,
        "source": source,
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
                  resume_session_id: str | None = None) -> None:
    """起 daemon 线程跑 runner（线程内自带 asyncio loop）。

    emit_event 已线程安全（proactive_dispatcher.emit → call_soon_threadsafe），
    daemon 线程直调即可。db 经参数链一路传到 _stream（落最终状态用）。
    resume_session_id：非 None 时透传 runner.run，续上同一 CC 会话历史（多轮）；
        None（StartSkill 路径）→ 全新会话。
    """
    cancel = threading.Event()
    _SESSIONS[sid] = {"cancel": cancel}

    def _thread():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _stream(db, sid, cwd, prompt, runner, emit_event, cancel,
                        resume_session_id=resume_session_id))
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
                  resume_session_id: str | None = None) -> None:
    """跑 runner；每条事件转 panel_data 推面板 + 落 messages 表；结束按 cancel/error/done 落最终状态。

    transcript 落库：user_msg（带 CC uuid，rewind 锚点）/ text_delta / done·stopped 终态 marker，
    seq 跨轮续号（每轮流式开始时从库里查本 sid 当前 max seq 续起，多轮不交错；
    HistorySkill 按 seq 取最近 40 条）。
    落库 try/except 隔离——transcript 丢失绝不许炸断流式。
    落最终状态前先查当前 status——用户主动 stop 时 _stop_session 已先写 stopped，
    这里保留 stopped 不被 done/failed 覆盖（race-safe，仿 agents._common._wait:66-73）。
    resume_session_id：非 None 时透传 runner.run，续上同一 CC 会话历史（多轮）。
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
        elif kind == "stopped":
            _persist("marker", str(ev.get("text") or "已中断"))
        if emit_event is not None:
            # panel/data 必须包在 payload 下：PanelApp.vue 的 panel_data 处理读
            # e.payload?.panel / e.payload?.data，shell proactive→Rust→PanelApp 不再加包装。
            emit_event({"kind": "panel_data",
                        "payload": {"panel": "coding:chat",
                                    "data": {"session_id": sid, "event": ev}}})
        if ev.get("kind") == "error":
            state["error"] = True

    try:
        cc_sid = await runner.run(prompt, cwd, on_event=on_event, cancel_event=_AsyncShield(cancel),
                                  resume_session_id=resume_session_id)
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
        # 后续多轮 resume 仍需它；runner 未拿到（取消/失败）则存 ""。
        db.update("sessions", sid, {
            "status": final,
            "finished_at": int(time.time()),
            "cc_session_id": cc_sid or "",
        })
    except Exception as e:
        print(f"[yibao/coding] session {sid} 落最终状态失败：{type(e).__name__}: {e}",
              file=sys.stderr)


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
    return True


class StartSkill(Skill):
    id = "coding.start"
    label = "开始编码会话"
    description = (
        "开始一个 coding 会话：选项目目录 + Claude Code，提交任务后台流式跑，"
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
                        "agent": {"type": "string", "description": "智能体（v1 固定 claude-code）"},
                        "source": {"type": "string", "description": "会话来源（可选）：用户直起留空；交接路径传 'codex:<sid>'"},
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
        sid = start_session(ctx.db, agent=agent, cwd=cwd, prompt=prompt, source=source)
        # 生产默认 runner；测试经 monkeypatch _spawn_stream 不真起线程
        # resume_session_id 不传 → None → 全新 CC 会话（首条消息）
        _spawn_stream(ctx.db, sid, cwd, prompt, ClaudeCodeRunner(), ctx.emit_event)
        return ActionResult(success=True, data={
            "session_id": sid,
            "panel": "coding:chat",
            "human": f"已开始编码会话 {sid}，面板实时回显",
        })


class SendSkill(Skill):
    id = "coding.send"
    label = "接续编码会话"
    description = (
        "向既有 coding 会话追加一条消息（多轮）：用 cc_session_id resume 同一 Claude Code 历史，"
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
        # 重置 running 状态：resume 是新一轮流式，finished_at 归零
        ctx.db.update("sessions", sid, {"status": "running", "finished_at": 0})
        _spawn_stream(ctx.db, sid, cwd, prompt, ClaudeCodeRunner(), ctx.emit_event,
                      resume_session_id=cc)
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


class ListSkill(Skill):
    id = "coding.list"
    label = "编码会话列表"
    description = "列出 coding 会话（按创建时间倒序）。"
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
        return ActionResult(success=True, data={"sessions": rows, "panel": "coding:chat"})


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


def make_tools(ctx: Any) -> list[Skill]:
    """插件加载器入口（_load_code_tools 遍历 skills/*.py 调本函数）。"""
    return [StartSkill(), SendSkill(), StopSkill(), ListSkill(),
            HandoffListSkill(), HandoffBriefSkill(), HistorySkill()]
