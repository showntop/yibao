// 运行代数闸（P0，单一事实源）：sidecar 给每个 run 事件盖 conversation_id + run_epoch + seq
// （同会话每次新 run 代数单调递增，seq 在 run 内单调递增）。
// 前端按会话只采纳「见过的最新 epoch」——被抢占旧 run 的迟到事件不得改变 UI 状态。
// 无 run_epoch 的事件（提醒/notice 等非 run 事件、旧 sidecar）一律放行：缺省字段稳健。

/** 判定所需的最小事件面（BrainEvent 的结构子集）。 */
export interface RunEpochEvent {
  conversationId?: string;
  run_epoch?: number;
  seq?: number;
}

interface ConvEpoch {
  /** 已采纳的最新 epoch（null = 还没见过带代数的事件） */
  current: number | null;
  /** 地板：发新消息时把当前 epoch 压进来——≤ 地板的都是被抢占旧 run 的迟到事件 */
  floor: number;
}

const _epochs = new Map<string, ConvEpoch>();

/**
 * 事件是否来自更旧 epoch（true = 丢弃；调用方不得让它改 UI，可记 debug 日志）。
 * 首个带 epoch 的事件/更高 epoch 被采纳并更新账本。
 */
export function isStaleRunEvent(e: RunEpochEvent): boolean {
  const epoch = e.run_epoch;
  if (epoch === undefined || epoch === null) return false;
  const key = e.conversationId ?? "";
  let rec = _epochs.get(key);
  if (!rec) {
    _epochs.set(key, { current: epoch, floor: -1 });
    return false;
  }
  if (epoch <= rec.floor) return true;
  if (rec.current !== null && epoch < rec.current) return true;
  rec.current = epoch;
  return false;
}

/**
 * 本会话发出新消息：旧 run 的事件即时作废（不等新 run 首个事件，关上「发送→受理」竞态窗）。
 * sidecar 受理即分配更高 epoch，其事件到来时被正常采纳。
 */
export function noteRunSubmitted(conversationId?: string | null): void {
  const rec = _epochs.get(conversationId ?? "");
  if (rec && rec.current !== null) rec.floor = Math.max(rec.floor, rec.current);
}

/** 大脑重启（sidecar 代数清零重来）→ 前端账本清零，否则新 brain 的事件全被误判成旧账。 */
export function resetRunEpochs(): void {
  _epochs.clear();
}
