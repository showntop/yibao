import { describe, expect, it, beforeEach } from "vitest";
import { MemoryKVStore } from "../persist-engine";
import { WindowDomain, clampBoundsToScreen } from "./window";
import type { WindowState } from "../types";

function makeDomain(screen?: { x: number; y: number; width: number; height: number }) {
  const store = new MemoryKVStore();
  const domain = new WindowDomain(store, { screen: () => screen ?? { x: 0, y: 0, width: 1920, height: 1080 } });
  return { store, domain };
}

describe("window domain", () => {
  let store: MemoryKVStore;
  let domain: WindowDomain;

  beforeEach(() => {
    const ctx = makeDomain();
    store = ctx.store;
    domain = ctx.domain;
  });

  it("persists and hydrates window state", async () => {
    domain.updateState("main", {
      bounds: { x: 100, y: 50, width: 900, height: 600 },
      visible: true,
      focusedConversationId: "c1",
      focusedPanelId: "p1",
    });
    await domain.flush();
    const fresh = new WindowDomain(store);
    await fresh.hydrate();
    const state = fresh.getState("main")!;
    expect(state.bounds).toEqual({ x: 100, y: 50, width: 900, height: 600 });
    expect(state.visible).toBe(true);
    expect(state.focusedConversationId).toBe("c1");
    expect(state.focusedPanelId).toBe("p1");
  });

  it("clamps bounds off-screen back to null (display disconnected)", async () => {
    domain.updateState("main", { bounds: { x: 5000, y: 0, width: 800, height: 600 } });
    await domain.flush();
    const fresh = new WindowDomain(store, { screen: () => ({ x: 0, y: 0, width: 1920, height: 1080 }) });
    await fresh.hydrate();
    expect(fresh.getState("main")!.bounds).toBeNull();
  });

  it("keeps on-screen bounds unchanged", async () => {
    domain.updateState("main", { bounds: { x: 200, y: 100, width: 800, height: 600 } });
    await domain.flush();
    const fresh = new WindowDomain(store);
    await fresh.hydrate();
    expect(fresh.getState("main")!.bounds).toEqual({ x: 200, y: 100, width: 800, height: 600 });
  });

  it("sanitizes invalid bounds on hydrate", async () => {
    store.put("windows", "main", { windowId: "main", bounds: { x: -5, y: 0, width: "bad", height: 600 } });
    const fresh = new WindowDomain(store);
    await fresh.hydrate();
    expect(fresh.getState("main")!.bounds).toBeNull();
  });

  it("partial updates merge with existing state", () => {
    domain.updateState("main", { visible: true, focusedConversationId: "c1" });
    domain.updateState("main", { alwaysOnTop: true });
    const state = domain.getState("main")!;
    expect(state.visible).toBe(true);
    expect(state.focusedConversationId).toBe("c1");
    expect(state.alwaysOnTop).toBe(true);
  });

  it("focused setters update references only", () => {
    domain.setFocusedConversation("main", "c9");
    domain.setFocusedPanel("main", null);
    expect(domain.getState("main")!.focusedConversationId).toBe("c9");
    expect(domain.getState("main")!.focusedPanelId).toBeNull();
  });

  it("clearAll wipes all windows", async () => {
    domain.updateState("main", { visible: true });
    domain.updateState("pet", { visible: false });
    await domain.flush();
    await domain.clearAll();
    expect(domain.getAllStates()).toHaveLength(0);
  });
});

describe("clampBoundsToScreen", () => {
  const screen = { x: 0, y: 0, width: 1920, height: 1080 };

  it("accepts fully visible bounds", () => {
    const bounds: WindowState["bounds"] = { x: 10, y: 10, width: 500, height: 400 };
    expect(clampBoundsToScreen(bounds, screen)).toEqual(bounds);
  });

  it("rejects bounds entirely outside the screen", () => {
    const bounds: WindowState["bounds"] = { x: 5000, y: 5000, width: 500, height: 400 };
    expect(clampBoundsToScreen(bounds, screen)).toBeNull();
  });

  it("rejects null input", () => {
    expect(clampBoundsToScreen(null, screen)).toBeNull();
  });
});
