"""编码会话生命周期技能域（R-15 自 coding.py 拆出）：start/send/stop/list/attach。

依赖说明：技能类运行时经 `_c()` 动态解析 coding 主模块的模块级符号
（_runner_for/_spawn_stream/_stop_session/_persist_marker/_session_brief/_PERM）——
不是绕路，是保住测试既有约定：monkeypatch codingmod.<attr> 必须继续生效
（名字解析推迟到调用时点，拆分不改测试语义）。sessions 域共享同一模块实例，
patch codingmod._sess._SESSIONS 两边都看得见。
"""
import importlib.util
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


def _sibling(stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules（R-35 归一：加载逻辑在 _common.load_sibling，薄委托）。

    _load_common 固定路径内联加载公共件（不含兄弟依赖，无递归）；与 coding.py 同源，
    不重复维护加载逻辑。
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


_sess = _sibling("sessions")  # 会话核心域（sessions.py）：_SESSIONS 等共享实例


def _c():
    """coding 主模块：测试直接 `import coding`（skills 目录在 sys.path）；
    生产加载器不挂 sys.modules（见 plugins._import_file 注释），且无人 _sibling("coding")，
    须现场兜底加载并挂表（2026-08-24 生产 KeyError('coding') 的根因）。"""
    mod = sys.modules.get("yibao_plugin_coding_coding")
    if mod is not None:
        return mod
    mod = sys.modules.get("coding")
    if mod is not None:
        return mod
    return _sibling("coding")


def start_session(db, *, agent: str, cwd: str, prompt: str, source: str = "",
                  mode: str = "acceptEdits") -> str:
    """纯函数：往 sessions 表插一行 running，返回 sid。不碰线程/runner（测试可直打）。

    source：会话来源标记——""=用户直起；"codex:<sid>"=从 codex 交接切过来（HandoffTool 起）。
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


class StartTool(Tool):
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
            runner = _c()._runner_for(agent)
        except ValueError as e:
            return ActionResult(success=False, error=str(e))
        sid = start_session(ctx.db, agent=agent, cwd=cwd, prompt=prompt, source=source, mode=mode)
        # 生产默认 runner（_runner_for 按 agent 选）；测试经 monkeypatch _spawn_stream 不真起线程
        # resume_session_id 不传 → None → 全新会话（首条消息）
        _c()._spawn_stream(ctx.db, sid, cwd, prompt, runner, ctx.emit_event,
                           permission_mode=mode, agent=agent)
        return ActionResult(success=True, data={
            "session_id": sid,
            # background=true → panel=None（loop 判空不开面板），静默执行靠终态任务卡汇报
            "panel": None if background else "coding:studio",
            "human": (f"已开始后台编码会话 {sid}，完成会汇报" if background
                      else f"已开始编码会话 {sid}，面板实时回显"),
        })


class SendTool(Tool):
    id = "coding.send"
    label = "接续编码会话"
    description = (
        "向既有 coding 会话追加一条消息（多轮）：按会话引擎用 cc_session_id resume 同一会话历史"
        "（Claude Code 走 SDK resume；Codex 走 codex exec resume thread_id），"
        "继续在同一上下文里干活，面板实时回显。立即返回，完成主动推 panel_data。"
        "会话正在运行时不拒绝：prompt 进督导队列（返回 queued），当前轮结束后自动合并接续。"
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
        entry = _sess._SESSIONS.get(sid)   # .get 防御 KeyError 缝：stop/收尾线程 pop entry 与 check-then-act 竞态（T3 评审）
        if entry is not None:
            if row.get("status") == "running":
                # 运行中 steer（spec §A）：不拒绝，prompt 排队进 live entry 的 steer 队列，
                # 当前轮 done 后由 _stream 合并续跑（resume 同引擎同会话，期间新到继续入队）。
                # 并发安全照抄 entry 级操作模式（setdefault/append 与 _sess._SESSIONS 同锁域）。
                queue = entry.setdefault("steer", [])
                queue.append(prompt)
                # B2 收尾竞态封口（入队后复读）：_stream 收尾前先立 closing 再查队——
                # 复读到 closing 说明查队已过、本条必然不被消费，自撤并回拒绝，
                # 不回假 ack（否则消息永不执行、会话落 done，用户被静默吞消息）
                if entry.get("closing"):
                    try:
                        queue.remove(prompt)
                    except ValueError:
                        pass
                    return ActionResult(success=False, error="会话正在收尾中，请稍候")
                pos = len(queue)
                text = f"督导补充已排队（第 {pos} 条），本轮结束后自动接续"
                _c()._persist_marker(ctx.db, sid, text)
                emit = getattr(ctx, "emit_event", None)
                if emit is not None:
                    # 面板流可见（仿 _stream marker 双写：已落库 + panel_data 进流）
                    emit({"kind": "panel_data",
                          "payload": {"panel": "coding:studio",
                                      "data": {"session_id": sid,
                                               "event": {"kind": "marker", "text": text}}}})
                return ActionResult(success=True, data={
                    "session_id": sid,
                    "queued": True,
                    "position": pos,
                    "panel": "coding:studio",
                    "human": f"已排队（第 {pos} 条），会话 {sid} 本轮结束后自动接续",
                })
            # check-then-act 缝：stop 落 stopped 但 runner 线程未退（长工具中）→ 同样拒
            return ActionResult(success=False, error="会话正在收尾中，请稍候")
        cc = row.get("cc_session_id") or ""
        cwd = row.get("cwd") or ""
        # mode 跨轮沿用：send 不带 mode → 用库值（start/coding.mode 落的）；带 → 覆盖回写
        mode = str(params.get("mode") or row.get("mode") or "acceptEdits")
        agent = str(row.get("agent") or "claude-code")   # 按会话落库引擎选驱动（codex 行走 exec resume）
        if not cc:
            # 首条消息未建立上下文（cc_session_id 为空）：runner 未捕获会话 id——常见于
            # codex 新会话/CC→codex 交接首轮失败（CLI 启动异常、stdout 零事件等）。
            # 不再一刀切拒绝（否则上下文/历史全丢，用户只能开新对话）——自动降级：
            # 以交接摘要在同一 sid 上重跑新会话（SessionBriefTool 手动补救的自动化，
            # 同 _stream 的 codex resume fallback 模式），前端无感、消息流连贯；
            # 重跑成功后 cc_session_id 自动更新为新 thread_id。llm 缺失/失败用原文节选兜底。
            src = dst = "Codex" if agent == "codex" else "Claude Code"
            brief = _c()._session_brief(ctx.db, sid, getattr(ctx, "llm", None), src, dst)
            prompt = f"【交接上下文】\n{brief}\n\n【用户继续】\n{prompt}"
        try:
            runner = _c()._runner_for(agent)
        except ValueError as e:
            return ActionResult(success=False, error=str(e))
        # 重置 running 状态：resume 是新一轮流式，finished_at 归零；mode 一并回写
        ctx.db.update("sessions", sid, {"status": "running", "finished_at": 0, "mode": mode})
        _c()._spawn_stream(ctx.db, sid, cwd, prompt, runner, ctx.emit_event,
                           resume_session_id=cc or None, permission_mode=mode, agent=agent,
                           llm=getattr(ctx, "llm", None))   # codex resume 失败 fallback 摘要用；无则不补救
        return ActionResult(success=True, data={
            "session_id": sid,
            "panel": "coding:studio",
            "human": (f"已为会话 {sid} 重建上下文（交接摘要续跑），面板实时回显"
                      if not cc else f"已接续会话 {sid}，面板实时回显"),
        })


class StopTool(Tool):
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
        ok = _c()._stop_session(ctx.db, _sess._SESSIONS, sid)
        if not ok:
            # 无 live runner（陈旧 running，如底座重启 mid-run）：db 已落 stopped——
            # 补发终态事件让面板复位，否则发送键永久锁死
            emit = getattr(ctx, "emit_event", None)
            if emit is not None:
                emit({"kind": "panel_data",
                      "payload": {"panel": "coding:studio",
                                  "data": {"session_id": sid,
                                           "event": {"kind": "stopped", "text": "已中断"}}}})
        return ActionResult(success=True, data={
            "id": sid,
            "human": f"已停止会话 {sid}",
        })


def _live_state(sid: str) -> str:
    """会话活体状态：waiting（_PERM 有该 sid 挂起审批）> running（_sess._SESSIONS 有 live entry）> idle。"""
    prefix = f"perm_{sid}_"
    for rid, entry in list(_c()._PERM.items()):
        if rid.startswith(prefix) and entry.get("allow") is None:
            return "waiting"
    if sid in _sess._SESSIONS:
        return "running"
    return "idle"


class ListTool(Tool):
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
        return ActionResult(success=True, data={"sessions": sessions, "panel": "coding:studio"})


class AttachTool(Tool):
    """打开 coding 面板并恢复指定会话（任务卡/studio 左栏「接管」点击路由）。

    只校验会话存在，真正的恢复在面板侧：api.toml 声明 panel="coding:studio"，
    直调成功后 panel_payload 把 data 原样透传进面板 init 数据（{session_id, agent, attach: true}），
    studio 面板的 handleData 见 attach 标志自动 resumeSession（P1 接管链路自然生效）；
    data.agent 让前端接管跨引擎会话时徽标立即正确，不用等首条流事件。
    """
    id = "coding.attach"
    label = "接管编码会话"
    description = (
        "打开 coding 面板并恢复指定会话的上下文（点击 Feed 任务卡/studio 左栏「接管」的路由）。"
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
        # attach 标志逐字对齐 studio 面板 handleData 的判别（data.attach === true）；
        # agent 取库行值（老行缺省按 claude-code，同 _stream/_runner_for 缺省）——
        # 前端接管跨引擎会话时引擎徽标立即正确，不等首条流事件
        return ActionResult(success=True, data={
            "session_id": sid,
            "agent": str(rows[0].get("agent") or "claude-code"),
            "attach": True,
            "human": f"已打开编码会话 {sid}",
        })
