<script setup lang="ts">
// coding:studio 多工位壳(R4 阶段三 T6,替换 T4 过渡壳)——
//   demux:onInit 唯一入口(T4 评审收口:StationView 不再自注册)。attach 载荷(任务卡/会话墙
//     「接管」路由)→ stationForSid 已绑短路聚焦(不跳槽,不重拉)/ 未绑 → pickIdleTarget
//     (T8:busy 守卫,全忙不绑不投+聚焦工位提示)+ bind + bindSession(拒理回滚路由表)+
//     focus;流事件 → stationForSid
//     已绑投递 / 未绑 bumpRail 派生左栏活体 + 400ms 防抖刷新。
//   预挂载 stash:壳 onInit 在 setup 顶层注册,init 数据可能早于 StationView 挂载到达——
//     stationRef 缺失时按工位暂存(每工位 ≤20 条,超出丢最旧),ref 登记时 flush。
//   左栏编排:coding.sessions quiet 别名拉取(coding.list 本体带 panel 事件,会把插件页顶成
//     coding 面板);title/subtitle 对齐 wall_data 语义;已绑行活体直读工位态。
//   聚焦停靠:每工位各自 Composer,非聚焦工位 footer 隐藏(实例存活,草稿保留),聚焦者
//     footer 绝对定位停靠 stations 区页底;--dock-h 由聚焦工位 dockH 驱动。
//   窄窗(matchMedia "(max-width: 720px)"):rail 隐藏 + ☰ 开抽屉,v-show 只留聚焦工位。
//   review 栏(R4 阶段四 T5):demux tap permission_request/permission_done 入出列(已绑 sid
//     照常投递工位,waiting 态工位自维护)+ perm_pending 挂载快照对账;宽窗右栏 260px 仅
//     有待批出列,窄窗 stations 右上「审批 N」徽按钮开 drawer(裁决清空自动收)。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { hasBridge, invoke, onInit } from "./lib/bridge";
import type { PanelData, SessionRow } from "./lib/types";
import { permSummary, relTime } from "./lib/format";
import { normAgent } from "./stores/drivers";
import { createStationsStore, MAX_STATIONS, type RailLive } from "./stores/stations";
import { createReviewStore, type ReviewItem } from "./stores/review";
import StationView from "./components/StationView.vue";
import SessionRail, { LIVE_TEXT, type RailRow } from "./components/SessionRail.vue";
import ReviewRail from "./components/ReviewRail.vue";

const stations = createStationsStore();
const review = createReviewStore();

// StationView defineExpose 的壳侧视图(以磁盘实装为准;expose 代理 unwrap ref,dockH 直读数字)
interface StationViewExposed {
  state: { waiting: boolean; streaming: boolean; sending: boolean };
  dockH: number;
  isBusy: boolean;
  onData: (d: PanelData) => void;
  bindSession: (sid: string, agent: string) => boolean; // T8:受理 true/守卫拒绝 false(壳据此回滚路由表)
  unbindSession: () => void;
  hint: (text: string, err?: boolean) => void;          // T8:壳侧状态行提示通道
}

// v-for 函数 ref 收集(不收进 reactive,避免组件代理被二次包裹;refsVersion 供 dockH 重算)
const stationRefs: Record<number, StationViewExposed | null> = {};
const refsVersion = ref(0);
// 预挂载 stash:stationRef 缺失时的载荷暂存(T4 评审收口),ref 登记 flush;每工位 ≤20 条丢最旧
const STASH_MAX = 20;
const preMountStash = new Map<number, PanelData[]>();

function setStationRef(id: number, el: unknown) {
  const r = (el as StationViewExposed | null) ?? null;
  stationRefs[id] = r;
  refsVersion.value++;
  if (!r) return;
  const q = preMountStash.get(id);
  if (q) {
    preMountStash.delete(id);
    for (const d of q) r.onData(d); // stash 整载荷回放:attach 走 handleData 的 attach 分支自恢复
  }
}

// 已绑工位投递:ref 在 → 直投 onData;缺失(预挂载窗)→ 入 stash
function deliverToStation(id: number, d: PanelData) {
  const r = stationRefs[id];
  if (r) { r.onData(d); return; }
  const q = preMountStash.get(id) ?? [];
  q.push(d);
  if (q.length > STASH_MAX) q.shift();
  preMountStash.set(id, q);
}

// attach/join 共用目标选择(T8 修复):pickBindTarget 落 busy 工位会被 bindSession 拒理,
// 路由表已绑但工位未绑即成黑洞——改挑首个非 busy 工位;全忙则不绑不投,聚焦工位状态行提示
function pickIdleTarget(): number | null {
  const t = stations.pickBindTarget();
  if (!stationRefs[t]?.isBusy) return t;
  const idle = stations.state.stations.find((s) => !stationRefs[s.id]?.isBusy);
  if (idle) return idle.id;
  stationRefs[stations.state.focusId]?.hint("所有工位都在忙，先停止一个再加入会话", true);
  return null;
}

// attach/join 共用落位:路由表先绑 → 工位 bindSession;双保险——拒理(busy 守卫)时回滚
// 路由表(仅当该工位路由表里仍是本 sid,防误滚新绑)。返回 false = 未绑成(调用方不聚焦不投递)
function bindStation(target: number, sid: string, agent: string): boolean {
  stations.bind(target, sid, agent);
  const r = stationRefs[target];
  if (!r || r.bindSession(sid, agent)) return true;
  if (stations.state.stations.find((s) => s.id === target)?.boundSid === sid) stations.unbind(target);
  return false;
}

// ---- demux(onInit 唯一入口)----
onInit((data) => {
  const d = data as PanelData;
  if (!d) return;
  if (d.attach === true && !d.event) {           // attach 载荷(任务卡/会话墙「接管」路由)
    const sid = String(d.session_id || "");
    if (!sid) return;
    const ex = stations.stationForSid(sid);         // 终审修复:已绑 sid 再 attach 会改绑别工位,原工位成 ghost
    if (ex !== null) { stations.focus(ex); return; } // 已绑即聚焦,不跳槽;attach 语义=聚焦,不重拉历史(工位内会话流是活的)
    const agent = normAgent(String(d.agent || ""));
    const target = pickIdleTarget();             // T8:落 busy 工位会被拒理成黑洞,先挑非 busy
    if (target === null) return;                 // 全忙:不绑不投(聚焦工位已亮提示)
    if (!bindStation(target, sid, agent)) return; // bindSession 拒理,路由表已回滚
    if (!stationRefs[target]) deliverToStation(target, d); // 工位未挂载:stash,挂载 flush 经 handleData attach 分支恢复
    stations.focus(target);
    return;
  }
  const sid = d.session_id ? String(d.session_id) : "";
  if (!sid || !d.event) return;
  const ev = d.event;                            // review 栏 tap(T5):工位路由之前,全量 sid(含未绑)入出列
  if (ev.kind === "permission_request") {
    review.upsert({ rid: String(ev.rid || ""), sid, tool: String(ev.tool || ""),
                    summary: permSummary(String(ev.tool || ""), ev.input), params: {} });
  } else if (ev.kind === "permission_done") {
    review.resolve(String(ev.rid || ""));
  }
  const id = stations.stationForSid(sid);
  if (id !== null) deliverToStation(id, d);      // 已绑工位:投递
  else {                                          // 未绑:左栏派生 + 终态/陌生 sid 防抖刷新
    stations.bumpRail(sid, d.event.kind);
    scheduleRailRefresh();
  }
});

// ---- 左栏编排 ----
const railRows = ref<RailRow[]>([]);
const lastCwd = ref(""); // refreshRail 首行(最近会话)cwd 缓存,新工位 defaultCwd 预填

let railTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleRailRefresh() {                     // 400ms 防抖(对齐 wall 刷新惯例)
  if (railTimer) clearTimeout(railTimer);
  railTimer = setTimeout(() => { railTimer = null; void refreshRail(); }, 400);
}

async function refreshRail() {
  if (!hasBridge) return;
  try {
    const r = await invoke<{ sessions?: SessionRow[] }>("coding.sessions", {}); // quiet 别名(不用 coding.list)
    const rows = (r && r.sessions) || [];
    if (rows.length && rows[0]!.cwd) lastCwd.value = String(rows[0]!.cwd);
    railRows.value = rows.map((row) => {
      const sid = String(row.id || "");
      const boundId = stations.stationForSid(sid);
      const live = boundId !== null ? liveOfStation(boundId) : (stations.state.railLive[sid] ?? normLive(row.live));
      return { id: sid, title: railTitle(row), subtitle: railSubtitle(row, live), agent: normAgent(String(row.agent || "")), live, boundStationId: boundId };
    });
  } catch { /* 静默:下轮事件再刷 */ }
}

// 已绑工位活体直读工位内状态(waiting > streaming/sending > idle)
function liveOfStation(id: number): RailLive {
  const st = stationRefs[id]?.state;
  if (!st) return "idle";
  if (st.waiting) return "waiting";
  if (st.streaming || st.sending) return "running";
  return "idle";
}

// 后端 _live_state 字串直通("waiting"/"running"/"idle"),缺省 "idle"
function normLive(v: string): RailLive { return v === "waiting" || v === "running" ? v : "idle"; }

// title/subtitle 对齐 wall_data(coding.py WallDataSkill):basename+prompt 前 20 字 / 引擎·活体·相对时间
function railTitle(row: SessionRow): string {
  const cwd = String(row.cwd || "");
  const normed = cwd.replace(/\/+$/, "") || cwd;   // normpath 去尾斜杠;根目录回退原值
  const base = cwd ? (normed.split("/").pop() || normed) : "?";
  const prompt = String(row.prompt || "").slice(0, 20);
  return prompt ? base + " · " + prompt : base;
}
function railSubtitle(row: SessionRow, live: RailLive): string {
  const engine = String(row.agent || "") === "codex" ? "Codex" : "CC"; // 无 agent 老行按 CC
  return engine + " · " + LIVE_TEXT[live] + " · " + relTime(Date.now(), row.created_at);
}

// ---- 行动作 ----
function join(sid: string, agent: string) {        // 未绑行点击:加入工位(等价 attach 路径)
  const ex = stations.stationForSid(sid);           // 未绑行才有 join,但 400ms 防抖窗内行数据可能陈旧,短路兜底
  if (ex !== null) { stations.focus(ex); return; } // 已绑即聚焦,不跳槽
  const t = pickIdleTarget();                      // T8:落 busy 工位会被拒理成黑洞,先挑非 busy
  if (t === null) return;                          // 全忙:不绑不投(聚焦工位已亮提示)
  if (!bindStation(t, sid, agent)) return;         // bindSession 拒理,路由表已回滚
  if (!stationRefs[t]) deliverToStation(t, { session_id: sid, agent, attach: true });
  stations.focus(t);
}
function stopSession(sid: string) {                 // 行内「停止」(主要服务未绑行;已绑工位内另有入口)
  void invoke("coding.stop", { id: sid }).catch(() => {});
}
function onNewStation() {
  stations.addStation();                            // 满 3 返回 null;rail-add 已 disabled 兜底
  drawerOpen.value = false;                         // 抽屉模式加完即收,落新工位
}
function onFocusStation(id: number) { stations.focus(id); }

// 移出:已绑 → 工位内解绑 + 路由表解绑;空工位且 >1 → removeStation;仅剩 1 个空工位 → 无操作。
// busy 期只触发工位内提示(unbindSession 的 busy 守卫),路由表不动——防流还在跑路由先断
function onRemoveStation(id: number) {
  const st = stations.state.stations.find((s) => s.id === id);
  if (!st) return;
  if (st.boundSid) {
    const r = stationRefs[id];
    if (r?.isBusy) { r.unbindSession(); return; }
    r?.unbindSession();
    stations.unbind(id);
  } else if (stations.state.stations.length > 1) {
    stations.removeStation(id);
  }
}

// sid-change 上报(T4 Minor 修复:sid=null 时 agent 是陈旧值,忽略只解绑);
// 触发防抖刷新——新会话入库后左栏可见
function onSidChange(id: number, sid: string | null, agent: string) {
  stations.syncStationSid(id, sid, sid ? agent : undefined);
  scheduleRailRefresh();
}

// ---- review 栏(R4 阶段四 T5):跨工位待批聚合的壳侧接线 ----
// 挂载快照:perm_pending 全量对账(面板晚开错过 permission_request 时补全);失败静默——
// 后续增删由流事件 tap 兜底(permission_request 增 / permission_done 删)
async function syncReviewPending() {
  if (!hasBridge) return;
  try {
    const r = await invoke<{ pending?: ReviewItem[] }>("coding.perm_pending", {});
    review.snapshot((r && r.pending) || []);
  } catch { /* 静默:流事件兜底后续增删 */ }
}

// 裁决:成功路径不本地移除,等 permission_done 流事件驱动出列(单一事实源);
// catch(已超时/已被他通道裁决)→ 本地 resolve 兜底(store 幂等,重复出列静默)
async function onDecide(rid: string, allow: boolean) {
  try { await invoke("coding.decide", { rid, allow }); }
  catch { review.resolve(rid); }
}
// 组头「全批」:逐 rid 走 onDecide(每条独立裁决/独立兜底)
function onDecideGroup(sid: string, allow: boolean) {
  for (const it of review.state.items.filter((x) => x.sid === sid)) void onDecide(it.rid, allow);
}

// groups props 装配:label = 已绑「工位 N」/ 未绑 sid 前 8 位;stationId 经路由表现算
const reviewGroups = computed(() =>
  review.groups.value.map((g) => {
    const stationId = stations.stationForSid(g.sid);
    return { ...g, stationId, label: stationId !== null ? `工位 ${stationId}` : g.sid.slice(0, 8) };
  }),
);

// 窄窗 review drawer:stations 右上「审批 N」徽按钮开;drawer 内裁决清空后自动收
const reviewDrawerOpen = ref(false);
watch(() => review.state.items.length, (n) => { if (!n) reviewDrawerOpen.value = false; });

// ---- 聚焦停靠:--dock-h 由聚焦工位 dockH 驱动(refsVersion 保证 ref 登记后重算) ----
const dockH = computed(() => {
  void refsVersion.value;
  return stationRefs[stations.state.focusId]?.dockH || 150;
});

// ---- 窄窗自适应:rail 隐藏 + ☰ 开抽屉,v-show 只留聚焦工位 ----
const narrow = ref(false);
const drawerOpen = ref(false);
let mq: MediaQueryList | null = null;
function onMqChange() {
  narrow.value = !!mq?.matches;
  if (!narrow.value) { drawerOpen.value = false; reviewDrawerOpen.value = false; } // 回宽窗收抽屉,rail/review 回侧栏
}

onMounted(() => {
  if (typeof matchMedia !== "undefined") {
    mq = matchMedia("(max-width: 720px)");
    onMqChange();
    mq.addEventListener("change", onMqChange);
  }
  void refreshRail(); // 挂载即刷
  void syncReviewPending(); // review 栏挂载快照(T5)
});
onBeforeUnmount(() => {
  mq?.removeEventListener("change", onMqChange);
  if (railTimer) clearTimeout(railTimer);
});
</script>

<template>
  <div class="shell" :class="{ narrow, 'has-review': review.state.items.length > 0 }">
    <SessionRail
      v-if="!narrow || drawerOpen"
      :rows="railRows"
      :stations="stations.state.stations"
      :focus-id="stations.state.focusId"
      :drawer="narrow"
      :add-disabled="stations.state.stations.length >= MAX_STATIONS"
      @join="join"
      @stop="stopSession"
      @new-session="onNewStation"
      @focus-station="onFocusStation"
      @close-drawer="drawerOpen = false"
    />
    <div class="stations" :style="{ '--dock-h': dockH + 'px' }">
      <!-- 窄窗 ☰:开左栏抽屉(壳绝对定位于 stations 左上角,仅 narrow 渲染;聚焦切换在抽屉内完成) -->
      <button v-if="narrow" type="button" class="rail-toggle" title="会话列表" @click="drawerOpen = true">☰</button>
      <!-- 窄窗「审批 N」徽按钮(T5):与 ☰ 对称(stations 右上角),仅有待批时现身,点击开 review drawer -->
      <button
        v-if="narrow && review.state.items.length" type="button" class="review-toggle"
        title="有待批的权限请求" @click="reviewDrawerOpen = true"
      >审批 {{ review.state.items.length }}</button>
      <!-- autoplay 仅 1 号工位:id 单调分配不复用 + v-show 只切显隐不重挂载,s.id===1 即首挂载 -->
      <StationView
        v-for="s in stations.state.stations" :key="s.id"
        :ref="(el) => setStationRef(s.id, el)"
        v-show="!narrow || s.id === stations.state.focusId"
        :class="{ focused: s.id === stations.state.focusId }"
        :focused="s.id === stations.state.focusId"
        :autoplay="s.id === 1"
        :default-cwd="lastCwd"
        @sid-change="(sid, agent) => onSidChange(s.id, sid, agent)"
        @request-focus="stations.focus(s.id)"
        @request-remove="onRemoveStation(s.id)"
      />
    </div>
    <!-- 统一 review 栏(T5):宽窗右栏 260px 仅有待批出列(自动进出);窄窗改 drawer 模式(徽按钮开) -->
    <ReviewRail
      v-if="review.state.items.length > 0 && (!narrow || reviewDrawerOpen)"
      :groups="reviewGroups"
      :drawer="narrow"
      @decide="onDecide"
      @decide-group="onDecideGroup"
      @close-drawer="reviewDrawerOpen = false"
    />
  </div>
</template>
