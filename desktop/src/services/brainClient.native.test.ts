// @vitest-environment happy-dom
// panelAction 的 native: 旁路：本机打开/亮出文件本地执行（不过 sidecar、无闸门），
// 白名单写死 reveal/open 两个；未知 native:* 与缺 path 一律拒绝且不触达 invoke。
import { describe, expect, it, vi, beforeEach } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import { panelAction } from "./brainClient";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve()) }));
vi.mock("@tauri-apps/api/event", () => ({
  emit: vi.fn(() => Promise.resolve()),
  listen: vi.fn(() => Promise.resolve(() => {})),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({
  openPath: vi.fn(() => Promise.resolve()),
  revealItemInDir: vi.fn(() => Promise.resolve()),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("panelAction native: 旁路", () => {
  it("native:reveal 本地 revealItemInDir，不发 panel_action", async () => {
    await panelAction("native:reveal", { path: "/tmp/v3.mp4" });
    expect(revealItemInDir).toHaveBeenCalledWith("/tmp/v3.mp4");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("native:open 本地 openPath", async () => {
    await panelAction("native:open", { path: "/tmp/a.pptx" });
    expect(openPath).toHaveBeenCalledWith("/tmp/a.pptx");
    expect(invoke).not.toHaveBeenCalled();
  });

  it("未知 native:* 拒绝且不触达 sidecar", async () => {
    await expect(panelAction("native:rm", { path: "/tmp/x" })).rejects.toThrow();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("path 缺失/非字符串拒绝", async () => {
    await expect(panelAction("native:open", {})).rejects.toThrow();
    expect(openPath).not.toHaveBeenCalled();
  });

  it("普通方法照常走 panel_action", async () => {
    await panelAction("zimeiti.list", {});
    expect(invoke).toHaveBeenCalledWith("panel_action", expect.objectContaining({ method: "zimeiti.list" }));
  });
});
