"""mobile/HTTP 域（R-13 第二步拆分序 2）：手机伴生端 API 的受理/打断/确认/状态/取数 + HTTP deps 装配。

从 server.serve_async 原样搬来（2026-08-22）；共享状态与 runs 调度函数经 RuntimeCtx
注入——ctx 上是 serve_async 那同一批对象（同一 dict/deque/闭包），闭包侧与域侧改的是同一份。
改行为请另开 commit。
"""
from __future__ import annotations

import itertools
import time

from ..approvals import _coding_perm_registry, _fulfill_coding_perm
from ..bridge import (
    _bridge_save,
    _conversations_payload,
    _history_payload,
    _reminders_cancel_payload,
    _reminders_list_payload,
    _start_http_api,
)
from ..config import save_settings
from ..http_api import MobileDeps
from ..ipc import Event
from ..log import log

from . import helpers


class MobileDomain:
    """mobile 域函数束：serve_async 构造一次，方法绑给 HTTP deps（MobileDeps 字段签名见 http_api）。

    _MOB_SEQ（手机 run id 序列）随域实例走——与 serve_async 生命周期一致。
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._mob_seq = itertools.count(1)
        self.deps = MobileDeps()  # 未接线形态（HTTP 面关闭时保持全 None → 路由 503）
        self.bridge_server = None

    def submit_run(self, text: str, conversation_id: str) -> dict:
        """手机 /v1/chat 受理：surface=mobile（不抢桌宠，桌宠也不抢手机）；会话槽位
        按 conversation_id 独立——手机与桌面、手机多会话之间真并行。
        不消费 invoke_ctx：那是桌面截图唤起的一次性上下文，留给桌面下一次 run。"""
        ctx = self.ctx
        rid = f"mob_{next(self._mob_seq)}"
        start = lambda c, t=text, r=rid, ci=conversation_id: ctx.drive_run(t, r, c, "mobile", ci)
        log(f"run 受理 rid={rid} surface=mobile conv={conversation_id}：{text[:30]!r}")
        ctx.schedule_run("mobile", rid, start, conversation_id)
        return {"ok": True, "run_id": rid, "conversation_id": conversation_id}

    def interrupt(self, conversation_id: str = "") -> bool:
        """手机打断：维持 surface 域限定（只动 running_surface=mobile 的槽），叠加
        conversation_id 定向（spec §E）——带 id 只打断该会话槽；不带 id 扫所有槽里
        在跑的 mobile 轮（旧行为）。
        task 判活对齐 state()：消掉「上轮收尾后陈旧的 running_surface=mobile」
        误报 True + 平白推进 preempt_gen 的跳窗口。壳 interrupt 是「全都停」或定向
        某槽；手机不该误伤桌面对话。"""
        run_slots = self.ctx.run_slots
        if conversation_id:
            named = run_slots.get(conversation_id)
            slots = [named] if named is not None else []
        else:
            slots = list(run_slots.values())
        for slot in slots:
            task = slot["task"]
            if (slot.get("running_surface") == "mobile"
                    and task is not None and not task.done()
                    and slot["cancel"] is not None):
                self.ctx.preempt_current(slot)
                return True
        return False

    def confirm(self, cid: str, approved: bool, remember: bool) -> bool:
        """与壳 confirm_batch 同路径：兑现 future；confirmer 未注册（SSE 事件先到一步）
        存 early_answers 待兑现。重复点击 → False（404）。

        early_answers 设 32 条上界：真实竞态（SSE 事件先到一步）最多攒 1-2 条，
        存到 32 还没被 confirmer 取走，只可能是未知/垃圾 id——继续存就是无界堆积
        （持有 token 的客户端可无限灌条目），故满员后按未知处理 → False（404）。"""
        ctx = self.ctx
        if cid in ctx.confirm_done:
            return False
        if cid.startswith("perm_"):
            # coding 工具审批：与壳 confirm_batch 同路由（双通道幂等），不占 future/早到缓存；
            # 兑现失败（插件未加载/请求已清理）→ False（404），不假 ok
            if not _fulfill_coding_perm(cid, approved):
                return False
            ctx.confirm_done.append(cid)
            return True
        fut = ctx.pending_confirms.get(cid)
        if fut is not None and not fut.done():
            fut.set_result((approved, remember))
        elif len(ctx.early_answers) < 32:
            ctx.early_answers[cid] = (approved, remember)
        else:
            return False  # 无 future 且早到缓存已满：未知 id，不当真
        ctx.confirm_done.append(cid)
        return True

    def state(self) -> dict:
        """/v1/state 载荷：running + pending。running = 任一槽在跑即非空闲（并发对话
        spec §F：多槽并行后 idle 判定看全局）。pending = confirm_meta 展开（L2 确认，
        条目带 conversation_id/surface 归属）
        + coding 审批只读合并 _PERM 挂起项（allow is None；confirmation_needed 只广播
        不落 confirm_meta，故在此补源）——生命周期随裁决/超时/stop 的 _PERM.pop 收敛，
        无需出队钩子。元素键名对齐 confirm_meta：id/skill_id/summary/risk/created_at。"""
        run_slots = self.ctx.run_slots
        busy = next((s for s in run_slots.values()
                     if s["task"] is not None and not s["task"].done()), None)
        running = {"surface": busy["surface"]} if busy is not None else None
        pending = [{"id": cid, **meta} for cid, meta in self.ctx.confirm_meta.items()]
        perm = _coding_perm_registry()
        if perm:
            for rid, entry in list(perm.items()):
                if isinstance(entry, dict) and entry.get("allow") is None:
                    pending.append({"id": rid, "skill_id": "coding",
                                    "summary": str(entry.get("summary") or entry.get("tool") or "编码审批"),
                                    "risk": 1,
                                    "created_at": int(entry.get("created_at") or 0)})
        return {"running": running, "pending": pending}

    def feed(self, limit: int = 60) -> dict:
        """/v1/feed 载荷（mobile M2）：与桌面 feed IPC 完全同形（items 倒序 + 问候统计 +
        进行中任务）。组装收敛在此，dispatch 的 feed 分支与手机端点共用一份。"""
        running_tasks = helpers._running_tasks(self.ctx)
        return {"items": self.ctx.feed.recent(limit=limit),
                "stats": helpers._feed_stats(self.ctx, running_tasks),
                "running_tasks": running_tasks}

    def register_push(self, registration_id: str, platform: str) -> None:
        """推送设备登记（P4 极光发送消费）。同 registration_id 覆盖，防重复堆积。"""
        settings = self.ctx.settings
        devices = [d for d in (settings.get("push.devices") or [])
                   if d.get("registration_id") != registration_id]
        devices.append({"registration_id": registration_id, "platform": platform, "added_at": int(time.time())})
        settings["push.devices"] = devices
        save_settings({"push.devices": devices})

    async def start_http(self):
        """装配 deps 并启动 aiohttp HTTP 面（扩展桥 + 手机 API）；失败 → None（不拖垮大脑）。

        deps 里的绑定依赖 serve_async 上文已定义的 _drive_run/_schedule_run 等，
        故由 serve_async 在主循环前调用（原注释：启动挪到主循环前）。
        """
        ctx = self.ctx

        async def _http_save(body: dict) -> tuple[int, dict]:
            def _emit(action, result) -> None:
                ev = Event(kind="action_result", action=action.model_dump(mode="json") if hasattr(action, "model_dump") else action,
                           result=result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
                ctx.write_msg({"type": "event", "event": ev.model_dump(mode="json")})
            return await _bridge_save(ctx.agent, _emit, body)

        deps = MobileDeps()
        deps.save = _http_save
        deps.submit_run = self.submit_run
        deps.interrupt = self.interrupt
        deps.confirm = self.confirm
        deps.state = self.state
        deps.register_push = self.register_push
        # 会话只读面（mobile M1）：/v1/conversations、/v1/history 直读 agent.history
        deps.conversations = lambda: _conversations_payload(ctx.agent.history)
        deps.history = lambda cid: _history_payload(ctx.agent.history, cid)

        # 信息浏览面（mobile M2）：feed 与桌面 IPC 同源；reminders 插件直连；memories 复用 _mem_list
        async def _mem_payload() -> dict:
            return {"ok": True, "items": await helpers._mem_list(ctx)}

        deps.feed = self.feed
        deps.reminders_list = lambda: _reminders_list_payload(ctx.agent)
        deps.reminders_cancel = lambda rid: _reminders_cancel_payload(ctx.agent, rid)
        deps.memories = _mem_payload
        self.deps = deps
        self.bridge_server = await _start_http_api(ctx.agent, ctx.settings, ctx.write_msg, deps)
        return self.bridge_server
