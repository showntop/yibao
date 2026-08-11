/** SessionState 体系统一出口 */
export { SessionStore } from "./session-store";
export type { SessionStoreOptions } from "./session-store";
export { orchestrateRestore } from "./restore-orchestrator";
export type { RestoreReport } from "./restore-orchestrator";
export { IdbKVStore, MemoryKVStore, TABLES, DB_NAME, DB_VERSION } from "./persist-engine";
export { registerDomain, getDescriptor, validateRecord, roughBytes, withinQuota, withSavedAt } from "./schema-registry";
export type { RecordKey, ValidateResult } from "./schema-registry";
export { newId } from "./domains/conversation";
export type { MessageInput, ConversationOptions, SurfaceOptions, WindowOptions, SurfaceSnapshot } from "./domains";
export type {
  KVStore,
  DomainId,
  DomainDescriptor,
  Message,
  MessagePayload,
  MessageRole,
  ProcProjection,
  RunRef,
  ConversationMeta,
  ConversationUIState,
  PendingApproval,
  ProcessedItem,
  SurfaceScene,
  SurfacePanel,
  SurfaceInteract,
  WindowId,
  WindowState,
} from "./types";
