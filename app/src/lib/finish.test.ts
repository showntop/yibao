// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyFinish, bootFinish, isFinishId, readFinish } from "./finish";

const mem: Record<string, string> = {};

describe("finish", () => {
  beforeEach(() => {
    for (const k of Object.keys(mem)) delete mem[k];
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => mem[k] ?? null,
      setItem: (k: string, v: string) => { mem[k] = v; },
      removeItem: (k: string) => { delete mem[k]; },
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete document.documentElement.dataset.finish;
  });

  it("rejects unknown ids", () => {
    expect(isFinishId("porcelain")).toBe(true);
    expect(isFinishId("glass")).toBe(false);
  });

  it("persists and boots data-finish", () => {
    applyFinish("metal");
    expect(mem["yibao-finish"]).toBe("metal");
    expect(document.documentElement.dataset.finish).toBe("metal");
    delete document.documentElement.dataset.finish;
    expect(readFinish()).toBe("metal");
    bootFinish();
    expect(document.documentElement.dataset.finish).toBe("metal");
  });
});
