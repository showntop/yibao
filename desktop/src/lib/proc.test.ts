import { describe, expect, it } from "vitest";
import { procErrorReason, settleProcOnError, settleProcsOnInterrupt, type ProcStateRow } from "./proc";

describe("procErrorReason", () => {
  it("maps rejection/policy errors to short conclusions, keeps other text", () => {
    expect(procErrorReason("用户拒绝执行 agents.code_exec")).toBe("已拒绝");
    expect(procErrorReason("策略禁止执行 agents.code_exec（风险过高）")).toBe("已拦截");
    expect(procErrorReason("大脑出错：网络中断")).toBe("大脑出错：网络中断");
    expect(procErrorReason(undefined)).toBe("失败");
  });
});

describe("settleProcOnError", () => {
  it("settles the matching running row as fail with a short reason", () => {
    const rows: ProcStateRow[] = [
      { text: "运行沙箱脚本", pstate: "run" },
      { text: "找文件", pstate: "run" },
    ];
    const procIdx = new Map([
      ["a1", 0],
      ["a2", 1],
    ]);
    settleProcOnError(rows, procIdx, {
      action: { id: "a1", label: "运行沙箱脚本" },
      text: "用户拒绝执行 agents.code_exec",
    });
    expect(rows[0]).toEqual({ text: "运行沙箱脚本：已拒绝", pstate: "fail" });
    expect(rows[1]).toEqual({ text: "找文件", pstate: "run" });
    expect(procIdx.has("a1")).toBe(false);
    expect(procIdx.has("a2")).toBe(true);
  });

  it("ignores errors without an action or with an unknown action id", () => {
    const rows: ProcStateRow[] = [{ text: "运行沙箱脚本", pstate: "run" }];
    const procIdx = new Map([["a1", 0]]);
    settleProcOnError(rows, procIdx, { text: "大脑出错" });
    settleProcOnError(rows, procIdx, { action: { id: "nope" }, text: "用户拒绝执行 x" });
    expect(rows[0].pstate).toBe("run");
    expect(procIdx.has("a1")).toBe(true);
  });
});

describe("settleProcsOnInterrupt", () => {
  it("settles every running row as fail and clears the index", () => {
    const rows: ProcStateRow[] = [
      { text: "运行沙箱脚本", pstate: "run" },
      { text: "找文件", pstate: "ok" },
      { text: "读文件", pstate: "run" },
    ];
    // a1 已收尾出队（不在索引里）；索引里两道在途行都要收尾
    const procIdx = new Map([
      ["a0", 0],
      ["a2", 2],
    ]);
    settleProcsOnInterrupt(rows, procIdx);
    expect(rows[0]).toEqual({ text: "运行沙箱脚本：已打断", pstate: "fail" });
    expect(rows[1]).toEqual({ text: "找文件", pstate: "ok" });
    expect(rows[2]).toEqual({ text: "读文件：已打断", pstate: "fail" });
    expect(procIdx.size).toBe(0);
  });
});
