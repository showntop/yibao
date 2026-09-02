// Focus 现场恢复（8-31 审计 P0-6）：进 Focus 收起两侧栏前快照，退出恢复真实先前状态。
import { describe, expect, it } from "vitest";
import { focusSceneNext } from "./focus-scene";

describe("focusSceneNext（Focus 现场快照/恢复）", () => {
  it("进 Focus：快照当前现场并收起两侧栏", () => {
    const next = focusSceneNext({ scene: { leftOpen: true, peekOpen: true }, saved: null }, true);
    expect(next.scene).toEqual({ leftOpen: false, peekOpen: false });
    expect(next.saved).toEqual({ leftOpen: true, peekOpen: true });
  });

  it("退出 Focus：恢复进 Focus 前的真实状态（一开一合也原样回来）", () => {
    const entered = focusSceneNext({ scene: { leftOpen: true, peekOpen: false }, saved: null }, true);
    const left = focusSceneNext(entered, false);
    expect(left.scene).toEqual({ leftOpen: true, peekOpen: false });
    expect(left.saved).toBeNull();
  });

  it("Focus 期间栏位被别的逻辑改动：退出仍恢复进入时的快照", () => {
    const entered = focusSceneNext({ scene: { leftOpen: true, peekOpen: true }, saved: null }, true);
    // Focus 中 peek 被摊纸逻辑摊开（pages watch），快照不能被污染
    const mid = { scene: { leftOpen: false, peekOpen: true }, saved: entered.saved };
    const left = focusSceneNext(mid, false);
    expect(left.scene).toEqual({ leftOpen: true, peekOpen: true });
    expect(left.saved).toBeNull();
  });

  it("非 Focus 退出（无快照）：不动现场", () => {
    const cur = { scene: { leftOpen: true, peekOpen: false }, saved: null };
    expect(focusSceneNext(cur, false)).toBe(cur);
  });

  it("重复进 Focus：保留最初快照，不被已收起态覆盖", () => {
    const entered = focusSceneNext({ scene: { leftOpen: true, peekOpen: true }, saved: null }, true);
    const again = focusSceneNext(entered, true);
    expect(again.saved).toEqual({ leftOpen: true, peekOpen: true });
    expect(again.scene).toEqual({ leftOpen: false, peekOpen: false });
  });

  it("往返后再进 Focus：重新快照当下现场", () => {
    const first = focusSceneNext({ scene: { leftOpen: true, peekOpen: true }, saved: null }, true);
    const back = focusSceneNext(first, false);
    // 用户在非 Focus 下关掉左栏，再进 Focus：新快照 = 当下状态
    const second = focusSceneNext({ scene: { leftOpen: false, peekOpen: true }, saved: back.saved }, true);
    expect(second.saved).toEqual({ leftOpen: false, peekOpen: true });
    const back2 = focusSceneNext(second, false);
    expect(back2.scene).toEqual({ leftOpen: false, peekOpen: true });
  });
});
