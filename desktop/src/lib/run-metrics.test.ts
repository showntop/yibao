// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readShowRunMetrics, setShowRunMetrics, showRunMetrics } from "./run-metrics";

const mem: Record<string, string> = {};

describe("run-metrics 开关", () => {
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
  });

  it("默认关：无存储时读 false", () => {
    expect(readShowRunMetrics()).toBe(false);
  });

  it("开关持久化并同步共享 ref", () => {
    setShowRunMetrics(true);
    expect(showRunMetrics.value).toBe(true);
    expect(mem["yibao-run-metrics"]).toBe("1");
    expect(readShowRunMetrics()).toBe(true);
    setShowRunMetrics(false);
    expect(showRunMetrics.value).toBe(false);
    expect(readShowRunMetrics()).toBe(false);
  });
});
