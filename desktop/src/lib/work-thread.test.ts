import { describe, expect, it } from "vitest";
import {
  groupPages,
  groupThread,
  isTaskLogBubble,
  isTaskLogEvent,
  isWorkPiece,
  paperErrorNotice,
  paperStamps,
  runAnswer,
  runIsLive,
  runShowFooter,
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
    // 告警行（拒绝/出错）算进当轮工作，不劈开 run
    expect(isWorkPiece(ai("用户拒绝执行 agents.code_exec", { icon: "alert" }))).toBe(true);
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

  it("keeps a rejection alert inside the run instead of splitting it into two avatar groups", () => {
    // 回归：拒绝气泡（icon=alert）曾是 misc，把一轮工作劈成两个头像组
    const bubbles = [
      user("给自己做个插件"),
      ai("先看看插件目录结构。"),
      proc(true),
      ai("用户拒绝执行 agents.code_exec", { icon: "alert" }),
      ai("换个方式，直接搜清单文件。"),
      proc(false),
    ];
    expect(groupThread(bubbles, () => false)).toEqual([
      { type: "user", index: 0 },
      { type: "run", start: 1, indices: [1, 2, 3, 4, 5] },
    ]);
    // 复制/重写只带 AI 正文，不含告警行
    expect(runAnswer(bubbles, [1, 2, 3, 4, 5])).toBe("先看看插件目录结构。\n\n换个方式，直接搜清单文件。");
  });

  it("keeps the run live while a proc is unfinished or a segment is streaming", () => {
    const bubbles = [ai("先搜"), proc(false), ai("半句")];
    expect(runIsLive(bubbles, [0, 1, 2], null)).toBe(true);
    expect(runIsLive([ai("完"), proc(true)], [0, 1], 0)).toBe(true);
    expect(runIsLive([ai("完"), proc(true)], [0, 1], null)).toBe(false);
  });

  it("treats sandbox/agent completion events as task logs, not user reminders", () => {
    expect(isTaskLogEvent({ task: { id: "abc", status: "done" } })).toBe(true);
    expect(isTaskLogEvent({ type: "watch_command" })).toBe(true);
    expect(isTaskLogEvent({ type: "morning_recap" })).toBe(false);
    expect(isTaskLogEvent({})).toBe(false);
    expect(isTaskLogBubble(ai("到点了", { icon: "clock" }))).toBe(false);
    expect(
      isTaskLogBubble(
        ai("✅ 沙箱脚本完成：const { execSync } = require(\"child_process\");\nnpm install OK", {
          icon: "clock",
        }),
      ),
    ).toBe(true);
  });

  it("does not let a task-log reminder split one working turn into two runs", () => {
    const bubbles = [
      user("做一份 PPT"),
      ai("npm 缓存目录有权限问题，改用工作目录内的缓存。"),
      proc(true),
      ai("✅ 沙箱脚本完成：const { execSync } = require(\"child_process\");\nnpm install OK", {
        icon: "clock",
      }),
      ai("依赖装好了。现在写生成脚本。"),
    ];
    expect(groupThread(bubbles, () => false)).toEqual([
      { type: "user", index: 0 },
      { type: "run", start: 1, indices: [1, 2, 4] },
    ]);
  });

  it("hides copy/feedback/rewrite until the latest working turn actually settles", () => {
    const live = [ai("先搜"), proc(true), ai("半句")];
    expect(runShowFooter(live, [0, 1, 2], null, false)).toBe(true);
    expect(runShowFooter(live, [0, 1, 2], null, true)).toBe(false);
    expect(runShowFooter(live, [0, 1, 2], 2, true)).toBe(false);
    expect(runShowFooter([ai("旧答"), user("下一句"), ai("新答"), proc(false)], [0], null, true)).toBe(true);
    expect(runShowFooter([ai("说了半句", { halted: true })], [0], null, true)).toBe(true);
    expect(runShowFooter([proc(true)], [0], null, false)).toBe(false);
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
