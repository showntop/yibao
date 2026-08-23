import { describe, expect, test } from "vitest";
import { ACTION_DEFS, actionsOf, ROLE_ACTIONS, type MsgAction } from "./msg-actions";

describe("消息可操作性策略（role → 操作）", () => {
  test("sys（系统通知/轻提示）零操作：不出现复制/重写/反馈", () => {
    expect(actionsOf("sys")).toEqual([]);
    // 回归（2026-08-23）：use_plugin 展开提示行被渲染出完整工具栏
    expect(ROLE_ACTIONS.sys.has("copy")).toBe(false);
    expect(ROLE_ACTIONS.sys.has("regenerate")).toBe(false);
    expect(ROLE_ACTIONS.sys.has("feedback")).toBe(false);
  });

  test("user 可复制/编辑", () => {
    expect(actionsOf("user")).toEqual(["copy", "edit"]);
  });

  test("ai 可复制/反馈/重写", () => {
    expect(actionsOf("ai")).toEqual(["copy", "feedback", "regenerate"]);
  });

  test("每个 role 的操作都是已定义操作", () => {
    const defined = new Set<MsgAction>(Object.keys(ACTION_DEFS) as MsgAction[]);
    for (const actions of Object.values(ROLE_ACTIONS)) {
      for (const a of actions) expect(defined.has(a)).toBe(true);
    }
  });
});
