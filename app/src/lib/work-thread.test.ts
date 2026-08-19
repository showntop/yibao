import { describe, expect, it } from "vitest";
import {
  groupThread,
  isWorkPiece,
  runAnswer,
  runIsLive,
  runTailIndex,
  type WorkBubble,
} from "./work-thread";

function ai(text: string, extra: Partial<WorkBubble> = {}): WorkBubble {
  return { role: "ai", text, ...extra };
}
function user(text: string): WorkBubble {
  return { role: "user", text };
}
function proc(done = false): WorkBubble {
  return { role: "sys", text: "", proc: { done } };
}

describe("work-thread", () => {
  it("treats proc and plain ai as one work piece, not reminders or panel links", () => {
    expect(isWorkPiece(ai("查一下"))).toBe(true);
    expect(isWorkPiece(proc())).toBe(true);
    expect(isWorkPiece(user("hi"))).toBe(false);
    expect(isWorkPiece(ai("到点了", { icon: "clock" }))).toBe(false);
    expect(isWorkPiece(ai("⇢ 协作", { panelLink: true }))).toBe(false);
  });

  it("groups a turn of text and tools into one run", () => {
    const bubbles = [
      user("调研 skill"),
      ai("我先搜一圈。"),
      proc(true),
      proc(true),
      ai("找到这些。"),
    ];
    const thread = groupThread(bubbles, () => false);
    expect(thread).toEqual([
      { type: "user", index: 0 },
      { type: "run", start: 1, indices: [1, 2, 3, 4] },
    ]);
    expect(runAnswer(bubbles, [1, 2, 3, 4])).toBe("我先搜一圈。\n\n找到这些。");
    expect(runTailIndex(bubbles, [1, 2, 3, 4])).toBe(4);
    expect(runIsLive(bubbles, [1, 2, 3, 4], null)).toBe(false);
  });

  it("keeps the run live while a proc is unfinished or a segment is streaming", () => {
    const bubbles = [ai("先搜"), proc(false), ai("半句")];
    expect(runIsLive(bubbles, [0, 1, 2], null)).toBe(true);
    expect(runIsLive([ai("完"), proc(true)], [0, 1], 0)).toBe(true);
    expect(runIsLive([ai("完"), proc(true)], [0, 1], null)).toBe(false);
  });

  it("splits runs at the next user turn and inserts day markers", () => {
    const bubbles = [user("a"), ai("一"), user("b"), ai("二")];
    const thread = groupThread(bubbles, (i) => i === 2);
    expect(thread.map((t) => t.type)).toEqual(["user", "run", "day", "user", "run"]);
  });
});
