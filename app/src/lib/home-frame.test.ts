import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { HOME_PRESETS, splitGridTracks } from "./home-assembly";

const frame = readFileSync(resolve(import.meta.dirname, "../components/HomeFrame.vue"), "utf8");

describe("desk composer row", () => {
  it("puts compose on its own content-sized row, not inside the book stretch", () => {
    const desk = HOME_PRESETS.desk.grid;
    expect(desk.areas.at(-1)).toContain("compose");
    expect(desk.areas.at(-1)).not.toContain("book");
    expect(splitGridTracks(desk.rows).at(-1)).toBe("auto");
  });

  it("sizes the input host to the bar instead of collapsing flex:1 in an auto row", () => {
    expect(frame).toMatch(/\.host\.kind-input\s+\.host-body\s*\{[^}]*flex:\s*none/);
  });
});

describe("column hosts", () => {
  it("lets nav and context hosts fill the column so peek can reveal them", () => {
    expect(frame).toMatch(/\.host\.kind-nav,\s*\.host\.kind-context\s*\{[^}]*flex:\s*1/);
  });

  it("gives stacked hosts the same order as their parts so sessions sit after glances", () => {
    expect(frame).toMatch(/--yb-widget-order': hostOrder/);
    expect(frame).toMatch(/\.host\s*\{[^}]*order:\s*var\(--yb-widget-order/);
  });

  it("sizes rail hosts to the padded column, not a raw 280px that outgrows glance tiles", () => {
    expect(frame).not.toMatch(/cell\[data-region="left"\] > :deep\(\*\)[\s\S]{0,120}min-width:\s*280px/);
    expect(frame).toMatch(/cell\[data-region="left"\] > :deep\(\*\)[\s\S]{0,160}max-width:\s*100%/);
  });
});
