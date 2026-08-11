import { describe, expect, it } from "vitest";
import { registerDomain, validateRecord, withinQuota, roughBytes, withSavedAt, getDescriptor } from "./schema-registry";
import type { DomainDescriptor } from "./types";

interface TestRecord {
  id: string;
  value: string;
}

const desc = (ttl: number | null): DomainDescriptor<TestRecord> => ({
  domain: "conversation",
  version: 1,
  ttl,
  validate: (raw): TestRecord | null => {
    if (typeof raw !== "object" || raw === null) return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.id !== "string" || typeof r.value !== "string") return null;
    return { id: r.id, value: r.value };
  },
});

describe("schema-registry", () => {
  it("registerDomain rejects duplicate record keys", () => {
    registerDomain("conversation:dup", desc(null));
    expect(() => registerDomain("conversation:dup", desc(null))).toThrow(/already registered/);
  });

  it("getDescriptor throws for unregistered", () => {
    expect(() => getDescriptor("conversation:nope")).toThrow(/not registered/);
  });

  it("validateRecord accepts valid and rejects corrupt", () => {
    const d = desc(null);
    expect(validateRecord(d, { id: "a", value: "x" }).ok).toBe(true);
    expect(validateRecord(d, { id: 1, value: "x" }).ok).toBe(false);
    expect(validateRecord(d, null).ok).toBe(false);
  });

  it("validateRecord enforces TTL via savedAt", () => {
    const d = desc(1000);
    const ok = validateRecord(d, withSavedAt({ id: "a", value: "x" }), 0);
    expect(ok.ok).toBe(true);
    const stale = validateRecord(d, withSavedAt({ id: "a", value: "x" }, -1500), 0);
    expect(stale.ok).toBe(false);
    if (!stale.ok) expect(stale.reason).toBe("stale");
  });

  it("TTL record missing savedAt is corrupt", () => {
    const d = desc(1000);
    const r = validateRecord(d, { id: "a", value: "x" }, 0);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("corrupt");
  });

  it("withinQuota rejects oversized values", () => {
    registerDomain("conversation:q", desc(null));
    const big = { id: "a", value: "x".repeat(1000) };
    expect(withinQuota<TestRecord>("conversation:q", big, 100)).toBeNull();
    expect(withinQuota<TestRecord>("conversation:q", { id: "a", value: "small" }, 1000)?.value).toBe("small");
  });

  it("roughBytes measures strings and objects", () => {
    expect(roughBytes("abc")).toBe(3);
    expect(roughBytes({ a: "x" })).toBe(JSON.stringify({ a: "x" }).length);
    expect(roughBytes(() => 1)).toBe(Number.POSITIVE_INFINITY);
  });
});
