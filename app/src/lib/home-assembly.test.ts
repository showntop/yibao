import { describe, expect, it, afterEach } from "vitest";
import { defaultLayout } from "./home-widgets";
import {
  HOME_PARTS,
  HOME_PRESET_DEFAULT,
  HOME_PRESETS,
  collapseGridColumns,
  isHomePresetId,
  presentationOf,
  pluginPartId,
  resetPluginParts,
  resolveAssembly,
  faceOf,
  spineLimitOf,
  splitGridTracks,
  stackOrder,
  syncPluginParts,
  defaultPeek,
} from "./home-assembly";

describe("home-assembly catalog", () => {
  it("registers chat and composer as parts, not only glance tiles", () => {
    const ids = HOME_PARTS.map((p) => p.id);
    expect(ids).toContain("chat");
    expect(ids).toContain("composer");
    expect(ids).toContain("sessions");
    expect(HOME_PARTS.find((p) => p.id === "chat")?.kind).toBe("work");
    expect(HOME_PARTS.find((p) => p.id === "composer")?.kind).toBe("input");
    expect(HOME_PARTS.find((p) => p.id === "chat")?.presentations).toEqual(["thread", "paper"]);
    expect(HOME_PARTS.find((p) => p.id === "sessions")?.presentations).toEqual(["list", "spine"]);
  });
});

describe("home-assembly presets", () => {
  it("defaults to rails and keeps desk as a second preset", () => {
    expect(HOME_PRESET_DEFAULT).toBe("rails");
    expect(isHomePresetId("rails")).toBe(true);
    expect(isHomePresetId("desk")).toBe(true);
    expect(isHomePresetId("canvas")).toBe(false);
  });

  it("lets each preset name its own regions", () => {
    expect(HOME_PRESETS.rails.grid.areas.join(" ")).toContain("left");
    expect(HOME_PRESETS.rails.grid.areas.join(" ")).toContain("main");
    expect(HOME_PRESETS.desk.grid.areas.join(" ")).toContain("book");
    expect(HOME_PRESETS.desk.grid.areas.join(" ")).toContain("compose");
    expect(HOME_PRESETS.desk.grid.areas.join(" ")).toContain("plug");
    expect(HOME_PRESETS.desk.grid.areas.join(" ")).not.toContain("work-start");
  });
});

describe("resolveAssembly", () => {
  it("picks presentations and regions from the preset", () => {
    const rails = resolveAssembly("rails", defaultLayout());
    expect(presentationOf(rails, "chat")).toBe("thread");
    expect(presentationOf(rails, "sessions")).toBe("list");
    expect(presentationOf(rails, "now")).toBe("inspector");
    expect(presentationOf(rails, "mind")).toBe("map");
    expect(rails.items.find((i) => i.id === "chat")?.region).toBe("main");
    expect(rails.items.find((i) => i.id === "composer")?.region).toBe("main");
    expect(rails.items.find((i) => i.id === "sessions")?.region).toBe("left");
    expect(rails.items.find((i) => i.id === "now")?.region).toBe("right");
    expect(rails.items.find((i) => i.id === "need")).toBeUndefined();

    const desk = resolveAssembly("desk", defaultLayout());
    expect(presentationOf(desk, "chat")).toBe("paper");
    expect(presentationOf(desk, "sessions")).toBe("spine");
    expect(presentationOf(desk, "now")).toBe("note");
    expect(presentationOf(desk, "mind")).toBe("tile");
    expect(desk.items.find((i) => i.id === "chat")?.region).toBe("book");
    expect(desk.items.find((i) => i.id === "sessions")?.dock).toEqual({ to: "chat", edge: "start" });
    expect(desk.items.find((i) => i.id === "now")?.dock).toEqual({ to: "chat", edge: "end" });
    expect(desk.items.find((i) => i.id === "need")?.region).toBe("need");
  });

  it("skips hidden parts and docks whose host is gone; does not reset the rest", () => {
    const prefs = { ...defaultLayout(), hidden: ["chat" as const, "remind" as const] };
    const desk = resolveAssembly("desk", prefs);
    expect(desk.items.find((i) => i.id === "chat")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "sessions")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "now")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "remind")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "mind")?.region).toBe("mind");
    expect(desk.items.find((i) => i.id === "composer")?.region).toBe("compose");
  });

  it("skips unknown ids and regions that are not on this grid", () => {
    const desk = resolveAssembly("desk", defaultLayout(), {
      extra: [
        { id: "ghost" as never, region: "book" },
        { id: "today", region: "no-such-cell" },
      ],
    });
    expect(desk.items.find((i) => i.id === "ghost" as never)).toBeUndefined();
    expect(desk.items.find((i) => i.id === "today")?.region).toBe("today");
  });

  it("orders parts that share a region using widget order", () => {
    const prefs = defaultLayout();
    prefs.order = ["sessions", "identity", "mind", "today", "need", "tasks", "remind", "now"];
    const rails = resolveAssembly("rails", prefs);
    const left = rails.items.filter((i) => i.region === "left").map((i) => i.id);
    expect(left[0]).toBe("sessions");
    expect(left).toContain("identity");
  });

  it("drops glance regions in compact desk but keeps book, compose, and docks", () => {
    const desk = resolveAssembly("desk", defaultLayout(), { compact: true });
    expect(desk.grid.areas).toEqual(["book", "compose"]);
    expect(desk.items.find((i) => i.id === "mind")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "chat")?.region).toBe("book");
    expect(desk.items.find((i) => i.id === "sessions")?.dock?.to).toBe("chat");
    expect(desk.items.find((i) => i.id === "composer")?.region).toBe("compose");
  });

  it("reads a part face with fallback when the part is not placed", () => {
    const rails = resolveAssembly("rails", defaultLayout());
    expect(faceOf(rails, "chat")).toBe("thread");
    expect(faceOf(rails, "need", "tile")).toBe("tile");
    expect(spineLimitOf(rails)).toBe(0);
    expect(spineLimitOf(resolveAssembly("desk", defaultLayout()))).toBe(4);
  });

  it("opens peek for a column inspector even if chat is paper; tucks a docked note even if chat is thread", () => {
    expect(defaultPeek(resolveAssembly("rails", defaultLayout()))).toBe(true);
    expect(defaultPeek(resolveAssembly("desk", defaultLayout()))).toBe(false);
    expect(defaultPeek({
      preset: "desk",
      grid: HOME_PRESETS.desk.grid,
      items: [
        { id: "chat", kind: "work", presentation: "paper", region: "book" },
        { id: "now", kind: "context", presentation: "inspector", region: "right" },
      ],
    })).toBe(true);
    expect(defaultPeek({
      preset: "rails",
      grid: HOME_PRESETS.rails.grid,
      items: [
        { id: "chat", kind: "work", presentation: "thread", region: "main" },
        { id: "now", kind: "context", presentation: "note", dock: { to: "chat", edge: "end" } },
      ],
    })).toBe(false);
  });
});

describe("grid tracks", () => {
  it("splits minmax tracks and collapses named columns", () => {
    expect(splitGridTracks("280px minmax(0, 1fr) 280px")).toEqual([
      "280px",
      "minmax(0, 1fr)",
      "280px",
    ]);
    const rails = HOME_PRESETS.rails.grid;
    expect(collapseGridColumns(rails, new Set(["left", "right"]))).toBe(
      "0px minmax(0, 1fr) 0px",
    );
    expect(collapseGridColumns(rails, new Set(["left"]))).toBe(
      "0px minmax(0, 1fr) 280px",
    );
  });
});

describe("plugin glances", () => {
  afterEach(() => resetPluginParts());

  it("merges widget panels into the catalog and places them in the preset plugin region", () => {
    syncPluginParts([{ panel: "notes:widget" }, { panel: "weather:now" }]);
    const rails = resolveAssembly("rails", defaultLayout());
    expect(rails.items.find((i) => i.id === pluginPartId("notes:widget"))?.region).toBe("left");
    expect(rails.items.find((i) => i.id === pluginPartId("weather:now"))?.kind).toBe("glance");

    const desk = resolveAssembly("desk", defaultLayout());
    expect(desk.items.find((i) => i.id === pluginPartId("notes:widget"))?.region).toBe("plug");

    const compact = resolveAssembly("desk", defaultLayout(), { compact: true });
    expect(compact.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();
  });

  it("stacks plugin cards after glances in the same region, before sessions", () => {
    syncPluginParts([{ panel: "notes:widget" }]);
    const prefs = defaultLayout();
    const rails = resolveAssembly("rails", prefs);
    const plugin = pluginPartId("notes:widget");
    expect(stackOrder(rails, prefs, "identity")).toBe(0);
    expect(stackOrder(rails, prefs, "mind")).toBe(1);
    expect(stackOrder(rails, prefs, "today")).toBe(2);
    expect(stackOrder(rails, prefs, plugin)).toBe(3);
    expect(stackOrder(rails, prefs, "sessions")).toBe(6);
    expect(stackOrder(rails, prefs, plugin)).toBeLessThan(stackOrder(rails, prefs, "sessions"));
  });
});
