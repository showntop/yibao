import { describe, expect, it, beforeEach } from "vitest";
import { MemoryKVStore, TABLES } from "../persist-engine";
import { SurfaceDomain } from "./surface";
import type { SurfaceInteract, SurfacePanel, SurfaceScene } from "../types";

function makeDomain() {
  const store = new MemoryKVStore();
  const domain = new SurfaceDomain(store);
  return { store, domain };
}

const scene: SurfaceScene = { panel: "zimeiti:board", visible: true, presentation: "stage", tab: "home" };
const panel: SurfacePanel = {
  panel: "zimeiti:board",
  title: "自媒体 · 选题看板",
  schema: { version: 1 },
  data: { rows: [{ title: "a" }] },
  webview: null,
};
const interact: SurfaceInteract = { panel: "zimeiti:board", expandedNodes: ["root"], searchQuery: "选题", activeTab: "data" };

describe("surface domain", () => {
  let store: MemoryKVStore;
  let domain: SurfaceDomain;

  beforeEach(() => {
    const ctx = makeDomain();
    store = ctx.store;
    domain = ctx.domain;
  });

  it("persists scene/panel/interact and hydrates back", async () => {
    domain.setScene(scene);
    domain.setPanel(panel);
    domain.setInteract(interact);
    await domain.flush();

    const fresh = new SurfaceDomain(store);
    await fresh.hydrate();
    expect(fresh.getScene()).toEqual(scene);
    expect(fresh.getPanel()).toEqual(panel);
    expect(fresh.getInteract()).toEqual(interact);
  });

  it("drops scene/panel older than TTL (24h)", async () => {
    domain.setScene(scene);
    domain.setPanel(panel);
    await domain.flush();
    // 把 savedAt 改到 25 小时前
    const now = Date.now();
    for (const key of ["scene", "panel"]) {
      const raw = store.dump(TABLES.surface).get(key) as { savedAt: number };
      raw.savedAt = now - 25 * 60 * 60 * 1000;
    }
    const fresh = new SurfaceDomain(store);
    await fresh.hydrate(now);
    expect(fresh.getScene()).toBeNull();
    expect(fresh.getPanel()).toBeNull();
    // 过期记录被就地清除
    expect(store.dump(TABLES.surface).has("scene")).toBe(false);
  });

  it("drops interact older than TTL (1h)", async () => {
    domain.setInteract(interact);
    await domain.flush();
    const now = Date.now();
    const raw = store.dump(TABLES.surface).get("interact") as { savedAt: number };
    raw.savedAt = now - 2 * 60 * 60 * 1000;
    const fresh = new SurfaceDomain(store);
    await fresh.hydrate(now);
    expect(fresh.getInteract()).toBeNull();
  });

  it("discards corrupt records and skips chain recovery", async () => {
    domain.setScene(scene);
    await domain.flush();
    await store.put(TABLES.surface, "scene", { nonsense: true });
    const fresh = new SurfaceDomain(store);
    await fresh.hydrate();
    expect(fresh.getScene()).toBeNull();
    expect(fresh.getPanel()).toBeNull();
    expect(fresh.getInteract()).toBeNull();
  });

  it("panel over quota keeps only the shell", async () => {
    const big = {
      panel: "big:panel",
      title: "大面板",
      schema: { version: 1 },
      data: { blob: "x".repeat(600 * 1024) },
      webview: null,
    };
    domain.setPanel(big);
    await domain.flush();
    const raw = store.dump(TABLES.surface).get("panel") as SurfacePanel;
    expect(raw.data).toEqual({});
    expect(raw.schema).toBeNull();
    expect(raw.panel).toBe("big:panel");
  });

  it("interact pointing at a different panel is invalidated on hydrate", async () => {
    domain.setPanel(panel);
    domain.setInteract({ ...interact, panel: "other:thing" });
    await domain.flush();
    const fresh = new SurfaceDomain(store);
    await fresh.hydrate();
    expect(fresh.getInteract()).toBeNull();
  });

  it("clearScene wipes all three records", async () => {
    domain.setScene(scene);
    domain.setPanel(panel);
    domain.setInteract(interact);
    await domain.flush();
    domain.clearScene();
    await domain.flush();
    expect(store.dump(TABLES.surface).size).toBe(0);
    expect(domain.getSnapshot()).toEqual({ scene: null, panel: null, interact: null });
  });
});
