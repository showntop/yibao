"""runs 调度域（R-13 第二步拆分序 4）：会话槽位与任务调度——取槽/抢占/受理/槽内串行。

从 server.serve_async 原样搬来（2026-08-22）；run_slots 经 RuntimeCtx 注入（同一 dict，
闭包侧与域侧改的是同一份）。_run_ctx（contextvars.ContextVar）经构造参数传原对象——
不能收进 ctx 实例属性：任务级隔离语义（cancel/surface/conversation_id 跨 await 传播），
收进去并发 run 会互相污染。改行为请另开 commit。
"""
from __future__ import annotations

import asyncio
import contextvars
import time

from ..log import log

# 被抢占任务的收尾宽限（秒）：超时强制取消，防 hung 任务把槽位卡死（「点了没反应」的根）
_PREEMPT_GRACE_S = 8.0


class RunsDomain:
    """runs 调度域函数束：_slot/_preempt_* /_schedule_run/_chain_start。

    run/voice_start/panel_action/手机 chat 的受理都走这里；同槽（= 同会话）新话顶旧话，
    跨槽真并行零等待（并发对话 spec §A）。
    """

    def __init__(self, ctx, run_ctx: "contextvars.ContextVar[dict | None]",
                 preempt_grace_s: float = _PREEMPT_GRACE_S) -> None:
        self.ctx = ctx
        self._run_ctx = run_ctx
        # 宽限经构造参数注入（默认本模块常量）：serve_async 传 server 模块级的
        # _PREEMPT_GRACE_S——测试 monkeypatch yibao_brain.server._PREEMPT_GRACE_S
        # 的路径原样生效（同 bridge._ensure_http_token 对 save_settings 的先例）。
        self.preempt_grace_s = preempt_grace_s

    def slot(self, conversation_id: str) -> dict:
        """取（无则建）会话槽位；conversation_id 空 → default 槽。"""
        return self.ctx.run_slots.setdefault(conversation_id or "", {
            "task": None, "cancel": None, "preempt_gen": 0, "surface": None, "running_surface": None})

    def preempt_current(self, slot: dict) -> None:
        slot["preempt_gen"] += 1
        if slot["cancel"] is not None:
            slot["cancel"].set()

    def preempt_if_same_surface(self, slot: dict, surface: str, conv_key: str) -> None:
        """同槽（= 同会话）新请求 → 抢占在跑任务。同会话跨 surface（同一
        conversation_id 从 Home 和 pet 同时发话）也视为同会话，照样抢占（spec §核心模型）。

        仅 default 槽（conv_key 为空 = 无 conversation_id 的遗留调用）保留旧的
        「跨 surface 不抢占、链式排队 + notice」（行为同现状）；跨会话不再走到这里——
        不同 conversation_id 落在不同槽位，直接真并行。

        比较对象是 slot["surface"] = 该槽最近一次受理的 surface（dispatch 时即写入，
        无「chain 任务还没跑起来」的调度竞态）。取舍：default 槽里 A(pet) 在跑、B(panel)
        排队中又来 C(pet) 时，C 会被判成跨 surface 而排队而非顶掉 A——三消息交替跨窗的
        极端场景，排队自愈、不会卡死，不为它引入 per-surface 代数。
        """
        prev = slot["task"]
        if prev is None or prev.done():
            return
        if conv_key or slot["surface"] == surface:
            self.preempt_current(slot)
        else:
            log(f"跨 surface 请求排队（在跑={slot['surface']}，新={surface}）")
            self.ctx.write_msg({"type": "event", "surface": surface, "event": {
                "kind": "notice", "text": "另一个窗口还在说，等它说完就轮到你…"}})

    def schedule_run(self, surface: str, rid, start, conversation_id: str = "") -> None:
        """受理尾巴（run/voice_start/手机 chat 共用）：按 conversation_id 取槽——同槽
        新话顶旧话，跨槽真并行零等待。running_surface 在真正开跑时才写——手机 interrupt
        按它判域，排队窗口不误杀桌面轮。"""
        slot = self.slot(conversation_id)
        self.preempt_if_same_surface(slot, surface, conversation_id or "")
        prev = slot["task"]
        slot["surface"] = surface  # 受理即记录：下次 dispatch 判断同/跨 surface 无调度竞态

        async def _marked(cancel, s=start, sf=surface, sl=slot, ci=conversation_id or ""):
            sl["running_surface"] = sf
            # 归属上下文：batch_confirmer 读本槽 cancel / confirm_meta 记会话归属
            self._run_ctx.set({"cancel": cancel, "surface": sf, "conversation_id": ci})
            await s(cancel)

        slot["task"] = asyncio.ensure_future(
            self.chain_start(slot, prev, _marked, slot["preempt_gen"]))

    async def chain_start(self, slot: dict, prev, start, queued_gen: int) -> None:
        """槽内串行：等本槽上一任务收尾再启动；主循环不在这里阻塞（ping 照答，看门狗
        不误杀）。「等上一任务」只在同槽内生效——跨槽（跨会话）零等待、真并行（spec §A）。

        排队期间本槽又来了更新的请求（preempt_gen 前进）→ 本任务一启动即置 cancel 快速跳过。
        上一任务被抢占后超过 _PREEMPT_GRACE_S 仍不收尾（LLM/TTS hung 等）→ 强制取消，
        槽位必须自愈，否则本槽后续所有请求都静默排队（「点了没反应」）。

        注意：slot["task"] 只在 dispatch 处写入（= 该槽最新受理的 chain）。
        这里绝不能再写——chain 启动晚于 dispatch，旧 chain 后启动会把 task 回写成自己，
        stdin 清理/打断看到的就是已收尾的旧任务，排队中的新任务被孤儿化（2026-07-25
        实测：测试里 asyncio.run 收尾顺手 cancel 孤儿 chain → 偶发丢 final_reply）。
        slot["cancel"] 由这里写（= 本槽当前真正在跑任务的取消闸）：抢占经 gen 代数
        传导，写晚了对齐的是「在跑」语义，不会误伤排队任务。
        """
        if prev is not None and not prev.done():
            t0 = time.monotonic()
            log("新请求排队，等上一任务收尾…")
            try:
                # shield：wait_for 超时不许连带取消 prev，强制取消由我们自己控制
                await asyncio.wait_for(asyncio.shield(prev), timeout=self.preempt_grace_s)
            except asyncio.TimeoutError:
                log(f"上一任务 {self.preempt_grace_s:.0f}s 未收尾，强制取消")
                prev.cancel()
                try:
                    await prev
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass  # prev 自身异常/被取消都算已收尾
            log(f"上一任务收尾完成（{time.monotonic() - t0:.1f}s）")
        cancel = asyncio.Event()
        if slot["preempt_gen"] > queued_gen:
            cancel.set()
        slot["cancel"] = cancel
        try:
            await start(cancel)
        except Exception as e:  # 兜底：任务未预期的异常不能毒死槽位
            log(f"任务异常收尾：{type(e).__name__}: {e}")
