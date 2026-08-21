// @vitest-environment happy-dom
import { describe, expect, it } from "vitest";
import {
  deskAskLine,
  unmatchedDeskPath,
  deskPathOpen,
  deskPathClose,
  isDeskPathBounce,
  isDeskPathCloseLine,
  isDeskPathOpenLine,
  shouldStampDeskPath,
  deskKind,
  deskLeaveLine,
  isDeskLivePlugin,
  isResumeDeskWork,
  setDeskOrigin,
  takeDeskOrigin,
} from "./home-desk-presence";

describe("deskAskLine", () => {
  it("names a first 委派 with one human name, not plugin · title · panel", () => {
    expect(deskAskLine({ plugin: "coding", title: "studio", objectTitle: "改登录" }))
      .toBe("已请 改登录");
    expect(deskAskLine({ plugin: "notes", title: "闪念盘", objectTitle: "闪念列表" }))
      .toBe("已请 闪念盘");
    expect(deskAskLine({ plugin: "agents", title: "智能体调度 · 任务列表" }))
      .toBe("已请 智能体调度");
    expect(deskAskLine({ plugin: "coding", title: "coding" })).toBe("已请 coding");
  });
});

describe("deskLeaveLine", () => {
  it("closes the same card, still one name", () => {
    expect(deskLeaveLine({ plugin: "notes", title: "闪念盘", objectTitle: "闪念列表" }))
      .toBe("已走 闪念盘");
    expect(deskLeaveLine({ plugin: "coding", title: "coding" })).toBe("已走 coding");
  });
});

describe("deskPath lines", () => {
  it("writes an execution trail without calling every surface a hired worker", () => {
    const notes = { plugin: "notes", title: "闪念盘" };
    const box = { plugin: "toolbox", title: "工具箱" };
    const job = { plugin: "coding", title: "studio", objectTitle: "改登录" };
    expect(deskPathOpen("worker", job)).toBe("已请 改登录");
    expect(deskPathClose("worker", job)).toBe("已走 改登录");
    expect(deskPathOpen("host", notes)).toBe("摊开 闪念盘");
    expect(deskPathClose("host", notes)).toBe("收起 闪念盘");
    expect(deskPathOpen("tool", box)).toBe("用了 工具箱");
    expect(deskPathClose("tool", box)).toBe("收起 工具箱");
  });
});

describe("desk path bounce", () => {
  const notes = { plugin: "notes", title: "闪念盘" };
  const box = { plugin: "toolbox", title: "工具箱" };

  it("does not stamp a second print for the same surface with no talk in between", () => {
    expect(isDeskPathOpenLine("摊开 闪念盘")).toBe(true);
    expect(isDeskPathCloseLine("收起 闪念盘")).toBe(true);
    expect(isDeskPathCloseLine("已走 改登录")).toBe(true);
    expect(isDeskPathBounce(notes, notes, [])).toBe(true);
    expect(shouldStampDeskPath(null, notes, notes, [])).toBe(false);
  });

  it("stamps again after a user turn or a work piece", () => {
    expect(shouldStampDeskPath(null, notes, notes, [{ role: "user", text: "记一条" }])).toBe(true);
    expect(shouldStampDeskPath(null, notes, notes, [{ role: "ai", text: "记下了" }])).toBe(true);
  });

  it("stamps when the object changed", () => {
    expect(shouldStampDeskPath(null, notes, box, [])).toBe(true);
  });

  it("does not stamp while the same workstation is still open", () => {
    expect(shouldStampDeskPath(notes, notes, notes, [])).toBe(false);
  });
});

describe("unmatchedDeskPath", () => {
  it("keeps an open trail until the matching close, so both stay in the ledger", () => {
    expect(unmatchedDeskPath(["摊开 闪念盘"])).toBe("摊开 闪念盘");
    expect(unmatchedDeskPath(["摊开 闪念盘", "收起 闪念盘"])).toBeNull();
    expect(unmatchedDeskPath(["已请 改登录", "已走 改登录", "用了 工具箱"])).toBe("用了 工具箱");
  });
});

describe("deskKind", () => {
  it("splits 无脑工具 / 译宝脑 / 工人脑", () => {
    expect(deskKind("coding")).toBe("worker");
    expect(deskKind("notes", "inherit")).toBe("host");
    expect(deskKind("agents")).toBe("host");
    expect(deskKind("toolbox")).toBe("tool");
    expect(deskKind("notes", "none")).toBe("tool");
  });
});

describe("isResumeDeskWork", () => {
  it("skips a second stamp when restoring the same panel", () => {
    const a = { panel: "coding:studio", plugin: "coding", title: "studio" };
    expect(isResumeDeskWork(null, a)).toBe(false);
    expect(isResumeDeskWork(a, a)).toBe(true);
    expect(isResumeDeskWork(a, { ...a, panel: "notes:list", plugin: "notes", title: "闪念" })).toBe(false);
  });
});

describe("isDeskLivePlugin", () => {
  it("presses the glance of the hand now on the desk", () => {
    expect(isDeskLivePlugin("coding:widget", "coding:studio")).toBe(true);
    expect(isDeskLivePlugin("coding:studio", "coding:studio")).toBe(true);
    expect(isDeskLivePlugin("notes:list", "coding:studio")).toBe(false);
    expect(isDeskLivePlugin("coding:studio", null)).toBe(false);
  });
});

describe("desk origin", () => {
  it("hands the glance rect to the grow-in once", () => {
    setDeskOrigin(new DOMRect(10, 20, 80, 40));
    const first = takeDeskOrigin();
    expect(first?.x).toBe(10);
    expect(first?.width).toBe(80);
    expect(takeDeskOrigin()).toBeNull();
  });
});
