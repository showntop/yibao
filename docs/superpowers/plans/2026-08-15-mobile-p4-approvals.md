# 手机伴生端审批页（P4 去推送部分）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 手机端审批页：待批列表实时到达（SSE `confirmation_needed`）+ 批准/拒绝/本会话不再询问，与桌面收件箱同一个真相（P1 的 confirm_meta/future 机制零改动）。

**Architecture:** mobile 新增 `state/approvals.ts`（useApprovals：/v1/state 拉取 + /v1/confirm 兑现 + SSE 帧驱动刷新）+ `views/Approvals.vue` + Chat 头部待批角标；deeplink 加 `yibao://approvals` 路径（推送深链的坑位，P4 推送后直接可用）。服务端唯一改动：confirm_meta 的 summary 从 dict-repr 可读化。

**Spec:** `docs/superpowers/specs/2026-08-14-mobile-companion-design.md` §5 审批页、§4.4 审批闭环（服务端已实现）

## Global Constraints

- 服务端审批机制（pending_confirms/early_answers/confirm_meta/_confirm_mobile）**零改动**——手机只是另一个消费者
- `sidecar/src/yibao_brain/server.py` 工作区有另一会话未提交 WIP：触碰该文件的提交必须 hunk 过滤（只挑含目标关键词的 hunk 暂存），绝不能整文件 add
- mobile 测试 vitest（fake 注入）；确认 decisive 行为的测试先红后绿；中文注释文案
- 本计划两任务可由同一实现者顺序完成（两笔独立提交），合并为一个评审席

---

### Task 1:（server）confirm_meta summary 可读化

**Files:**
- Modify: `sidecar/src/yibao_brain/server.py:654`（summary 字段）
- Test: `sidecar/tests/test_server.py`（若现有用例断言旧 repr 需同步；grep "summary" 找）

**Interfaces:**
- Produces: `confirm_meta[cid]["summary"]` = `k=v, k=v` 形式（params 非空 dict 时，截 120 字），否则 skill_id。旧值是 `str({'path': '/tmp'})` 的 dict-repr。

- [ ] **Step 1: 测试**：test_server.py 追加（沿用 batch_confirmer 已有测试的注入模式，或最小直接构造）——若已有用例覆盖 summary 字段则改其断言为 `path=/tmp/x, force=true` 形式；无则新增一条断言 `confirm_meta` 里 summary 为 k=v 形式。
- [ ] **Step 2: 实现**：:654 改为

```python
                "summary": (", ".join(f"{k}={v}" for k, v in (getattr(action, "params", None) or {}).items())[:120]
                            if isinstance(getattr(action, "params", None), dict) and action.params else skill_id),
```

（实现者可按仓内风格提取小函数；保证 params 为空 dict 时回落 skill_id。）
- [ ] **Step 3: 全量 `uv run --extra dev pytest -x -q` 绿 → hunk 过滤提交**（挑含 `summary` 关键词的 hunk；暂存区核对后 commit `fix(server): 审批摘要可读化——dict-repr 改 k=v 形式`）。

---

### Task 2:（mobile）审批页 + 待批角标 + 深链路径

**Files:**
- Create: `mobile/src/state/approvals.ts`、`mobile/src/state/approvals.test.ts`
- Modify: `mobile/src/router.ts`（+/approvals 路由）
- Create: `mobile/src/views/Approvals.vue`
- Modify: `mobile/src/state/chat.ts`（pendingCount + confirmation_needed 监听）
- Modify: `mobile/src/views/Chat.vue`（头部角标按钮）
- Modify: `mobile/src/deeplink.ts` + `deeplink.test.ts`（yibao://approvals 路径）
- Modify: `mobile/src/App.vue`（深链分流）

**Interfaces:**
- Consumes: P1 服务端 `GET /v1/state → {ok, running, pending:[{id,skill_id,summary,risk,created_at}]}`；`POST /v1/confirm {id,approved,remember} → 200|404`；SSE 帧 `confirmation_needed`（广播，无 surface 信封——refresh 全量拉取为准，不依赖帧内容）
- Produces:
  - `useApprovals(conn, stream, fetchImpl=fetch)` → `{ pendings: Ref<PendingConfirm[]>, loading, error, refresh(): Promise<void>, decide(id, approved, remember): Promise<"ok"|"gone"> }`；构造时 `stream.on("confirmation_needed", () => void refresh())`
  - `PendingConfirm = { id; skill_id; summary; risk; created_at }`
  - useChat 增 `pendingCount: Ref<number>`：构造时拉一次 /v1/state 计数，`confirmation_needed` 帧 +1，`run_done` 不动；进入审批页返回后由 Chat onMounted 重新拉（新增 `syncPendingCount()`）
  - deeplink：`parseDeepPath(u): {kind:"pair", host, token} | {kind:"approvals"} | null`（重构 parsePairUrl 复用；approvals 判 `yibao://approvals`）

- [ ] **Step 1: approvals.test.ts 先红**（fake fetch + 直传 fake stream——on(kind, fn) 注册表模式照 chat.test.ts）：

```typescript
import { describe, expect, it, vi } from "vitest";
import { useApprovals } from "./approvals";
import type { EventSourceLike } from "../api/events";

function fakeStream() {
  const reg = new Map<string, Set<(d: any) => void>>();
  return {
    es: { addEventListener: () => {}, close: () => {}, onopen: null, onerror: null } as EventSourceLike,
    on: (k: string, fn: (d: any) => void) => { if (!reg.has(k)) reg.set(k, new Set()); reg.get(k)!.add(fn); },
    emit: (k: string) => reg.get(k)?.forEach((f) => f({})),
  };
}

describe("useApprovals", () => {
  it("refresh 拉取 pending；decide ok 后刷新；404 返回 gone", async () => {
    const st = fakeStream();
    const state = { calls: 0, confirmStatus: 200 };
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/v1/state")) {
        state.calls++;
        return new Response(JSON.stringify({ ok: true, running: null,
          pending: state.calls === 1 ? [{ id: "pa_1", skill_id: "code_exec", summary: "cmd=rm x", risk: 3, created_at: 1 }] : [] }), { status: 200 });
      }
      return new Response("{}", { status: state.confirmStatus });
    });
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    await a.refresh();
    expect(a.pendings.value).toHaveLength(1);
    expect(await a.decide("pa_1", true, false)).toBe("ok");
    expect(a.pendings.value).toHaveLength(0); // decide 内部 refresh
    state.confirmStatus = 404;
    expect(await a.decide("pa_9", true, false)).toBe("gone"); // 桌面已处理/过期
  });

  it("confirmation_needed 帧驱动自动刷新", async () => {
    const st = fakeStream();
    const fetchImpl = vi.fn(async () => new Response(
      JSON.stringify({ ok: true, running: null, pending: [{ id: "pa_2", skill_id: "s", summary: "x", risk: 2, created_at: 2 }] }), { status: 200 }));
    const a = useApprovals({ host: "http://x", token: "t" } as never, st as never, fetchImpl as never);
    st.emit("confirmation_needed"); // 桌面发起的新待批
    await new Promise((r) => setTimeout(r, 0));
    expect(a.pendings.value).toHaveLength(1);
  });
});
```

- [ ] **Step 2: approvals.ts 实现**（decide：POST /v1/confirm；200→"ok"；404→"gone"；其他/异常→error ref + "gone"；两种结果都 void refresh()）。
- [ ] **Step 3: deeplink 重构 + 测试先红再绿**：`parseDeepPath` 覆盖 pair/approvals/未知；App.vue 分流（pair→handlePairUrl 原逻辑；approvals→router.replace("/approvals")；原生守卫不变）。
- [ ] **Step 4: Approvals.vue + 路由 + Chat 角标**：
  - 路由 `{ path: "/approvals", component: () => import("./views/Approvals.vue") }`（守卫自动覆盖）
  - Approvals.vue：自建 `useEventStream`（进入 start/离开 stop，与 Chat 同模式）+ useApprovals；卡片列表（skill_id/risk 徽章/summary/时间），每卡「拒绝」「批准」+「本会话不再询问」checkbox（默认关，随 decide 的 remember 传）；顶部返回按钮回 /chat；空态文案「没有待审批的事项」；"gone" 时提示「该审批已在桌面处理，列表已刷新」
  - chat.ts：pendingCount + syncPendingCount()（GET /v1/state 数 pending 长度）；confirmation_needed 帧 +1；chat.test.ts 补一条（帧到达计数、sync 重置）
  - Chat.vue 头部加角标按钮：`<router-link to="/approvals">⏳ {{ chat.pendingCount.value }}</router-link>`（0 时隐藏）；onMounted 调 syncPendingCount()（从审批页返回也会重跑）
- [ ] **Step 5: 验证**：`cd mobile && pnpm test && pnpm build` 绿 → 提交 `feat(mobile): 审批页——SSE 实时待批/批准/拒绝/角标 + yibao://approvals 深链坑位`
- [ ] **Step 6: 浏览器真链路冒烟（尽力而为）**：sidecar + pnpm dev 起着，对话页发「帮我创建 /tmp/yibao-approve-test.txt 再删除它」——若风险闸门挂起（L3 code_exec 走确认），角标 +1、审批页出现卡片、批准后桌面继续执行并播报。若该 prompt 未触发确认路径（LLM 行为不确定），记录后跳过，留用户真机验收（桌面随便让译宝干件需要确认的事）。

---

## 验收

- [ ] sidecar 全量 pytest 绿（summary 可读化用例）
- [ ] mobile pnpm test/build 绿（approvals 2 + deeplink 3 + chat 1 新用例）
- [ ] 浏览器或真机：桌面触发 L3 → 手机角标 +1 → 审批页批准 → 桌面继续跑（用户验收）
