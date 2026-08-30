import { describe, expect, it } from "vitest";
import { defaultLayout } from "../home/home-widgets.ts";
import { resolveAssembly } from "./layout.ts";

/** 真机验收回归：溪场 narrow 档（<1280）点「会话」收细脊后，网格必须仍是合法两列。 */
describe("field narrow fold regression", () => {
  const narrow = resolveAssembly("field", defaultLayout(), { narrow: true });

  it("baseline narrow: spine+chat+axis 三列", () => {
    expect(narrow.grid?.columns).toBe("40px minmax(420px, 58fr) minmax(0, 42fr)");
    expect(narrow.grid?.areas).toBe(`"spine chat axis" ". compose ."`);
  });

  it("fold spine: 网格收成两列且 areas 行列数一致", () => {
    const folded = resolveAssembly("field", defaultLayout(), { narrow: true, collapsed: ["spine"] });
    expect(folded.grid?.columns).toBe("minmax(420px, 58fr) minmax(0, 42fr)");
    expect(folded.grid?.areas).toBe(`"chat axis" "compose ."`);
  });

  it("fold both: 单列合法", () => {
    const both = resolveAssembly("field", defaultLayout(), { narrow: true, collapsed: ["spine", "axis"] });
    expect(both.grid?.columns).toBe("minmax(420px, 58fr)");
    expect(both.grid?.areas).toBe(`"chat" "compose"`);
  });

  it("desk fold note: 便条列连同相邻间隙移除，其余轨道完好", () => {
    const folded = resolveAssembly("desk", defaultLayout(), { collapsed: ["note"] });
    expect(folded.grid?.columns).toBe("164px 12px 40px minmax(0,1fr) 12px 164px");
    expect(folded.grid?.columns).not.toContain("188px");
  });
});
