"""coding 插件工具审批兑现（从 server.py 拆出）：壳 confirm_batch 与手机 /v1/confirm 双通道共用的 _PERM 路由。

纪律：本模块的函数从 server.py 原样搬来，不改逻辑；改行为请另开 commit。
"""
from __future__ import annotations

import sys


def _coding_perm_registry() -> dict | None:
    """coding 插件 can_use_tool 的 _PERM 注册表（_sibling 加载的模块单例
    sys.modules["yibao_plugin_coding__runner"] 上）；插件未加载/形态不对 → None。"""
    mod = sys.modules.get("yibao_plugin_coding__runner")
    perm = getattr(mod, "_PERM", None)
    return perm if isinstance(perm, dict) else None


def _fulfill_coding_perm(cid: str, approved: bool) -> bool:
    """coding 插件 can_use_tool 审批兑现：cid 以 "perm_" 开头的确认路由进插件 _PERM 注册表
    （写 allow + set 等待事件）。与 coding.decide 双通道幂等——先到先得，后到不覆盖；
    插件未加载 / 请求已超时清理 → False（无害，等待方 60s 超时 deny 兜底）。

    coding 审批不经 batch_confirmer 的 future：runner 线程在 threading.Event 上等。
    """
    perm = _coding_perm_registry()
    if perm is None:
        return False
    entry = perm.get(cid)
    if not isinstance(entry, dict):
        return False
    if entry.get("allow") is None:
        entry["allow"] = bool(approved)
    event = entry.get("event")
    if event is not None:
        event.set()
    return True
