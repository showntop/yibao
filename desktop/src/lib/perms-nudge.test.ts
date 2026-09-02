// @vitest-environment happy-dom
// 权限引导按需降级（9-01 P2-01 / 8-31 P0-7）：收起/展开偏好持久化 + 电脑控制工具判定。
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  needsComputerControl,
  permsNudgeCollapsed,
  readPermsNudgeCollapsed,
  setPermsNudgeCollapsed,
} from "./perms-nudge";

const mem: Record<string, string> = {};

describe("perms-nudge 收起偏好", () => {
  beforeEach(() => {
    for (const k of Object.keys(mem)) delete mem[k];
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => mem[k] ?? null,
      setItem: (k: string, v: string) => { mem[k] = v; },
      removeItem: (k: string) => { delete mem[k]; },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("缺省收起：无存储时读 true（降级一行条）", () => {
    expect(readPermsNudgeCollapsed()).toBe(true);
  });

  it("展开/收起持久化并同步共享 ref", () => {
    setPermsNudgeCollapsed(false);
    expect(permsNudgeCollapsed.value).toBe(false);
    expect(mem["yibao-perms-nudge"]).toBe("0");
    expect(readPermsNudgeCollapsed()).toBe(false);
    setPermsNudgeCollapsed(true);
    expect(permsNudgeCollapsed.value).toBe(true);
    expect(readPermsNudgeCollapsed()).toBe(true);
  });
});

describe("needsComputerControl（电脑控制工具判定）", () => {
  it("感知/控制类工具命中（sidecar perception 域）", () => {
    for (const id of ["screenshot", "read_tree", "open_app", "click_control", "type_text", "computer_use"]) {
      expect(needsComputerControl(id)).toBe(true);
    }
  });

  it("普通工具与空值不命中", () => {
    expect(needsComputerControl("web_search")).toBe(false);
    expect(needsComputerControl("write_note")).toBe(false);
    expect(needsComputerControl(undefined)).toBe(false);
  });
});
