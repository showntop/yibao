"""工具域（R-13 第二步拆分序 1）：主屏/记忆页取数——进行中任务、问候统计、widget 取数、记忆列出。

从 server.serve_async 原样搬来（2026-08-22）；共享状态（agent/feed）经 RuntimeCtx 注入，
不再靠闭包捕获。改行为请另开 commit。
"""
from __future__ import annotations

import os
import time

from ..approvals import _coding_perm_registry
from ..config import plugin_data_dir
from ..llm import ToolCall
from ..log import log
from ..loop import _offload
from ..plugins import get_mem_namespaces, get_widgets, panel_payload
from ..safety import Decision


def _running_tasks(ctx, limit: int = 20) -> list[dict]:
    """读取 agents 与后台命令，只返回 Home 需要的 running 摘要。"""
    agent = ctx.agent
    adb_file = os.path.join(plugin_data_dir("agents"), "data.db")
    rows = []
    if os.path.exists(adb_file):
        try:
            from ..plugindb import PluginDb

            adb = PluginDb("agents")
            try:
                rows = adb.query(
                    "tasks", where={"status": "running"},
                    order="created_at DESC", limit=limit,
                )
            finally:
                adb.close()
        except Exception as e:
            log(f"进行中任务查询失败（已降级）：{e}")

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
            log(f"后台命令查询失败（已降级）：{e}")
    # coding 插件运行中会话（P2 督导）：sessions 表 status=running 为准——_SESSIONS
    # 仅存于流式期间、重启即丢，陈旧 running 由 coding.stop 的陈旧兜底补发 stopped，
    # 这里照列让用户在主屏可见可停（与 agents 段同策略：只读表，不碰插件内存态）。
    cdb_file = os.path.join(plugin_data_dir("coding"), "data.db")
    if os.path.exists(cdb_file):
        try:
            from ..plugindb import PluginDb

            cdb = PluginDb("coding")
            try:
                crows = cdb.query(
                    "sessions", where={"status": "running"},
                    order="created_at DESC", limit=limit,
                )
            finally:
                cdb.close()
        except Exception as e:
            log(f"coding 会话查询失败（已降级）：{e}")
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


def _feed_stats(ctx, running_tasks: list[dict] | None = None) -> dict:
    """主屏问候统计：待办提醒 / 进行中任务 / 近 24h 完成任务。"""
    agent = ctx.agent
    feed = ctx.feed
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


async def _collect_widgets(ctx) -> list[dict]:
    """主屏 widget 取数（OS 感 §4.2）：每个 widget 调其声明的 L0 method，
    返回 panel_payload 形状（+open 点击跳转方法）。单个失败/被拒只跳过，不拖垮其他。"""
    agent = ctx.agent
    out = []
    for ref, decl in get_widgets().items():
        try:
            action = agent.invoker.propose(ToolCall(id=f"w_{ref}", tool_id=decl["method"], params={}))
            if agent.invoker.decide(action) != Decision.AUTO:  # 理论上是 L0 恒 AUTO；防御
                continue
            result = await _offload(
                agent.invoker.execute, action, {},
                {"surface": "widget", "invocation_persistence": "transient"},
            )
            if not result.success:
                log(f"widget {ref} 取数失败（已跳过）：{result.error}")
                continue
            result.panel = ref
            payload = panel_payload(result)
            if payload is not None:
                payload["open"] = decl.get("open")
                out.append(payload)
        except Exception as e:
            log(f"widget {ref} 取数异常（已跳过）：{e}")
    return out


async def _mem_list(ctx) -> list[dict]:
    """记忆管理页数据（OS 感 §4.4）：底座（译宝）+ 各插件命名空间分组列出。单空间失败不拖垮整体。"""
    agent = ctx.agent
    groups = [("译宝", "", agent.user_id)]
    groups.extend((label, ns, f"{ns}:{agent.user_id}") for ns, label in get_mem_namespaces().items())
    out = []
    for label, ns, uid in groups:
        try:
            rows = await _offload(agent.memory.list_all, uid)
        except Exception as e:
            log(f"记忆列出失败（{label}，已跳过）：{e}")
            continue
        for r in rows:
            out.append({"id": r["id"], "text": r["text"], "ns": ns, "label": label,
                        "created_at": r.get("created_at", "")})
    return out
