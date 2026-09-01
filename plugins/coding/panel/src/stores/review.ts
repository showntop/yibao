// review 聚合 store(R4 阶段四 T2):跨工位待批聚合壳层——permission_request 入列、
// 裁决(approve/deny/timeout/stop 放行)出列、perm_pending 快照全量对账。
// 未绑会话的待批同样入列(sid 直记,与工位绑定无关);groups 按 sid 分组供 review 栏分节渲染。
import { computed, reactive } from "vue";
import type { ComputedRef } from "vue";

export interface ReviewItem {
  rid: string;
  sid: string;
  tool: string;
  summary: string;
  params: Record<string, string>;
}

export interface ReviewState { items: ReviewItem[] }

export function createReviewStore() {
  const state = reactive<ReviewState>({ items: [] });

  /** 待批入列:rid 幂等(同 rid 原位覆盖,不重复入列) */
  function upsert(item: ReviewItem) {
    const i = state.items.findIndex((x) => x.rid === item.rid);
    if (i >= 0) state.items[i] = item;
    else state.items.push(item);
  }

  /** 裁决出列:无此 rid 静默(双通道裁决/快照对账可能重复到达) */
  function resolve(rid: string) {
    const i = state.items.findIndex((x) => x.rid === rid);
    if (i >= 0) state.items.splice(i, 1);
  }

  /** 按会话清空(F5):会话终态(done/stopped/error)时挂起的待批项随会话终结一并出列——
   *  中断/出错路径后端会补 permission_done(deny),但面板晚收/丢事件时卡片会滞留死栏;
   *  幂等:无此 sid 的条目则无操作 */
  function dropSession(sid: string) {
    const i = state.items.findIndex((x) => x.sid === sid);
    if (i < 0) return;
    state.items = state.items.filter((x) => x.sid !== sid);
  }

  /** 挂载同步:perm_pending 快照全量替换(对账启动前漏收的事件) */
  function snapshot(items: ReviewItem[]) {
    state.items.splice(0, state.items.length, ...items);
  }

  // 按 sid 分组:组序 = sid 首次出现序,组内保插入序
  const groups: ComputedRef<Array<{ sid: string; items: ReviewItem[] }>> = computed(() => {
    const out: Array<{ sid: string; items: ReviewItem[] }> = [];
    const idx = new Map<string, number>();
    for (const it of state.items) {
      let gi = idx.get(it.sid);
      if (gi === undefined) {
        gi = out.length;
        idx.set(it.sid, gi);
        out.push({ sid: it.sid, items: [] });
      }
      out[gi]!.items.push(it);
    }
    return out;
  });

  return { state, upsert, resolve, dropSession, snapshot, groups };
}
