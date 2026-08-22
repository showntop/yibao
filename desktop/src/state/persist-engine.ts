/**
 * PersistEngine：KVStore 的持久化实现。
 *
 * - IdbKVStore：IndexedDB 生产实现（容量大、异步、事务、增量 put）。
 * - MemoryKVStore：内存实现（单测注入 / 非浏览器降级）。
 *
 * 领域逻辑只依赖 KVStore 接口，不感知具体实现 —— 单测注入 MemoryKVStore，
 * 引擎本身用 fake-indexeddb 单独测 IdbKVStore。
 */
import type { KVStore } from "./types";

/** 表名常量：与 DB schema 一一对应 */
export const TABLES = {
  meta: "meta",
  conversations: "conversations",
  messages: "messages",
  conversationUi: "conversation-ui",
  surface: "surface",
  windows: "windows",
} as const;

export const DB_NAME = "yibao-session";
export const DB_VERSION = 1;

const ALL_TABLES = Object.values(TABLES);

function idbRequest<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error("indexeddb request failed"));
  });
}

/** IndexedDB 实现：每个 table 一个 object store，字符串主键 */
export class IdbKVStore implements KVStore {
  private readonly dbPromise: Promise<IDBDatabase>;

  constructor(dbName = DB_NAME, version = DB_VERSION, tables: readonly string[] = ALL_TABLES) {
    this.dbPromise = this.open(dbName, version, tables);
  }

  private open(dbName: string, version: number, tables: readonly string[]): Promise<IDBDatabase> {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(dbName, version);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const table of tables) {
          if (!db.objectStoreNames.contains(table)) db.createObjectStore(table);
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error ?? new Error("indexeddb open failed"));
    });
  }

  private async withStore<T>(table: string, mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await this.dbPromise;
    const tx = db.transaction(table, mode);
    const result = idbRequest(fn(tx.objectStore(table)));
    return result;
  }

  get<T>(table: string, key: string): Promise<T | null> {
    return this.withStore<T | undefined>(table, "readonly", (s) => s.get(key)).then((v) => v ?? null);
  }

  async put(table: string, key: string, value: unknown): Promise<void> {
    await this.withStore(table, "readwrite", (s) => s.put(value, key));
  }

  async delete(table: string, key: string): Promise<void> {
    await this.withStore(table, "readwrite", (s) => s.delete(key));
  }

  async clear(table: string): Promise<void> {
    await this.withStore(table, "readwrite", (s) => s.clear());
  }

  async entries<T>(table: string): Promise<Array<{ key: string; value: T }>> {
    const db = await this.dbPromise;
    const tx = db.transaction(table, "readonly");
    const req = tx.objectStore(table).getAll();
    const keysReq = tx.objectStore(table).getAllKeys();
    const [values, keys] = await Promise.all([idbRequest(req), idbRequest(keysReq)]);
    return values.map((value, i) => ({ key: String(keys[i]), value: value as T }));
  }

  async batch(ops: Array<{ table: string; key: string; value?: unknown }>): Promise<void> {
    if (ops.length === 0) return;
    const db = await this.dbPromise;
    const tables = [...new Set(ops.map((op) => op.table))];
    const tx = db.transaction(tables, "readwrite");
    for (const op of ops) {
      const store = tx.objectStore(op.table);
      if (op.value === undefined) store.delete(op.key);
      else store.put(op.value, op.key);
    }
    await new Promise<void>((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error ?? new Error("indexeddb batch failed"));
      tx.onabort = () => reject(tx.error ?? new Error("indexeddb batch aborted"));
    });
  }
}

/** 内存实现：单测注入 / 启动降级路径（不落盘） */
export class MemoryKVStore implements KVStore {
  private readonly data = new Map<string, Map<string, unknown>>();

  private table(name: string): Map<string, unknown> {
    let m = this.data.get(name);
    if (!m) {
      m = new Map();
      this.data.set(name, m);
    }
    return m;
  }

  async get<T>(table: string, key: string): Promise<T | null> {
    const v = this.table(table).get(key);
    return (v === undefined ? null : v) as T | null;
  }

  async put(table: string, key: string, value: unknown): Promise<void> {
    this.table(table).set(key, value);
  }

  async delete(table: string, key: string): Promise<void> {
    this.table(table).delete(key);
  }

  async clear(table: string): Promise<void> {
    this.table(table).clear();
  }

  async entries<T>(table: string): Promise<Array<{ key: string; value: T }>> {
    return [...this.table(table).entries()].map(([key, value]) => ({ key, value: value as T }));
  }

  async batch(ops: Array<{ table: string; key: string; value?: unknown }>): Promise<void> {
    for (const op of ops) {
      if (op.value === undefined) this.table(op.table).delete(op.key);
      else this.table(op.table).set(op.key, op.value);
    }
  }

  /** 测试辅助：直接窥视表内容 */
  dump(table: string): Map<string, unknown> {
    return new Map(this.table(table));
  }
}
