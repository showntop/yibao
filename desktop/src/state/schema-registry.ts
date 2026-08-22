/** SchemaRegistry：域描述符注册表 + 统一容错（版本/TTL/结构校验） */
import type { DomainDescriptor, DomainId } from "./types";

/** 注册键：`domain:recordKey`（同一域可注册多条记录，如 surface 的 scene/panel/interact） */
export type RecordKey = `${DomainId}:${string}`;

const registry = new Map<RecordKey, DomainDescriptor<unknown>>();

export function registerDomain<T>(recordKey: RecordKey, descriptor: DomainDescriptor<T>): void {
  if (registry.has(recordKey)) {
    throw new Error(`record already registered: ${recordKey}`);
  }
  registry.set(recordKey, descriptor as DomainDescriptor<unknown>);
}

export function getDescriptor<T = unknown>(recordKey: RecordKey): DomainDescriptor<T> {
  const d = registry.get(recordKey);
  if (!d) throw new Error(`record not registered: ${recordKey}`);
  return d as DomainDescriptor<T>;
}

/** 校验结果：ok=true 返回净化后的值；ok=false 为损坏/过期，调用方应丢弃该记录 */
export type ValidateResult<T> = { ok: true; value: T } | { ok: false; reason: "corrupt" | "stale" | "missing" };

/**
 * 统一记录校验：
 * 1. 结构校验（descriptor.validate）失败 → corrupt（丢弃该条）
 * 2. TTL 检查：savedAt 超 ttl 视为过期 → stale（丢弃该条）
 * 非 null TTL 的记录要求带 savedAt 数字字段；不满足同样视为 corrupt。
 */
export function validateRecord<T>(
  descriptor: DomainDescriptor<T>,
  raw: unknown,
  now = Date.now(),
): ValidateResult<T> {
  if (raw === null || raw === undefined) return { ok: false, reason: "missing" };
  const value = descriptor.validate(raw);
  if (value === null) return { ok: false, reason: "corrupt" };
  if (descriptor.ttl !== null) {
    const savedAt = (raw as { savedAt?: unknown }).savedAt;
    if (typeof savedAt !== "number" || Number.isNaN(savedAt)) return { ok: false, reason: "corrupt" };
    if (now - savedAt > descriptor.ttl) return { ok: false, reason: "stale" };
  }
  return { ok: true, value };
}

/** 近似 JSON 字节数（用于容量配额判断；非精确但足够做上限守卫） */
export function roughBytes(value: unknown): number {
  if (typeof value === "string") return value.length;
  try {
    return JSON.stringify(value).length;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

/**
 * 容量守卫：超限返回 null（调用方据此丢弃或降级）。
 * TTL 记录会注入 savedAt 后再落盘 —— 由调用方自行决定保存时是否附加时间戳。
 */
export function withinQuota<T>(recordKey: RecordKey, value: unknown, quota: number): T | null {
  const descriptor = getDescriptor(recordKey);
  if (roughBytes(value) > quota) return null;
  return descriptor.validate(value) as T | null;
}

/** 附加 savedAt（供 TTL 记录落盘用） */
export function withSavedAt<T>(value: T, now = Date.now()): T & { savedAt: number } {
  return { ...(value as object), savedAt: now } as T & { savedAt: number };
}
