import { describe, expect, it } from "vitest";
import {
  defaultLayout,
  moveWidget,
  parseLayout,
  setMaterial,
  setPartFrame,
  setSize,
  specOf,
  toggleHidden,
  clearPartFrame,
  clearPresetLayout,
} from "./home-widgets";

describe("home-widgets", () => {
  it("ignores unknown ids and keeps order complete", () => {
    const layout = parseLayout(JSON.stringify({
      hidden: ["mind", "nope"],
      size: { today: "s", ghost: "l" },
      material: { identity: "glass" },
      order: ["sessions", "bogus"],
    }));
    expect(layout.hidden).toEqual(["mind"]);
    expect(layout.size.today).toBe("s");
    expect(layout.material.identity).toBe("glass");
    expect(layout.order[0]).toBe("sessions");
    expect(layout.order).toContain("identity");
    expect(layout.order).toContain("today");
    expect(layout.order).toContain("need");
    expect(layout.order).toContain("tasks");
    expect(layout.order).toContain("remind");
  });

  it("falls back on garbage json", () => {
    expect(parseLayout("{")).toEqual(defaultLayout());
  });

  it("toggles hide and reports spec defaults", () => {
    const hidden = toggleHidden(defaultLayout(), "today");
    expect(specOf(hidden, "today").visible).toBe(false);
    expect(specOf(defaultLayout(), "mind").material).toBe("porcelain");
    expect(specOf(defaultLayout(), "sessions").size).toBe("l");
  });

  it("moves a widget before another", () => {
    const next = moveWidget(defaultLayout(), "sessions", "identity");
    expect(next.order[0]).toBe("sessions");
    expect(setSize(next, "sessions", "s").size.sessions).toBe("s");
    expect(setMaterial(next, "sessions", "glass").material.sessions).toBe("glass");
  });

  it("stores a canvas frame overlay per preset and detaches attach", () => {
    const next = setPartFrame(defaultLayout(), "canvas", "mind", { left: 10, top: 20, width: 100, height: 80, z: 3 });
    expect(next.layouts.canvas?.frames?.mind).toMatchObject({ left: 10, top: 20, width: 100, height: 80 });
    expect(next.layouts.canvas?.attach?.mind).toBeNull();
  });

  it("clears a preset's frames back to the factory snapshot", () => {
    const moved = setPartFrame(defaultLayout(), "canvas", "mind", { left: 10, top: 20, width: 100, height: 80 });
    const next = clearPresetLayout(moved, "canvas");
    expect(next.layouts.canvas).toBeUndefined();
    expect(clearPartFrame(moved, "canvas", "mind").layouts.canvas?.frames?.mind).toBeUndefined();
  });
});
