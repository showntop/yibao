import { describe, expect, it } from "vitest";
import { horizonEcho, horizonNodes } from "./horizon.ts";
import type { FeedItem } from "../brain";

function feed(partial: Partial<FeedItem> & Pick<FeedItem, "id" | "text">): FeedItem {
  return {
    ts: 1,
    kind: "reminder",
    read: 0,
    status: "none",
    ...partial,
  };
}

/** 2026-08-28 当天某时刻的 epoch ms（本地时区）。 */
function at(hhmm: string): number {
  return new Date(`2026-08-28T${hhmm}:00`).getTime();
}

describe("horizonNodes", () => {
  it("returns empty for no items", () => {
    expect(horizonNodes([], at("09:00"))).toEqual([]);
  });

  it("sorts nodes by time ascending with HH:MM labels", () => {
    const nodes = horizonNodes(
      [feed({ id: 2, text: "晚的", ts: at("08:30") }), feed({ id: 1, text: "早的", ts: at("08:24") })],
      at("09:00"),
    );
    expect(nodes.map((n) => n.label)).toEqual(["08:24", "08:30"]);
    expect(nodes.map((n) => n.id)).toEqual([1, 2]);
  });

  it("marks unread items hot, read items dim", () => {
    const nodes = horizonNodes(
      [
        feed({ id: 1, text: "未读", ts: at("08:24"), read: 0 }),
        feed({ id: 2, text: "已读", ts: at("08:26"), read: 1 }),
      ],
      at("09:00"),
    );
    expect(nodes.map((n) => n.hot)).toEqual([true, false]);
  });

  it("excludes items the user chose to ignore", () => {
    const nodes = horizonNodes(
      [
        feed({ id: 1, text: "忽略", ts: at("08:24"), status: "ignore" }),
        feed({ id: 2, text: "保留", ts: at("08:26") }),
      ],
      at("09:00"),
    );
    expect(nodes.map((n) => n.id)).toEqual([2]);
  });

  it("keeps only the most recent nodes when over budget", () => {
    const items = [1, 2, 3, 4, 5, 6, 7, 8].map((i) =>
      feed({ id: i, text: `第${i}条`, ts: at(`0${i}:00` as `${number}:00`) }),
    );
    const nodes = horizonNodes(items, at("09:00"), 5);
    expect(nodes).toHaveLength(5);
    expect(nodes.map((n) => n.id)).toEqual([4, 5, 6, 7, 8]);
  });
});

describe("horizonEcho", () => {
  it("renders a running action with tone busy", () => {
    expect(horizonEcho({ state: "work", proc: { label: "zimeiti.rewrite", done: false } })).toEqual({
      text: "zimeiti.rewrite …",
      tone: "busy",
    });
  });

  it("renders a completed action with ✓ and tone ok", () => {
    expect(horizonEcho({ state: "success", proc: { label: "reminders.set", done: true, ok: true } })).toEqual({
      text: "reminders.set ✓",
      tone: "ok",
    });
  });

  it("renders a failed action with ✗ and amber tone (红只留给不可逆外发)", () => {
    expect(horizonEcho({ state: "error", proc: { label: "wewrite.publish", done: true, ok: false } })).toEqual({
      text: "wewrite.publish ✗",
      tone: "warn",
    });
  });

  it("shows nothing when there is no action at all", () => {
    expect(horizonEcho({ state: "idle", proc: null })).toBeNull();
    expect(horizonEcho({ state: "say", proc: null })).toBeNull();
  });

  it("keeps the last completed echo visible once back to idle (回显不是瞬时 toast)", () => {
    expect(horizonEcho({ state: "idle", proc: { label: "editor.replace_range", done: true, ok: true } })).toEqual({
      text: "editor.replace_range ✓",
      tone: "ok",
    });
  });
});
