"""coding 插件的会话核心域：_SESSIONS 注册表 + daemon 线程流式（_spawn_stream/_stream）+ 终态汇报/取消。

与 coding.py 的边界：本模块管会话运行态与落库收尾；技能类（start/send/stop/list/attach…）留在
coding.py（经 _sibling 引用本模块；coding 命名空间 re-export _stream/_stop_session 等保持测试路径）。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path


def make_tools(ctx):
    """辅助模块不直接贡献工具（工具统一由 coding.py 提供）；空实现通过插件加载器检查。"""
    return []


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


_runner = _sibling("_runner")        # make_permission_callback / release_pending_permissions
_codex = _sibling("_codex_reader")   # git_summary（交接 Brief 用）
_brief_mod = _sibling("_brief")
_build_brief = _brief_mod.build_brief
_tr = _sibling("transcript")         # 转录解析域（last_sessions 等间接层保持一致）


# sid -> {"cancel": threading.Event, ...}。stop 经此拿 cancel 信号；线程收尾后 pop。
# entry 可选键：steer（运行中督导补充队列）、mode_pending/rewind_pending（切模式/回滚通道）、
# usage_baseline（codex usage 差分基准）。跨进程丢失：_SESSIONS 随进程蒸发，sessions 表
# status 仍 running 的陈旧行由 make_tools 的 _reconcile_stale_running 对账落 interrupted + marker。
_SESSIONS: dict[str, dict] = {}


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
                  agent: str = "claude-code",
                  llm=None) -> None:
    """起 daemon 线程跑 runner（线程内自带 asyncio loop）。

    emit_event 已线程安全（proactive_dispatcher.emit → call_soon_threadsafe），
    daemon 线程直调即可。db 经参数链一路传到 _stream（落最终状态用）。
    resume_session_id：非 None 时透传 runner.run，续上同一 CC 会话历史（多轮）；
        None（StartTool 路径）→ 全新会话。
    permission_mode：透传 runner.run（acceptEdits/plan），进 SDK options / codex sandbox。
    agent：会话引擎 id（claude-code/cc/codex），透传 _stream → panel_data data 平级键
        （面板按此实时更新引擎徽标）。
    llm：会话级 LLM 能力（SendTool 透传 ctx.llm），仅供 _stream 的 codex resume
        失败 fallback 生成交接摘要用；None → 无 fallback，落原 failed 路径。
    _SESSIONS[sid] entry 同时是运行中切模式的通道（coding.mode 写 mode_pending，
    runner 每条消息前消费 → client.set_permission_mode）。
    usage_baseline 回填：codex 的 usage 差分基准（_codex_runner._diff_usage 就地写
    entry）跨轮持久在 sessions 表——entry 每轮新建且流终 pop，不回填则第 2 轮起
    baseline 落空、done 报 thread 全量累计，token 逐轮重复计。新建 entry 后查库回填；
    坏数据（非法 JSON/非 dict）静默跳过，baseline 按 0 退化为全量上报一轮，不炸流。
    CC runner 不读该键，回填与否皆无害。
    """
    cancel = threading.Event()
    entry: dict = {"cancel": cancel}
    try:
        rows = db.query("sessions", where={"id": sid})
        raw = str(rows[0].get("usage_baseline") or "") if rows else ""
        if raw:
            base = json.loads(raw)
            if isinstance(base, dict):
                entry["usage_baseline"] = base
    except Exception:
        pass   # 坏数据不炸流：baseline 缺失按 0 差分（详见 docstring）
    _SESSIONS[sid] = entry

    def _thread():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _stream(db, sid, cwd, prompt, runner, emit_event, cancel,
                        resume_session_id=resume_session_id,
                        permission_mode=permission_mode, agent=agent, llm=llm))
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

    thread = threading.Thread(target=_thread, daemon=True, name=f"yibao-coding-{sid}")
    try:
        thread.start()
    except Exception as e:
        # B6：线程起不来（资源耗尽等）→ 半失败态收口——行已插 running，不收口则永久卡
        # running（只能等重启对账）。落 failed + 补终态事件/汇报，绝不静默。
        print(f"[yibao/coding] session {sid} 流式线程启动失败：{type(e).__name__}: {e}",
              file=sys.stderr)
        _SESSIONS.pop(sid, None)
        try:
            db.update("sessions", sid, {"status": "failed", "finished_at": int(time.time())})
        except Exception:
            pass
        _report_final(emit_event, sid, prompt, "failed", None)


async def _stream(db, sid: str, cwd: str, prompt: str, runner, emit_event, cancel,
                  resume_session_id: str | None = None,
                  permission_mode: str = "acceptEdits",
                  agent: str = "claude-code",
                  llm=None) -> None:
    """跑 runner；每条事件转 panel_data 推面板 + 落 messages 表；结束按 cancel/error/done 落最终状态。

    transcript 落库：user_msg（带 CC uuid，rewind 锚点）/ text_delta / done·stopped 终态 marker，
    seq 跨轮续号（每轮流式开始时从库里查本 sid 当前 max seq 续起，多轮不交错；
    HistoryTool 按 seq 取最近 40 条）。
    落库 try/except 隔离——transcript 丢失绝不许炸断流式。
    落最终状态前先查当前 status——用户主动 stop 时 _stop_session 已先写 stopped，
    这里保留 stopped 不被 done/failed 覆盖（race-safe，仿 agents._common._wait:66-73）。
    resume_session_id：非 None 时透传 runner.run，续上同一会话历史（多轮；CC=SDK resume，
    codex=exec resume thread_id）。
    permission_mode：透传 runner.run（acceptEdits/plan）。
    agent：会话引擎 id，panel_data data 加平级 "agent" 键（data={session_id, agent, event}，
    面板按此实时更新引擎徽标）。
    llm：会话级 LLM 能力（SendTool 透传 ctx.llm）。codex resume 零事件失败
    （encrypted_content bug 形态：stdout 零事件 → 未捕获 thread.started → cc_sid None，
    runner returncode 守御补发 error）时一次性自动 fallback：用交接摘要新开会话续跑
    （SessionBriefTool 手动补救流程的自动化），再败自然落原 failed 路径（无循环）；
    llm None → 跳过 fallback。
    session_entry：_SESSIONS[sid] live entry 透传 runner.run——coding.mode 写入
    mode_pending 后，runner 每条消息前消费并 client.set_permission_mode（运行中切模式）。
    can_use_tool：每轮新建权限回调桥（make_permission_callback(sid, on_event, emit_event=…)）——
    SDK 触发权限询问时发 permission_request 进面板流 + confirmation_needed 进 L2 确认体系，
    阻塞等 confirm_batched 路由 / coding.decide 备用通道裁决（双通道幂等，超时默认 deny）。
    steer drain：本轮结束后查 _SESSIONS[sid]["steer"]（SendTool 对 running 会话的排队
    督导补充），非空合并续跑（resume_session_id=cc_sid），队列空才落终态汇报。
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
            # codex usage 差分基准落库：runner 发 done 前已把 thread 累计值就地写回
            # session_entry["usage_baseline"]（_codex_runner._diff_usage），这里同步落
            # sessions 行供下轮 _spawn_stream 回填（entry 流终 pop，内存留不住）。
            # CC 引擎 entry 无该键 → 跳过；失败只 print 不炸流（同 _persist 隔离风格）。
            try:
                entry = _SESSIONS.get(sid)
                base = entry.get("usage_baseline") if isinstance(entry, dict) else None
                if isinstance(base, dict) and base:
                    db.update("sessions", sid, {"usage_baseline": json.dumps(base)})
            except Exception as e:
                print(f"[yibao/coding] session {sid} usage_baseline 落库失败（跳过）：{e}",
                      file=sys.stderr)
        elif kind == "stopped":
            _persist("marker", str(ev.get("text") or "已中断"))
        elif kind == "marker":
            # 流内留痕（如 codex resume 失败 fallback 提示）：落 messages 表 + 经下方
            # emit_event 进面板流（双写一致，同 done/stopped 的 marker 发送方式）
            _persist("marker", str(ev.get("text") or ""))
        if emit_event is not None:
            # panel/data 必须包在 payload 下：PanelApp.vue 的 panel_data 处理读
            # e.payload?.panel / e.payload?.data，shell proactive→Rust→PanelApp 不再加包装。
            emit_event({"kind": "panel_data",
                        "payload": {"panel": "coding:studio",
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

    # codex resume 零事件失败的一次性自动 fallback（encrypted_content bug 形态：stdout 零事件
    # → 未捕获 thread.started → cc_sid None，runner returncode 守御补发 error——「resume 根本
    # 没跑起来」；turn 中途失败已捕获 thread_id，cc_sid 非 None 不触发）。判据齐备则用交接摘要
    # 新开会话续跑（SessionBriefTool 手动补救流程的自动化）；只重试一次，再败自然落原
    # failed 路径（state["error"] 由重试的 error 事件重立，无循环）。
    if (agent == "codex" and resume_session_id and cc_sid is None
            and state["error"] and not cancel.is_set() and llm is not None):
        # 摘要生成统一走 _session_brief（messages 尾 40 条 → LLM 凝练 → 原文节选兜底，恒有值）
        brief = _session_brief(db, sid, llm, "Codex", "Codex")
        on_event({"kind": "marker", "text": "resume 失败，已用交接摘要新开会话续跑"})
        state["error"] = False   # 复位，让重试自行定终态（再败由 on_event 的 error 分支重立）
        # 新会话沿用同一 session_entry：先清旧 thread 的 usage 差分基准，
        # 否则 fallback 轮差分被旧累计值钳 0 少报（S5-T2 评审留）
        _entry = _SESSIONS.get(sid)
        if isinstance(_entry, dict):
            _entry.pop("usage_baseline", None)
        try:
            cc_sid = await runner.run(
                f"【交接上下文】\n{brief}\n\n【用户继续】\n{prompt}", cwd,
                on_event=on_event, cancel_event=_AsyncShield(cancel),
                resume_session_id=None,
                permission_mode=permission_mode,
                can_use_tool=_runner.make_permission_callback(
                    sid, on_event, emit_event=emit_event),
                session_entry=_SESSIONS.get(sid))
        except Exception as e:  # 与首轮同款框架级兜底
            print(f"[yibao/coding] session {sid} fallback 重跑框架异常：{type(e).__name__}: {e}",
                  file=sys.stderr)
            state["error"] = True

    # steer 队列 drain（运行中 steer，spec §A）：本轮跑完后查 live entry 的 steer 队列——
    # 非空则取出全部排队消息按序合并为一条（【督导补充】前缀），以 resume_session_id=cc_sid
    # 原地续跑；期间新到的 steer 继续入队，逐轮 drain 直到队列空才真正落终态
    # （_report_final 只在队列空后触发）。stop 清队列 + cancel → 循环顶检查直接 break；
    # cc_sid 为 None（首轮取消/失败未捕获会话 id）无法 resume → break，终态照常落。
    while not cancel.is_set() and cc_sid:
        entry = _SESSIONS.get(sid)
        # B2 收尾竞态封口：先立 closing 再查队。SendTool 入队后复读 closing——
        # 「先立后查」与「查空即立（保持 True 到线程 finally pop）」两序都收敛：
        # 窗内晚到的入队者要么被本次查到（续跑），要么复读到 closing 自撤（收到拒绝，
        # 不再有「ack 后静默丢弃」）。续跑窗重开（closing=False），steer 可再入队。
        if isinstance(entry, dict):
            entry["closing"] = True
        queue = entry.get("steer") if isinstance(entry, dict) else None
        queued: list[str] = []
        while queue:                     # 逐条 pop(0)：与 SendTool 并发 append 不丢消息（GIL 原子）
            queued.append(str(queue.pop(0)))
        if not queued:
            break                        # closing 保持 True：收尾窗（落终态→线程 pop）内 SendTool 一律拒
        if isinstance(entry, dict):
            entry["closing"] = False     # 续跑窗重开：期间新到 steer 继续入队，下轮收尾再立
        on_event({"kind": "marker",
                  "text": f"督导补充已接续（合并 {len(queued)} 条排队消息）"})
        merged = "【督导补充】\n" + "\n".join(queued)
        try:
            new_sid = await runner.run(
                merged, cwd, on_event=on_event, cancel_event=_AsyncShield(cancel),
                resume_session_id=cc_sid,
                permission_mode=permission_mode,
                can_use_tool=_runner.make_permission_callback(
                    sid, on_event, emit_event=emit_event),
                session_entry=_SESSIONS.get(sid))
            if new_sid:
                cc_sid = new_sid
        except Exception as e:  # 与首轮同款框架级兜底
            print(f"[yibao/coding] session {sid} steer 续跑框架异常：{type(e).__name__}: {e}",
                  file=sys.stderr)
            state["error"] = True
        if state["error"]:
            break                        # 续跑失败：不再 drain，落 failed 终态（残余队列随流终丢弃）

    # B5 流终残留 pending 兜底：rewind/mode 的消费点在「下一条消息前」，本轮在最后一条
    # 消息后直接收尾时 entry 里的 pending 永不执行，而 RewindTool 已回执 success——
    # 补 marker 讲实话（再次点击 ⏪ 走 fresh-client 路径仍可回滚）；mode 库值已更新、
    # 下轮 send 自然生效，静默清即可。
    _entry = _SESSIONS.get(sid)
    if isinstance(_entry, dict):
        if _entry.pop("rewind_pending", None) is not None:
            on_event({"kind": "marker", "text": "回滚未执行（本轮恰已收尾），请再点一次 ⏪ 重试"})
        _entry.pop("mode_pending", None)

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
    # B7 终态覆盖缝：读态（running）→ 写终态之间用户 stop 完成（先落 stopped 后 set cancel）
    # 时，上面刚落的 done/failed 会反过来盖掉 stopped。cancel 已 set ⟹ stopped 必已落库
    # （stop 协议顺序保证）——写后复核一次，撞上即纠正回 stopped + 补 stopped 终态事件
    # （此路径 runner 已退出、未消费 cancel，不会有第二条 stopped 事件，不重复）。
    if final != "stopped" and cancel.is_set():
        final = "stopped"
        try:
            db.update("sessions", sid, {"status": "stopped", "finished_at": int(time.time())})
        except Exception as e:
            print(f"[yibao/coding] session {sid} 终态纠正失败：{type(e).__name__}: {e}",
                  file=sys.stderr)
        _persist("marker", "已中断")
        if emit_event is not None:
            emit_event({"kind": "panel_data",
                        "payload": {"panel": "coding:studio",
                                    "data": {"session_id": sid, "agent": agent,
                                             "event": {"kind": "stopped", "text": "已中断"}}}})
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
    带成本摘要。usage 数值本身不落库（只此一播进气泡/任务卡）——注意 sessions 表
    usage_baseline 列落的是 codex 差分基准（thread 累计值，下轮 resume 回填用），
    不是本轮用量，两者别混。"""
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


def _persist_marker(db, sid: str, text: str) -> None:
    """流外 marker 落 messages 表（steer 入队提示 / 重启对账留痕用）：seq 从库当前 max 续起；
    失败只 print 不炸（同 _stream._persist 隔离风格）。seq 与流内 _persist 并发撞号无碍
    （messages.seq 仅索引非唯一约束，面板按 seq 排序微乱可接受）。"""
    try:
        prev = db.query("messages", where={"session_id": sid}, order="seq DESC", limit=1)
        seq = int(prev[0]["seq"]) + 1 if prev else 1
        db.insert("messages", {
            "session_id": sid, "role": "marker", "text": text,
            "ts": int(time.time()), "seq": seq, "uuid": "",
        })
    except Exception as e:
        print(f"[yibao/coding] marker 落库失败（跳过）：{e}", file=sys.stderr)


def _session_brief(db, sid: str, llm, src: str, dst: str) -> str:
    """会话库任一会话的交接 Brief（三处共用：SessionBriefTool / _stream 的 codex
    resume fallback / SendTool 的 cc_session_id 空降级续跑）。

    取 messages 尾 40 条（seq DESC LIMIT 40 再反转回正序）→ git 摘要 → LLM 凝练；
    llm 缺失/失败/空历史退化为最近消息原文节选——恒有交接上下文，不被 LLM 故障挡路。
    src/dst 是引擎展示名（_build_brief 的 wording 入参）。
    """
    try:
        msgs = db.query("messages", where={"session_id": sid}, order="seq DESC", limit=40)
        msgs.reverse()
        turns = [{"role": m["role"], "text": m["text"]} for m in msgs]
    except Exception as e:
        print(f"[yibao/coding] session {sid} 查历史失败（按空历史续）：{e}", file=sys.stderr)
        turns = []
    brief = None
    if llm is not None and turns:
        git = ""
        try:
            rows = db.query("sessions", where={"id": sid})
            cwd = str(rows[0].get("cwd") or "") if rows else ""
            git = _codex.git_summary(cwd) if cwd else ""
        except Exception:
            git = ""   # git 摘要失败不挡路（非 git 目录等），brief 仍有对话内容
        brief = _build_brief(llm, turns, git, src, dst)
    if not brief:
        excerpt = "\n".join(f"{t['role']}: {str(t['text'])[:500]}" for t in turns[-10:]) or "（无历史消息）"
        brief = f"（摘要生成失败，以下为最近对话节选）\n{excerpt}"
    return brief


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
    # steer 队列连停（spec §A：停止=全停，不留尾巴）：清空排队督导补充，
    # _stream drain 循环顶的 cancel 检查兜底不再续跑（停了不会又自己跑起来）
    if isinstance(entry, dict):
        steer = entry.get("steer")
        if isinstance(steer, list):
            steer.clear()
    # 放行挂起的权限等待（deny 收场）：否则 cancel 要等权限 60s 超时才被消费，停止最长延迟 60s
    _runner.release_pending_permissions(sid)
    return True

