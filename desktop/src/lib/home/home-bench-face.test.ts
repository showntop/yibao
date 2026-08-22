import { describe, expect, it } from "vitest";
import { benchFace } from "./home-bench-face.ts";

describe("benchFace", () => {
  it("prefers a running coding session", () => {
    expect(benchFace({
      coding: {
        sessions: [
          { id: "s1", status: "done", prompt: "旧活" },
          { id: "s2", status: "running", prompt: "改首页", cwd: "/Users/me/yibao", agent: "codex" },
        ],
      },
      feed: [{ id: "t1", kind: "agent", label: "别的", prompt: "别的", status: "running", created_at: 1 }],
    })).toEqual({
      kind: "coding",
      label: "改首页",
      who: "Codex",
      state: "在跑",
      method: "coding.attach",
      params: { session_id: "s2" },
      surface: "panel:coding",
    });
  });

  it("falls back to a running agent row, then the feed", () => {
    expect(benchFace({
      widgets: [{
        panel: "agents:widget",
        data: { rows: [{ prompt: "扫一遍仓库", agent: "Claude Code", status: "running" }] },
      }],
    })).toMatchObject({
      kind: "agent",
      label: "扫一遍仓库",
      who: "Claude Code",
      method: "agents.task_list",
    });
    expect(benchFace({
      feed: [{ id: "t1", kind: "script", label: "转个表", prompt: "", status: "running", created_at: 1 }],
    })).toMatchObject({ kind: "agent", who: "脚本", label: "转个表" });
  });

  it("stays empty when nothing is live", () => {
    expect(benchFace({
      coding: { sessions: [{ id: "s1", status: "done", prompt: "旧活" }] },
      widgets: [{ panel: "agents:widget", data: { rows: [{ status: "done", prompt: "完了" }] } }],
      feed: [],
    })).toBeNull();
  });
});
