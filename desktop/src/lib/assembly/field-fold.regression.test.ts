import { describe, expect, it } from "vitest";
import { defaultLayout } from "../home/home-widgets.ts";
import { resolveAssembly } from "./layout.ts";

/** 真机验收回归：溪场折叠后的网格必须仍合法；会话不常驻（§5），细脊已退役。 */
describe("field fold regression", () => {
  const narrow = resolveAssembly("field", defaultLayout(), { narrow: true });

  it("baseline narrow: chat+axis 两列，无会话列", () => {
    expect(narrow.grid?.columns).toBe("minmax(420px, 58fr) minmax(0, 42fr)");
    expect(narrow.grid?.areas).toBe(`"chat axis" "compose ."`);
    expect(narrow.items.some((item) => item.id === "sessions")).toBe(false);
  });

  it("fold axis: 收成对话单列且 areas 合法", () => {
    const folded = resolveAssembly("field", defaultLayout(), { narrow: true, collapsed: ["axis"] });
    expect(folded.grid?.columns).toBe("minmax(420px, 58fr)");
    expect(folded.grid?.areas).toBe(`"chat" "compose"`);
  });

  it("main 档三列也无会话列，折轴后仍合法", () => {
    const full = resolveAssembly("field", defaultLayout());
    expect(full.grid?.columns).toBe("minmax(420px, 46fr) minmax(0, 27fr) minmax(0, 24fr)");
    expect(full.grid?.areas).toBe(`"chat axis shelf" "compose . ."`);
    const folded = resolveAssembly("field", defaultLayout(), { collapsed: ["axis"] });
    expect(folded.grid?.columns).toBe("minmax(420px, 46fr) minmax(0, 24fr)");
    expect(folded.grid?.areas).toBe(`"chat shelf" "compose ."`);
  });

  it("desk fold note: 便条列连同相邻间隙移除，其余轨道完好", () => {
    const folded = resolveAssembly("desk", defaultLayout(), { collapsed: ["note"] });
    expect(folded.grid?.columns).toBe("164px 12px 40px minmax(0,1fr) 12px 164px");
    expect(folded.grid?.columns).not.toContain("188px");
  });
});
