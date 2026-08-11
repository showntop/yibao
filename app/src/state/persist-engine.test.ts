import { describe, expect, it, beforeEach } from "vitest";
import "fake-indexeddb/auto";
import { IdbKVStore, MemoryKVStore, TABLES } from "./persist-engine";

describe("MemoryKVStore", () => {
  let store: MemoryKVStore;

  beforeEach(() => {
    store = new MemoryKVStore();
  });

  it("put/get/delete roundtrip", async () => {
    await store.put("t", "k1", { a: 1 });
    expect(await store.get("t", "k1")).toEqual({ a: 1 });
    expect(await store.get("t", "missing")).toBeNull();
    await store.delete("t", "k1");
    expect(await store.get("t", "k1")).toBeNull();
  });

  it("entries returns all rows", async () => {
    await store.put("t", "a", 1);
    await store.put("t", "b", 2);
    const entries = await store.entries<number>("t");
    expect(entries.sort((x, y) => x.key.localeCompare(y.key))).toEqual([
      { key: "a", value: 1 },
      { key: "b", value: 2 },
    ]);
  });

  it("batch writes and deletes atomically in order", async () => {
    await store.batch([
      { table: "t", key: "a", value: 1 },
      { table: "t", key: "b", value: 2 },
    ]);
    await store.batch([{ table: "t", key: "a", value: undefined }]);
    expect(await store.get("t", "a")).toBeNull();
    expect(await store.get("t", "b")).toBe(2);
  });

  it("clear wipes a table", async () => {
    await store.put("t", "a", 1);
    await store.clear("t");
    expect((await store.entries("t")).length).toBe(0);
  });
});

describe("IdbKVStore", () => {
  let counter = 0;

  /** 每个测试用唯一库名，避免 deleteDatabase 与活跃连接互等 */
  async function newStore(): Promise<IdbKVStore> {
    counter += 1;
    return new IdbKVStore(`test-db-${counter}`, 1, Object.values(TABLES));
  }

  beforeEach(async () => {
    store = await newStore();
    await store.put("meta", "__ready__", true);
  });

  let store: IdbKVStore;

  it("put/get/delete roundtrip", async () => {
    await store.put("meta", "k1", { a: 1 });
    expect(await store.get("meta", "k1")).toEqual({ a: 1 });
    expect(await store.get("meta", "missing")).toBeNull();
    await store.delete("meta", "k1");
    expect(await store.get("meta", "k1")).toBeNull();
  });

  it("entries returns all rows", async () => {
    await store.put("windows", "main", { windowId: "main" });
    await store.put("windows", "pet", { windowId: "pet" });
    const entries = await store.entries<{ windowId: string }>("windows");
    expect(entries.map((e) => e.value.windowId).sort()).toEqual(["main", "pet"]);
  });

  it("batch commits multiple tables", async () => {
    await store.batch([
      { table: "conversations", key: "c1", value: { id: "c1", title: "T" } },
      { table: "messages", key: "c1:m1", value: { id: "m1" } },
    ]);
    expect(await store.get("conversations", "c1")).toEqual({ id: "c1", title: "T" });
    expect(await store.get("messages", "c1:m1")).toEqual({ id: "m1" });
  });

  it("clear wipes a table", async () => {
    await store.put("meta", "x", 1);
    await store.clear("meta");
    expect((await store.entries("meta")).length).toBe(0);
  });
});
