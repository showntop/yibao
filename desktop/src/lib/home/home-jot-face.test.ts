import { describe, expect, it } from "vitest";
import { jotFace, jotWhen } from "./home-jot-face.ts";

describe("jotFace", () => {
  it("reads the latest notes widget row", () => {
    const ts = Math.floor(new Date("2026-08-22T12:00:00+08:00").getTime() / 1000);
    expect(jotFace(
      [{
        panel: "notes:widget",
        open: "notes.list",
        data: { rows: [{ text: "想看个电影", created_at: ts }] },
      }],
      new Date("2026-08-22T22:00:00+08:00"),
    )).toEqual({
      text: "想看个电影",
      when: "8月22日",
      open: "notes.list",
    });
  });

  it("stays empty when the tray has no slip", () => {
    expect(jotFace([{ panel: "notes:widget", open: "notes.list", data: { rows: [] } }])).toBeNull();
    expect(jotFace([])).toBeNull();
  });
});

describe("jotWhen", () => {
  it("drops the year when it is this year", () => {
    expect(jotWhen(0)).toBe("");
    expect(jotWhen(1735689600, new Date("2026-08-22"))).toBe("2025年1月1日");
  });
});
