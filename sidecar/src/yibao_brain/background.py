"""后台循环与纯 helper（从 server.py 拆出，server 只留调度与 IPC 分发）。

纪律：本模块的函数从 server.py 原样搬来，不改逻辑；改行为请另开 commit。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from .loop import _offload
from .plugins import get_plugin_summaries
from .watch import WatchCtx


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
                             run_state: dict, write_msg, dispatcher=None) -> None:
    """到期提醒分发：Feed/历史照落（可追溯底线）；气泡广播与 TTS 受 proactive.level 管辖。"""
    text = str(r.get("text", ""))
    level = _proactive_level(settings)
    if dispatcher is not None:
        await dispatcher.dispatch({"kind": "reminder", "text": text, "rid": r.get("id")})
    else:
        feed.add("reminder", text, {"rid": r.get("id")})  # compatibility path for tests
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
    if dispatcher is None and level == "full" and voice is not None and settings.get("proactive_voice", True) \
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
    # load_settings 经 server 模块间接取，保留 yibao_brain.server.load_settings 的
    # monkeypatch 路径（test_server.test_dock_list_* 依赖该 patch 点）。
    from . import server as _server
    pinned_raw = list(_server.load_settings().get("dock_pinned") or [])
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


def _watch_tick(behaviors, snapshot, stgs) -> list:
    """跑一轮 watch 行为；返回经 _gate_proactive_event 放行的事件列表。

    单行为报错只记 stderr、跳过，不影响其它行为或整轮 watch。
    """
    ctx = WatchCtx(settings=stgs)
    out: list[dict] = []
    for b in behaviors:
        try:
            ev = b.tick(snapshot, ctx)
        except Exception as e:
            print(f"[yibao] watch 行为 {getattr(b, 'name', '?')} 报错（跳过）：{e}", file=sys.stderr)
            continue
        if not ev:
            continue
        gated = _gate_proactive_event(ev, stgs)
        if gated is not None:
            out.append(gated)
    return out


def _recover_background_jobs(feed, background_jobs, jobs_store, emit) -> None:
    """watch_command 跨重启恢复（启动钩子）：store 挂上 manager；
    上代进程的 running 孤儿能重跑的重跑（Feed 记账），否则标 interrupted（Feed 记账）。
    恢复是增强面：任何单条失败不拖垮启动。"""
    if background_jobs is None:
        return
    background_jobs._store = jobs_store

    def _restart(orphan: dict):
        if not os.path.isdir(orphan["cwd"]):
            return None
        return background_jobs.start(
            orphan["command"], cwd=orphan["cwd"], name=orphan["name"],
            timeout=orphan["timeout"], emit=emit)

    for r in background_jobs.recover_orphans(restart=_restart):
        if r["outcome"] == "restarted":
            feed.add("event", f"大脑重启，后台任务已重新执行（原任务 {r['orphan']}）",
                     {"type": "watch_command"})
        else:
            feed.add("event", f"后台任务因大脑重启中断，未重跑（{r['orphan']}）",
                     {"type": "watch_command"})


def _consume_invoke_context(invoke_ctx: dict) -> str | None:
    """取出新鲜（<60s）的唤起屏幕描述并清空暂存（一次性）；没有/过期 → None 并清空。"""
    text = invoke_ctx.get("text")
    fresh = bool(text) and time.time() - float(invoke_ctx.get("ts") or 0.0) < 60
    invoke_ctx.update({"text": None, "ts": 0.0})
    return str(text) if fresh else None


def _describe_screen(client, b64: str) -> str | None:
    """一句话描述屏幕内容（截图唤起上下文注入用）；任何失败返 None（静默跳过）。"""
    from .llm import describe_screen

    return describe_screen(client, b64)
