import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const frame = readFileSync(resolve(import.meta.dirname, "../views/HomeFrame.vue"), "utf8");
const bubble = readFileSync(resolve(import.meta.dirname, "../components/common/Bubble.vue"), "utf8");

describe("home frame place engines", () => {
  it("branches once on place, not on preset name", () => {
    expect(frame).toMatch(/assembly\.value\.place === "canvas"/);
    expect(frame).toMatch(/gridStageStyle\(grid\)/);
    expect(frame).toMatch(/data-place/);
    expect(frame).not.toMatch(/if \(preset === ['"]salon['"]\)/);
    expect(frame).not.toMatch(/\[data-preset="salon"\]/);
    expect(frame).not.toMatch(/data-preset/);
  });

  it("lets grid tile shadows paint instead of clipping them into gray corners", () => {
    expect(frame).toMatch(/\.area \{[\s\S]*?overflow: visible;/);
    expect(frame).toMatch(/\[data-place="grid"\] \.stage \{[\s\S]*?overflow: visible;/);
  });

  it("stretches desk workstation like the chat paper", () => {
    expect(frame).toMatch(/\.host :deep\(\.paper-wrap\),[\s\S]*?\.desk-work/);
  });

  it("collapses empty glance hosts so vacant tiles leave the desk", () => {
    // 空槽（未填充只剩注释节点、无任何元素子节点）才藏；裸字零件（如日题）合法占位
    expect(frame).toMatch(/\.host\.kind-glance:not\(:has\(\*\)\) \{[\s\S]*?display: none;/);
  });

  it("only snaps and drags on the canvas engine", () => {
    expect(frame).toMatch(/if \(!isCanvas\.value \|\| !item\.frame\) return/);
    expect(frame).toMatch(/snapBox\(/);
    expect(frame).toMatch(/foldHandleStyle\(/);
    expect(frame).not.toMatch(/rail-avatar-reopen/);
  });
});

describe("reminder bubble", () => {
  it("wraps long reminder copy instead of overflowing the thread", () => {
    expect(bubble).toMatch(/\.ai\.icon-clock \{[\s\S]*?min-width: 0;/);
    expect(bubble).toMatch(/\.ai\.icon-clock > span \{[\s\S]*?overflow-wrap: anywhere;/);
  });
});
