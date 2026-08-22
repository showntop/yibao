"""coding 插件 skills：start（建 session + 后台流式）/ stop（race-safe 取消）/ list。

三件事：
1. start_session：纯函数，往 sessions 表插一行 running（测试直接打）。
2. _spawn_stream：起 daemon 线程，线程内开自己的 asyncio loop 跑 ClaudeCodeRunner，
   每条 SDK 事件 normalize 后经 ctx.emit_event → panel_data 推 coding:studio 面板。
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
    R-35 归一：加载逻辑收敛到 _common.load_sibling（公共件无兄弟依赖，无循环），
    本文件与 sessions/transcript/_session_skills 均为薄委托。
    """
    return _load_common().load_sibling(Path(__file__).parent, "yibao_plugin_coding", stem)


def _load_common():
    """加载本目录 _common.py（固定路径内联；公共件不含兄弟依赖，无递归/无二次种子加载）。"""
    name = "yibao_plugin_coding__common"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("_common.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_runner = _sibling("_runner")
ClaudeCodeRunner = _runner.ClaudeCodeRunner   # 生产默认 runner factory（claude-code/cc）
_codex_runner = _sibling("_codex_runner")
CodexCliRunner = _codex_runner.CodexCliRunner  # codex CLI 子进程 runner factory
_PERM = _runner._PERM                         # can_use_tool 裁决注册表（rid → {event, allow, tool, summary, params}；DecideSkill 消费，perm_pending 读展示字段）


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

_sess = _sibling("sessions")  # 会话核心域（sessions.py）

# re-export：会话运行态与收尾（技能类与 tests/test_coding_plugin 沿用 coding 命名空间路径）
_spawn_stream = _sess._spawn_stream
_stream = _sess._stream
_stop_session = _sess._stop_session
_report_final = _sess._report_final
_persist_marker = _sess._persist_marker
_session_brief = _sess._session_brief
_usage_suffix = _sess._usage_suffix

_tr = _sibling("transcript")  # 转录解析域（transcript.py）
_session_skills = _sibling("_session_skills")  # 会话生命周期技能域（R-15 拆出：start/send/stop/list/attach）
start_session = _session_skills.start_session          # re-export：测试 codingmod.start_session 直打
_live_state = _session_skills._live_state              # re-export：活体状态判定
StartSkill = _session_skills.StartSkill                # re-export：测试/装配沿用 coding 命名空间
SendSkill = _session_skills.SendSkill
StopSkill = _session_skills.StopSkill
ListSkill = _session_skills.ListSkill
AttachSkill = _session_skills.AttachSkill


def _codex_sessions_root() -> str:
    """Codex session JSONL 根目录（间接层，供测试 monkeypatch 改道到 tmp 目录）。

    转录域（transcript.py）内部经本模块动态解析此函数（见 transcript._codex_sessions_root），
    测试 monkeypatch codingmod._codex_sessions_root 即可让 last_sessions/attach_codex 改道。"""
    return os.path.expanduser("~/.codex/sessions")




class StudioSkill(Skill):
    """打开 coding:studio 多工位面板(R4 module 面板,新 coding UI 骨架)。

    L0 只读:只发 panel 事件。数据消费全部走面板内 invoke(coding.list 等既有方法),
    本 skill 不带数据(data={})。
    """
    id = "coding.studio"
    label = "多工位"
    description = "打开 coding 多工位面板(module 面板运行时;多会话同屏工位,阶段一为骨架)。"
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
        return ActionResult(success=True, data={}, panel="coding:studio")


class HandoffListSkill(Skill):
    """列指定项目下 Codex 的会话（跨 agent 交接入口；只读）。

    经模块级 `_tr._codex_sessions_root()` 取 session 根 → `_codex.list_sessions(cwd)`，
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
            sessions = _codex.list_sessions(cwd, root=_tr._codex_sessions_root())
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
            sessions = _codex.list_sessions(cwd, root=_tr._codex_sessions_root())
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
        brief = _session_brief(ctx.db, sid, getattr(ctx, "llm", None), src, dst)
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
        entry = _sess._SESSIONS.get(sid)   # .get 防御 KeyError 缝：stop/收尾线程 pop entry 与 check-then-act 竞态（T3 评审）
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
                emit({"kind": "panel_data", "payload": {"panel": "coding:studio",
                      "data": {"session_id": sid, "event": ev}}})

        # 在跑：下条消息前由 runner 执行（消费 rewind_pending）。
        # .get 防御 KeyError 缝：stop/收尾线程 pop entry 与 check-then-act 竞态（T3 评审 mode_pending 同款）
        entry = _sess._SESSIONS.get(sid)
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


class PermPendingSkill(Skill):
    """待批权限清单（studio review 栏挂载快照；quiet 只读）。

    面板晚开错过 permission_request 流事件时补齐全量挂起项；之后的增删由流事件驱动
    （permission_request 增 / permission_done 删）。rid 解析 sid：perm_<sid>_<seq>，rsplit 去尾序号。
    """
    id = "coding.perm_pending"
    label = "待批权限清单"
    description = "列出 coding 当前全部挂起中的工具权限请求（review 栏快照用；L0 只读）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        out = []
        for rid, entry in list(_PERM.items()):
            if not isinstance(entry, dict) or entry.get("allow") is not None:
                continue
            sid = rid[len("perm_"):].rsplit("_", 1)[0] if rid.startswith("perm_") else ""
            out.append({"rid": rid, "sid": sid,
                        "tool": str(entry.get("tool") or ""),
                        "summary": str(entry.get("summary") or ""),
                        "params": entry.get("params") if isinstance(entry.get("params"), dict) else {}})
        return ActionResult(success=True, data={"pending": out})


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
    path = _tr._find_cc_transcript(cc_session_id, cwd)
    if path is None:
        return None
    messages = _tr._cc_transcript_rows(path)
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
            "cc": _tr._cc_latest_session(cwd),
            "codex": _tr._codex_latest_session(cwd),
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
            meta = _tr._find_codex_session(thread_id)
            if meta is not None:   # rollout 已删 → 无源可补，静默直返
                fb = rows[0].get("created_at")
                if not isinstance(fb, int):
                    fb = _tr._iso_ts(meta.get("timestamp")) or int(time.time())
                _tr._import_codex_messages(db, sid, meta["path"], fallback_ts=fb)
        return sid
    meta = _tr._find_codex_session(thread_id)
    if meta is None:
        return None
    ts = _tr._iso_ts(meta.get("timestamp"))
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
    _tr._import_codex_messages(db, sid, meta["path"], fallback_ts=ts)
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


def _reconcile_stale_running(ctx: Any) -> None:
    """底座重启对账（make_tools 时跑一次，仿 agents._reconcile_orphans）：sessions 表
    status="running" 但内存无活体 entry（_sess._SESSIONS 随进程蒸发）→ 落 interrupted +
    messages 补 marker「底座重启，会话中断，可 send 续跑」。coding 是 in-process，
    判据就是「内存无 entry」而非 pid。对账只读插件 db，任何失败只 print 不炸加载。"""
    db = getattr(ctx, "db", None)
    if db is None:
        return
    try:
        rows = db.query("sessions", where={"status": "running"})
    except Exception as e:
        print(f"[yibao/coding] 陈旧 running 对账查询失败：{e}", file=sys.stderr)
        return
    for row in rows:
        sid = str(row.get("id") or "")
        if not sid or sid in _sess._SESSIONS:
            continue                     # 有活体（理论不会：加载时无流式线程）→ 不动
        try:
            db.update("sessions", sid, {"status": "interrupted",
                                        "finished_at": int(time.time())})
            _persist_marker(db, sid, "底座重启，会话中断，可 send 续跑")
            print(f"[yibao/coding] 对账：会话 {sid} 无活体 entry，落 interrupted", file=sys.stderr)
        except Exception as e:
            print(f"[yibao/coding] 会话 {sid} 对账落库失败：{e}", file=sys.stderr)


def make_tools(ctx: Any) -> list[Skill]:
    """插件加载器入口（_load_code_tools 遍历 skills/*.py 调本函数）。"""
    _reconcile_stale_running(ctx)
    return [_session_skills.StartSkill(), _session_skills.SendSkill(), _session_skills.StopSkill(),
            _session_skills.ListSkill(), _session_skills.AttachSkill(),
            HandoffListSkill(), HandoffBriefSkill(), HistorySkill(), ModeSkill(),
            RewindSkill(), DecideSkill(), PermPendingSkill(), FilesSkill(), LastSessionsSkill(), AttachCcSkill(),
            DriversSkill(), AttachCodexSkill(), SessionBriefSkill(), StudioSkill()]
