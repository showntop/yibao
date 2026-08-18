import { describe, expect, it } from "vitest";
import { pickReplayCandidate, replayStep, shouldYieldReplay } from "./replay";
import type { SessionRow } from "./types";

function row(p: Partial<SessionRow>): SessionRow {
  return {
    id: "s?", agent: "claude-code", cwd: "/p", prompt: "t",
    status: "done", created_at: 0, finished_at: 0, cc_session_id: "",
    source: "", mode: "acceptEdits", live: "idle",
    ...p,
  };
}

describe("pickReplayCandidate", () => {
  it("取该 cwd 时间倒序首个可回放会话(rows 已按时间倒序)", () => {
    const rows = [row({ id: "a", cwd: "/other" }), row({ id: "b" }), row({ id: "c" })];
    expect(pickReplayCandidate(rows, "/p", null)).toEqual({ sid: "b", agent: "claude-code" });
  });

  it("排除活体(running/waiting 发送会被拒)", () => {
    const rows = [row({ id: "r", live: "running" }), row({ id: "w", live: "waiting" }), row({ id: "ok" })];
    expect(pickReplayCandidate(rows, "/p", null)).toEqual({ sid: "ok", agent: "claude-code" });
  });

  it("codex 已探测不可用(false)排除 codex 行;未探测(null)/可用(true)保留", () => {
    const rows = [row({ id: "cx", agent: "codex" }), row({ id: "cc" })];
    expect(pickReplayCandidate(rows, "/p", false)).toEqual({ sid: "cc", agent: "claude-code" });
    expect(pickReplayCandidate(rows, "/p", null)).toEqual({ sid: "cx", agent: "codex" });
    expect(pickReplayCandidate(rows, "/p", true)).toEqual({ sid: "cx", agent: "codex" });
  });

  it("无命中/空 rows/行缺 id → null", () => {
    expect(pickReplayCandidate([], "/p", null)).toBeNull();
    expect(pickReplayCandidate([row({ id: "x", cwd: "/other" })], "/p", null)).toBeNull();
    expect(pickReplayCandidate([row({ id: "" })], "/p", null)).toBeNull();
  });

  it("cwd 精确匹配(尾斜杠差异不归一——调用方传入的 rows/cwd 同源)", () => {
    expect(pickReplayCandidate([row({ id: "x", cwd: "/p/" })], "/p", null)).toBeNull();
  });
});

describe("shouldYieldReplay", () => {
  it("currentSession 出现或 resume 在飞即让位", () => {
    expect(shouldYieldReplay(false, false)).toBe(false);
    expect(shouldYieldReplay(true, false)).toBe(true);
    expect(shouldYieldReplay(false, true)).toBe(true);
    expect(shouldYieldReplay(true, true)).toBe(true);
  });
});

describe("replayStep", () => {
  it("0=空会话顺延下一条;-1=被归并(他路抢占)停;>0=已回放停", () => {
    expect(replayStep(0)).toBe("tryNext");
    expect(replayStep(-1)).toBe("stop");
    expect(replayStep(3)).toBe("stop");
  });
});
