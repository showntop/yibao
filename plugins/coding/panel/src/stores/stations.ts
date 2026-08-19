// 工位布局 store(R4 阶段三):工位增删/绑定/聚焦 + sid→工位路由表(demux 用)
// + 未绑工位 sid 的左栏派生活体(running/waiting/idle)。
// 未绑 sid 不缓冲全量事件:后端 _stream 逐条落 messages 表(coding.py _persist),
// 绑定时 resumeSession 拉全量历史即等价回放对齐——此处只需状态派生供左栏。
import { reactive } from "vue";

export const MAX_STATIONS = 3;

export interface Station { id: number; boundSid: string | null; boundAgent: string }
export type RailLive = "running" | "waiting" | "idle";

export interface StationsState {
  stations: Station[];
  focusId: number;
  railLive: Record<string, RailLive>;
}

export function createStationsStore() {
  const state = reactive<StationsState>({
    // 默认单工位(验收样式收敛:空工位大片灰底显乱;多工位经「+ 新工位」按需加)
    stations: [{ id: 1, boundSid: null, boundAgent: "claude-code" }],
    focusId: 1,
    railLive: {},
  });
  let seq = 1;

  const byId = (id: number) => state.stations.find((s) => s.id === id) ?? null;

  function focus(id: number) { if (byId(id)) state.focusId = id; }

  function addStation(): number | null {
    if (state.stations.length >= MAX_STATIONS) return null; // 第 4 栏不可加
    const id = ++seq;
    state.stations.push({ id, boundSid: null, boundAgent: "claude-code" });
    state.focusId = id;
    return id;
  }

  function removeStation(id: number): boolean {
    if (state.stations.length <= 1) return false;
    unbind(id);
    state.stations.splice(state.stations.findIndex((s) => s.id === id), 1);
    if (state.focusId === id) state.focusId = state.stations[0]!.id;
    return true;
  }

  function stationForSid(sid: string): number | null {
    for (const s of state.stations) if (s.boundSid === sid) return s.id;
    return null;
  }

  function unbind(id: number) {
    const st = byId(id);
    if (st) { st.boundSid = null; }
  }

  function bind(id: number, sid: string, agent: string) {
    const prev = stationForSid(sid);
    if (prev !== null && prev !== id) unbind(prev); // 一个会话至多占一个工位
    const st = byId(id);
    if (!st) return;
    st.boundSid = sid;
    st.boundAgent = agent;
    delete state.railLive[sid]; // 绑上后状态由工位流直接可见
  }

  /** 站内会话自愈同步(newChat/handoff/start 换 sid 后 StationView watch 上报) */
  function syncStationSid(id: number, sid: string | null, agent?: string) {
    const st = byId(id);
    if (!st) return;
    st.boundSid = sid;
    if (sid && agent) st.boundAgent = agent;
  }

  /** 未绑工位 sid 的左栏派生活体;已绑 sid 不受理(工位流内自见) */
  function bumpRail(sid: string, kind: string) {
    if (stationForSid(sid) !== null) return;
    if (kind === "permission_request") state.railLive[sid] = "waiting";
    else if (kind === "done" || kind === "stopped" || kind === "error") state.railLive[sid] = "idle";
    else state.railLive[sid] = "running";
  }

  /** 加入工位目标:聚焦工位空→聚焦;否则首个空;都无→聚焦(换绑) */
  function pickBindTarget(): number {
    const focused = byId(state.focusId);
    if (focused && !focused.boundSid) return focused.id;
    const empty = state.stations.find((s) => !s.boundSid);
    return (empty ?? focused ?? state.stations[0]!).id;
  }

  return { state, focus, addStation, removeStation, bind, unbind, syncStationSid, stationForSid, bumpRail, pickBindTarget };
}
