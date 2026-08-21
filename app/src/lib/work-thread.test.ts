import { describe, expect, it } from "vitest";
import {
  groupPages,
  groupThread,
  isWorkPiece,
  paperErrorNotice,
  paperStamps,
  runAnswer,
  runIsLive,
  runTailIndex,
  talkTurns,
  talkBeats,
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
    expect(isWorkPiece(ai("已请 改登录", { panelLink: true }))).toBe(false);
    expect(isWorkPiece({ role: "sys", text: "已走 改登录" })).toBe(false);
    expect(isWorkPiece(ai("摊开 闪念盘"))).toBe(false);
    expect(isWorkPiece(ai("用了 工具箱"))).toBe(false);
    expect(isWorkPiece({ role: "sys", text: "收起 闪念盘" })).toBe(false);
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

  it("binds a user turn and the following run into one page, reminders onto that page", () => {
    const bubbles = [
      user("调研 skill"),
      ai("找到这些。"),
      proc(true),
      ai("到点了", { icon: "clock" }),
    ];
    expect(groupPages(bubbles)).toEqual([
      { userIndex: 0, runIndices: [1, 2], miscIndices: [3] },
    ]);
  });

  it("keeps a reminder-only stream as a duty page", () => {
    expect(groupPages([ai("该开战会了", { icon: "clock" })])).toEqual([
      { userIndex: null, runIndices: [], miscIndices: [0] },
    ]);
  });

  it("does not pile orphan 已请/已走 lines onto the paper", () => {
    const bubbles = [
      ai("已走 notes · 闪念盘 · 闪念列表"),
      ai("已走 agents · 智能体调度 · 任务列表"),
      ai("该开战会了", { icon: "clock" }),
      ai("已请 改登录", { panelLink: true }),
      { role: "sys" as const, text: "已走 改登录" },
      { role: "sys" as const, text: "收起 闪念盘" },
    ];
    expect(groupPages(bubbles)).toEqual([
      { userIndex: null, runIndices: [], miscIndices: [2, 3] },
    ]);
  });

  it("dedupes paper stamps and keeps at most three", () => {
    expect(paperStamps(["续源网页", "续源网页", "联网搜索", "读 readme", "多余"])).toEqual([
      "续源网页",
      "联网搜索",
      "读 readme",
    ]);
    expect(paperStamps(["", "  "])).toEqual([]);
  });

  it("summarizes brain errors for paper, keeping the raw payload as detail", () => {
    const raw = "大脑出错：Error code: 402 - {'error': {'message': 'Insufficient Balance'}}";
    expect(paperErrorNotice(raw)).toEqual({
      summary: "大脑暂时没额度",
      detail: raw,
    });
    expect(paperErrorNotice("该开战会了")).toBeNull();
    expect(paperErrorNotice("大脑出错：网络中断")).toEqual({
      summary: "网络中断",
      detail: "大脑出错：网络中断",
    });
  });

  it("splits runs at the next user turn and inserts day markers", () => {
    const bubbles = [user("a"), ai("一"), user("b"), ai("二")];
    const thread = groupThread(bubbles, (i) => i === 2);
    expect(thread.map((t) => t.type)).toEqual(["user", "run", "day", "user", "run"]);
  });

  it("keeps only the last spoken lines for salon, skipping tools and notices", () => {
    const bubbles = [
      user("早"),
      ai("早。"),
      proc(true),
      ai("到点了", { icon: "clock" }),
      user("下一句"),
      ai("好。"),
      user("三"),
      ai("四"),
    ];
    expect(talkTurns(bubbles, 4).map((i) => bubbles[i].text)).toEqual(["下一句", "好。", "三", "四"]);
    expect(talkTurns(bubbles, 0)).toEqual([]);
  });

  it("cuts a long salon reply into visual-novel beats", () => {
    const text = "好，继续深挖。\n\n**第一梯队**综合巨无霸。内置 Python 推荐器。\n\n第二梯队品味向。";
    const beats = talkBeats(text, 24);
    expect(beats[0]).toContain("好，继续深挖。");
    expect(beats.length).toBeGreaterThan(1);
    expect(beats.some((beat) => beat.includes("第一梯队"))).toBe(true);
    expect(beats.join("")).not.toContain("**");
  });
});
