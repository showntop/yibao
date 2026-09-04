// @vitest-environment happy-dom
// 待批准队列：旁路（手机/HTTP 桥）批准的裁决经 confirmation_resolved 即时出队——
// 不再等工具执行完的 action_result（长工具下门卡曾残留整个执行期，收件箱计数同步滞留）。
import { describe, expect, it } from "vitest";
import { handlePendingBrainEvent, onPendingConfirms } from "./pending";
import type { PendingConfirm } from "../lib/brain";

describe("pending 确认队列", () => {
  it("confirmation_resolved 按 action_ids 即时出队", () => {
    const seen: PendingConfirm[][] = [];
    const last = () => seen[seen.length - 1];
    const ids = (l: PendingConfirm[] | undefined) => (l ?? []).map((p: PendingConfirm) => p.id);
    const off = onPendingConfirms((l) => seen.push(l));
    handlePendingBrainEvent({ kind: "confirmation_needed", action: { id: "act_1", tool_id: "zimeiti.render_save" } });
    handlePendingBrainEvent({ kind: "confirmation_needed", action: { id: "act_2", tool_id: "zimeiti.render_save" } });
    expect(ids(last())).toEqual(["act_1", "act_2"]);
    handlePendingBrainEvent({ kind: "confirmation_resolved", action_ids: ["act_1"] });
    expect(ids(last())).toEqual(["act_2"]);
    handlePendingBrainEvent({ kind: "confirmation_resolved", action_ids: ["act_2"] });
    expect(last()).toEqual([]);
    off();
  });
});
