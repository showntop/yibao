/**
 * 应用级 SessionStore 单例：生产用 IndexedDB 引擎。
 * 组件统一从本模块 import sessionStore，不再直接触碰任何存储 API。
 * 测试注入 MemoryKVStore（见各 domain 单测），不 import 本模块。
 */
import { SessionStore } from "./session-store";

export const sessionStore = SessionStore.create();

/** 启动恢复编排：三域并行 hydrate，域级独立容错 */
export function restoreSessionState(): Promise<void> {
  return sessionStore.restore().then(() => undefined);
}

/** 旧 localStorage key 清理：迁移后一次性清除（session-state 重构不兼容旧版，明确不清旧数据） */
const LEGACY_SESSION_KEYS = [
  "yb-session-messages-v1",
  "yb-sessions",
  "yb-active-session",
  "yb-input-draft",
  "yb-capability-panel-v2",
  "yb-capability-scene-v1",
];

export function clearLegacySessionKeys(): void {
  try {
    for (const key of LEGACY_SESSION_KEYS) localStorage.removeItem(key);
  } catch { /* 非浏览器环境 / 存储不可用忽略 */ }
}
