// 与大脑 sidecar 的通信封装。已按职责拆分：
//   - 协议契约/类型     → ../protocol/brain-types
//   - IPC 封装/事件订阅 → ../services/brainClient
//   - 待批队列状态机    → ../state/pending
// 本文件为兼容 re-export 入口（组件继续 from "../lib/brain" 引用不受影响）。
export * from "../protocol/brain-types";
export * from "../services/brainClient";
export * from "../state/pending";
