import { describe, expect, it } from "vitest";
import { lineFace, readLineCache, writeLineCache } from "./home-line-face.ts";

describe("lineFace", () => {
  it("takes the first quote row", () => {
    expect(lineFace({
      rows: [{ text: "星影落九天", from: "测试出处" }, { text: "下一句", from: "" }],
    })).toEqual({ text: "星影落九天", from: "测试出处" });
  });

  it("stays empty without a sentence", () => {
    expect(lineFace({ rows: [] })).toBeNull();
    expect(lineFace({ rows: [{ text: "  ", from: "x" }] })).toBeNull();
    expect(lineFace(null)).toBeNull();
  });
});

describe("line cache", () => {
  it("round-trips a kept sentence", () => {
    const store: Record<string, string> = {};
    const storage = {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value; },
    };
    writeLineCache({ text: "少年与爱永不老去。", from: "测试" }, storage);
    expect(readLineCache(storage)).toEqual({ text: "少年与爱永不老去。", from: "测试" });
  });
});
