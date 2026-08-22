"""HTTP 桥与移动直连载荷（从 server.py 拆出）：扩展桥 /save、手机 /v1/* 的直达调用与只读载荷组装。

纪律：本模块的函数从 server.py 原样搬来，不改逻辑；改行为请另开 commit。
"""
from __future__ import annotations

from .log import log
import asyncio
import itertools
import sys

from .config import http_port
from .llm import ToolCall
from .loop import AgentLoop, _offload
from .plugins import get_api
from .safety import Decision


# ---------- 浏览器扩展桥（127.0.0.1 微 HTTP → zimeiti quiet 直调）----------


def _pick_en_ip(ifconfig_out: str) -> str:
    """从 ifconfig 输出挑物理网卡（en*）的第一个私网 IPv4；纯函数便于测试。
    跳过 169.254（自配链路本地）。为什么不用 UDP connect 挑默认路由：公司 VPN
    （utun）常接管默认路由，挑出来的是 VPN 隧道地址，手机根本够不着。"""
    import ipaddress
    import re

    cur = ""
    for ln in ifconfig_out.splitlines():
        m = re.match(r"^(\w+):", ln)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"\s+inet (\d+\.\d+\.\d+\.\d+)", ln)
        if m and cur.startswith("en"):
            ip = m.group(1)
            try:
                if ipaddress.ip_address(ip).is_private and not ip.startswith("169.254."):
                    return ip
            except ValueError:
                continue
    return ""


def _lan_ip() -> str:
    """内网 IPv4（配对 URL 用）：优先物理网卡（en* = WiFi/以太网）的私网地址；
    找不到（无 WiFi 之类）再退 UDP connect 路由选择（可能命中 utun，聊胜于无）。"""
    import socket
    import subprocess

    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2).stdout
    except Exception:
        out = ""
    ip = _pick_en_ip(out)
    if ip:
        return ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def _ensure_http_token(settings: dict, key: str) -> str:
    """HTTP 面共享 token（http.token=扩展桥 / http.mobile_token=手机伴生端）：
    空则生成并持久化（save_settings 只落已知键，两键均已在默认表）。"""
    # save_settings 经 server 模块间接取，保留 yibao_brain.server.save_settings 的
    # monkeypatch 路径（test_bridge.test_ensure_http_token_* 依赖该 patch 点，
    # 与 background._dock_list 同款先例）。
    from . import server as _server

    tok = str(settings.get(key) or "")
    if not tok:
        import secrets

        tok = secrets.token_hex(16)
        _server.save_settings({key: tok})
        settings[key] = tok
    return tok


_BRIDGE_SEQ = itertools.count(1)  # 桥/分享保存的 action id 序（跨调用唯一）

async def _bridge_save(agent: AgentLoop, emit, body: dict) -> tuple[int, dict]:
    """存素材/选题核心（扩展桥 /save 与手机 /v1/save 共用；原 _make_bridge_route._route 主体）。
    emit(action, result)：回执出口（经 EventTap → stdio 壳 + SSE 手机）。"""
    url = str(body.get("url") or "").strip()
    title = str(body.get("title") or "").strip()[:200]
    text = str(body.get("text") or "").strip()[:20000]
    mode = str(body.get("mode") or "material")
    if not text:
        return 400, {"ok": False, "error": "text 为空"}
    if mode == "material":
        api_name = "zimeiti.invoke_mat_save"
        # 先存后整理：defer 跳过 LLM 摘要立刻落库（秒回），mat_enrich 后台补元数据
        params = {"url": url, "text": f"{title}\n\n{text}" if title else text, "title": title, "defer": True}
    elif mode == "topic":
        api_name = "zimeiti.invoke_add_topic"
        params = {"title": title or text[:30], "source": url or "浏览器扩展"}
    else:
        return 400, {"ok": False, "error": f"未知 mode：{mode}"}
    api = get_api(api_name)
    if api is None or not api.direct:
        return 500, {"ok": False, "error": f"方法不可用：{api_name}"}
    rid = f"http_{next(_BRIDGE_SEQ)}"
    action = agent.invoker.propose(ToolCall(id=f"pa_{rid}", skill_id=api.handler, params=params))
    action.id = f"pa_{rid}"  # 壳侧靠 pa_ 前缀认领回执（与 panel_action 同协议）
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    decision = agent.invoker.decide(action)
    if decision != Decision.AUTO:
        return 403, {"ok": False, "error": "策略要求确认或禁止（桥场景无确认通道），未执行"}
    result = await _offload(agent.invoker.execute, action, params)
    emit(action, result)
    if not result.success:
        return 500, {"ok": False, "error": result.error or "执行失败"}
    data = result.data or {}
    if mode == "material" and data.get("pending"):
        asyncio.ensure_future(_enrich_later(agent, data.get("id")))
    return 200, {"ok": True, "title": data.get("title", title)}


async def _enrich_later(agent: AgentLoop, material_id: str | None) -> None:
    """先存后整理的后半拍：LLM 摘要/标签后台补写。失败静默——素材本体已在库，即席元数据可用。"""
    if not material_id:
        return
    try:
        action = agent.invoker.propose(
            ToolCall(id=f"pa_enrich_{material_id}", skill_id="zimeiti.mat_enrich", params={"id": material_id})
        )
        action.id = f"pa_enrich_{material_id}"
        if agent.invoker.decide(action) != Decision.AUTO:
            return
        await _offload(agent.invoker.execute, action, {"id": material_id})
    except Exception as e:
        log(f"素材后台精整失败（已跳过）：{e}")


async def _start_http_api(agent: AgentLoop, settings: dict, tap, deps) -> "object | None":
    """起 aiohttp HTTP 面（扩展桥 + 移动 API）；失败 → stderr + None（不拖垮大脑）。"""
    try:
        from .http_api import build_app, run_server


        # token 兜底生成后传"现取闭包"：桌面重置 token（settings_set）后无需重启面
        _ensure_http_token(settings, "http.token")
        _ensure_http_token(settings, "http.mobile_token")
        app = build_app(
            get_bridge_token=lambda: str(settings.get("http.token") or ""),
            get_mobile_token=lambda: str(settings.get("http.mobile_token") or ""),
            tap=tap,
            deps=deps,
        )
        bind = str(settings.get("http.bind") or "127.0.0.1")
        runner = await run_server(app, bind, http_port())
        log(f"HTTP 面（桥+移动 API）已监听 {bind}:{http_port()}")
        return runner
    except Exception as e:
        log(f"HTTP 面启动失败（{e}，已禁用）")
        return None


def _conversations_payload(history) -> dict:
    """/v1/conversations 载荷（mobile M1）：桶摘要列表。
    history 未启用（agent.history=None，测试态/无 history_file）→ 空列表，不 503。"""
    if history is None:
        return {"ok": True, "items": []}
    return {"ok": True, "items": history.conversations()}


def _history_payload(history, conversation_id: str) -> dict:
    """/v1/history 载荷（mobile M1）：单桶消息平铺成 {role, text}；
    conversation_id 缺省 → default 桶。history 未启用 → 空列表。"""
    if history is None:
        return {"ok": True, "items": []}
    return {"ok": True, "items": [{"role": m.get("role"), "text": m.get("content")}
                                  for m in history.messages(conversation_id or None)]}


async def _reminders_call(agent: AgentLoop, api_name: str, params: dict) -> dict:
    """reminders 直连核心（mobile M2，_bridge_save 同款）：get_api → propose →
    api.risk 只许收紧 → decide==AUTO → 线程池执行。浏览场景无确认通道，非 AUTO 即拒。
    返回 {"ok", "data"?/"error"?}——降级策略（list 空列表 / cancel 带 error）由调用方定。"""
    api = get_api(api_name)
    if api is None or not api.direct:
        return {"ok": False, "error": f"方法不可用：{api_name}"}
    rid = f"pa_mob_{next(_BRIDGE_SEQ)}"
    action = agent.invoker.propose(ToolCall(id=rid, skill_id=api.handler, params=params))
    action.id = rid  # propose 不透传 ToolCall.id（Action 另起 act_ 号）——回填 pa_mob_ 前缀，壳侧审计可区分手机发起（与 _bridge_save 同协议）
    if api.risk is not None:
        action.risk = max(action.risk, api.risk)
    if agent.invoker.decide(action) != Decision.AUTO:
        return {"ok": False, "error": "策略要求确认或禁止（手机浏览场景无确认通道），未执行"}
    result = await _offload(agent.invoker.execute, action, params)
    if not result.success:
        return {"ok": False, "error": result.error or "执行失败"}
    return {"ok": True, "data": result.data or {}}


async def _reminders_list_payload(agent: AgentLoop) -> dict:
    """/v1/reminders 载荷（mobile M2）：reminders.list 直连，rows → items。
    插件缺席/策略拦/执行失败/异常 → 空列表不 500（浏览宁空勿炸）。"""
    try:
        out = await _reminders_call(agent, "reminders.list", {})
        rows = out.get("data", {}).get("rows") if out.get("ok") else None
        return {"ok": True, "items": rows or []}
    except Exception as e:
        log(f"提醒列出失败（已降级空列表）：{e}")
        return {"ok": True, "items": []}


async def _reminders_cancel_payload(agent: AgentLoop, rid: str) -> dict:
    """/v1/reminders/cancel 载荷（mobile M2）：reminders.cancel 直连。
    成功 {"ok": True}；失败/异常 {"ok": False, "error"}（路由层转 500）。"""
    try:
        out = await _reminders_call(agent, "reminders.cancel", {"id": rid})
        if not out.get("ok"):
            return {"ok": False, "error": out.get("error") or "取消失败"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": f"取消提醒失败：{e}"}
