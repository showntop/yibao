import { describe, expect, it } from "vitest";
import { gateItemsFor } from "./gate";
import type { PendingConfirm } from "../../protocol/brain-types";

function item(patch: Partial<PendingConfirm>): PendingConfirm {
  return { id: "a1", tool_id: "project.create", label: "立项", desc: "", ...patch };
}

describe("gateItemsFor 归属过滤", () => {
  it("同会话的 pet 卡可见", () => {
    const list = gateItemsFor([item({ surface: "pet", conversationId: "cv1" })], "cv1");
    expect(list).toHaveLength(1);
  });

  it("别会话的卡不可见", () => {
    const list = gateItemsFor([item({ surface: "pet", conversationId: "cv2" })], "cv1");
    expect(list).toHaveLength(0);
  });

  it("非 pet surface 的卡不可见", () => {
    const list = gateItemsFor([item({ surface: "panel", conversationId: "cv1" })], "cv1");
    expect(list).toHaveLength(0);
  });

  it("无归属信息的卡保持可见（宁可重复不可漏达）", () => {
    expect(gateItemsFor([item({})], "cv1")).toHaveLength(1);
    expect(gateItemsFor([item({ conversationId: "cv1" })], "")).toHaveLength(1);
  });

  it("顺序保持", () => {
    const list = gateItemsFor(
      [item({ id: "a", conversationId: "cv1" }), item({ id: "b", conversationId: "cv1" })],
      "cv1",
    );
    expect(list.map((i) => i.id)).toEqual(["a", "b"]);
  });
});
