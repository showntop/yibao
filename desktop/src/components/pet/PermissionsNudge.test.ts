// @vitest-environment happy-dom
// PermissionsNudge：权限卡按需降级——默认一行低干扰状态条，点击展开完整引导，
// 展开态可重新收起（偏好持久化）；demand（电脑控制工具被调用而权限缺失）就地自动展开。
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/brain", () => ({
  checkPermissions: vi.fn(() => Promise.resolve()),
  promptPermission: vi.fn(() => Promise.resolve()),
  revealAppInFinder: vi.fn(() => Promise.resolve()),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn(() => Promise.resolve()) }));

import PermissionsNudge from "./PermissionsNudge.vue";
import { permsNudgeCollapsed, setPermsNudgeCollapsed } from "../../lib/perms-nudge";

// happy-dom 环境不带可用 Storage：与 run-metrics.test.ts 同款内存替身
const mem: Record<string, string> = {};

const PERMS_ALL_MISSING = { ax: false, screen: false, input: false };
const PERMS_PART_MISSING = { ax: true, screen: false, input: true };

function mountNudge(props: { perms: typeof PERMS_ALL_MISSING; demand?: boolean }) {
  return mount(PermissionsNudge, {
    props,
    global: { stubs: { YbIcon: { template: "<i />" } } },
  });
}

describe("PermissionsNudge 权限卡按需降级", () => {
  beforeEach(() => {
    for (const k of Object.keys(mem)) delete mem[k];
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => mem[k] ?? null,
      setItem: (k: string, v: string) => { mem[k] = v; },
      removeItem: (k: string) => { delete mem[k]; },
    });
    setPermsNudgeCollapsed(true); // 每个用例从默认降级态出发
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    setPermsNudgeCollapsed(true);
  });

  it("默认渲染一行状态条，不渲染完整权限卡", () => {
    const w = mountNudge({ perms: PERMS_ALL_MISSING });
    expect(w.find(".nudge-bar").exists()).toBe(true);
    expect(w.find(".nudge-bar").text()).toContain("电脑控制未授权");
    expect(w.find(".banner").exists()).toBe(false);
  });

  it("状态条列出缺失项名称", () => {
    const w = mountNudge({ perms: PERMS_PART_MISSING });
    const text = w.find(".nudge-bar").text();
    expect(text).toContain("屏幕录制");
    expect(text).not.toContain("辅助功能");
    expect(text).not.toContain("输入监控");
  });

  it("点击状态条展开完整权限卡，并持久化展开偏好", async () => {
    const w = mountNudge({ perms: PERMS_ALL_MISSING });
    await w.find(".nudge-bar").trigger("click");
    expect(w.find(".banner").exists()).toBe(true);
    expect(permsNudgeCollapsed.value).toBe(false);
    expect(mem["yibao-perms-nudge"]).toBe("0");
  });

  it("展开态可重新收起，并持久化收起偏好", async () => {
    const w = mountNudge({ perms: PERMS_ALL_MISSING });
    await w.find(".nudge-bar").trigger("click");
    await w.find(".fold").trigger("click");
    expect(w.find(".banner").exists()).toBe(false);
    expect(w.find(".nudge-bar").exists()).toBe(true);
    expect(permsNudgeCollapsed.value).toBe(true);
    expect(mem["yibao-perms-nudge"]).toBe("1");
  });

  it("用户偏好展开时直接渲染完整权限卡", () => {
    setPermsNudgeCollapsed(false);
    const w = mountNudge({ perms: PERMS_ALL_MISSING });
    expect(w.find(".banner").exists()).toBe(true);
    expect(w.find(".nudge-bar").exists()).toBe(false);
  });

  it("demand（能力真正需要权限）就地自动展开，即使偏好收起", async () => {
    const w = mountNudge({ perms: PERMS_ALL_MISSING, demand: false });
    expect(w.find(".banner").exists()).toBe(false);
    delete mem["yibao-perms-nudge"]; // 清掉初始化的写入，单独观察 demand 是否写偏好
    await w.setProps({ demand: true });
    expect(w.find(".banner").exists()).toBe(true);
    // demand 不改用户持久化偏好
    expect(mem["yibao-perms-nudge"]).toBeUndefined();
  });

  it("demand 展开后用户仍可收起", async () => {
    const w = mountNudge({ perms: PERMS_ALL_MISSING, demand: true });
    expect(w.find(".banner").exists()).toBe(true);
    await w.find(".fold").trigger("click");
    expect(w.find(".banner").exists()).toBe(false);
    expect(w.find(".nudge-bar").exists()).toBe(true);
  });
});
