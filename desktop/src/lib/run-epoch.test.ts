// 运行代数闸（P0）单测：sidecar 给每个 run 事件盖 conversation_id + run_epoch + seq，
// 前端按会话只采纳最新 epoch——被抢占旧 run 的迟到事件不得改变 UI 状态。
import { beforeEach, describe, expect, it } from "vitest";
import { isStaleRunEvent, noteRunSubmitted, resetRunEpochs } from "./run-epoch";

describe("run-epoch 闸", () => {
  beforeEach(() => resetRunEpochs());

  it("无 run_epoch 的事件一律放行（提醒/notice 等非 run 事件，兼容缺省字段）", () => {
    expect(isStaleRunEvent({ conversationId: "c1" })).toBe(false);
    expect(isStaleRunEvent({})).toBe(false);
  });

  it("首个带 epoch 的事件被采纳；同 epoch 后续事件放行", () => {
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 3, seq: 1 })).toBe(false);
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 3, seq: 2 })).toBe(false);
  });

  it("更旧 epoch 的事件被丢弃；更新 epoch 被采纳后旧 epoch 再丢", () => {
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 2, seq: 1 })).toBe(false);
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 9 })).toBe(true); // 旧 run 迟到
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 2, seq: 2 })).toBe(false); // 当前 run 继续
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 10 })).toBe(true);
  });

  it("epoch 按会话独立：A 会话的新代数不影响 B 会话", () => {
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 5, seq: 1 })).toBe(false);
    expect(isStaleRunEvent({ conversationId: "c2", run_epoch: 1, seq: 1 })).toBe(false);
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 4, seq: 1 })).toBe(true);
  });

  it("发新消息（noteRunSubmitted）后，被抢占旧 run 的迟到事件立即作废——不等新 run 首个事件", () => {
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 1 })).toBe(false);
    noteRunSubmitted("c1"); // 用户发出第二句，sidecar 将分配 epoch 2
    // 竞态窗：旧 run 的迟到回复在新 run 首个事件之前到达 → 也必须丢弃
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 2 })).toBe(true);
    // 新 run 的事件（更高 epoch）照常采纳
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 2, seq: 1 })).toBe(false);
  });

  it("未见过任何事件的会话发消息不影响后续采纳（地板只压见过的代数）", () => {
    noteRunSubmitted("c1");
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 1 })).toBe(false);
  });

  it("大脑重启后 resetRunEpochs 清零账本：新 brain 从 epoch 1 重来不被误判为旧账", () => {
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 7, seq: 1 })).toBe(false);
    noteRunSubmitted("c1");
    resetRunEpochs();
    expect(isStaleRunEvent({ conversationId: "c1", run_epoch: 1, seq: 1 })).toBe(false);
  });
});
