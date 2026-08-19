<script setup lang="ts">
// coding:studio(R4 阶段三 T4):StationView——多工位的工位单元。原 App.vue 单会话接线整体下沉
// (store 创建/头部控件/浮层互收/esc 优先级/成本/状态行/cwd chip+浮层/autoReplay/引擎 picker/
// mode pill/接续浮层/Codex→CC 交接/⏪/Composer 接线/RunPill 布局/RO/生命周期),单工位态行为
// 与阶段二等价。T3 后 takeover 退役:onInit 不再读 takeover 标志(数据直投 store.handleData),
// onHostMessage 段整段不搬(宿主消息通道随 takeover 退役无消费方)。
// T6 后 onInit 注册收归壳(App.vue demux 唯一入口),本组件不再自注册——init 数据只经
// expose 的 onData 收壳投递。
// 工位接口(T5/T6 壳依赖):
//   props  { focused, autoplay, defaultCwd }——focused=false 时文档级 esc/click-outside 监听
//          直接 return(多实例共存只有聚焦工位响应);autoplay=false 时 onMounted 不跑
//          prefillCwd/autoReplay,仅把 defaultCwd 预填进 cwd(非空才填,不回放);
//   expose { state, dockH, onData, bindSession, unbindSession, stop, isBusy, hint };
//   emits  sid-change(sid, agent) / request-focus() / request-remove()。
// T4 行为修订:busy(sending||streaming)期 onSend 不再静默丢弃——过与空闲同组校验后
// store.queueInput 入队(终态自动泄放),状态行提示「已排队，本轮结束后自动发送」。
// 行为逐条对齐 chat.html:
//   handoffSend(:1964-1991,chip 跨引擎):占 sending 窗 coding.session_brief;等待期改主意
//     (currentSession/switchAgent 变)丢弃;成功 → 旧 sid 进 discarded + marker + send 走 start
//     (isStart 分支带 mode+agent)。编排在 store.handoffSend,switchAgent 选择态经回调同步。
//   Codex→CC(:2047-2319):handoff_list → 0 提示/1 直进/多 picker → handoff_brief(失败也开卡)
//     → HandoffCard(可编辑/取消/用它开始)→ store.startHandoffSession(coding.start 带
//     source=codex:sid,不带 mode/agent;受理前即 streaming,秒败竞态同 send)。
//   rewind(:958-987):用户气泡 uuid 挂 ⏪;coding.rewind {id,user_msg_id};rewind_ok 经事件流回。
//   接续浮层(:2335-2554):两路并发 last_sessions(失败降级 null)+list;区 1 CC/Codex 卡,
//     区 2 译宝历史行(normCwd 过滤);两区皆空空态;overlay 空白关闭。chip 过滤通道(无调用方)不移植。
//   autoReplay(:2869-2885):纯函数抽至 lib/replay.ts(pickReplayCandidate/shouldYieldReplay/
//     replayStep);空会话顺延;currentSession||isResuming 让位。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { hasBridge, invoke } from "../lib/bridge";
import type { HandoffSessionItem, LastSessions, PanelData, SessionRow } from "../lib/types";
import { doneStatusText, emsg, fmtCost, fmtTok, normCwd } from "../lib/format";
import { pickReplayCandidate, replayStep, shouldYieldReplay } from "../lib/replay";
import { createSessionStore, type HandoffSendResult, type RenderItem } from "../stores/session";
import { agentLabel, createDriversStore, normAgent } from "../stores/drivers";
import MessageList from "./MessageList.vue";
import ErrBar from "./ErrBar.vue";
import RunPill from "./RunPill.vue";
import Composer from "./Composer.vue";
import CwdChip from "./CwdChip.vue";
import ModePill from "./ModePill.vue";
import AgentChip from "./AgentChip.vue";
import StatusLine from "./StatusLine.vue";
import HistoryOverlay from "./HistoryOverlay.vue";
import HandoffPicker from "./HandoffPicker.vue";

const props = withDefaults(defineProps<{
  focused: boolean;   // 聚焦工位才响应文档级 esc/click-outside(多实例共存守卫)
  autoplay: boolean;  // 主工位才 prefillCwd/autoReplay;false 时仅 defaultCwd 预填 cwd
  defaultCwd: string; // 非 autoplay 工位的预填目录(空串不填)
}>(), { focused: false, autoplay: false, defaultCwd: "" });

const emit = defineEmits<{
  "sid-change": [sid: string | null, agent: string]; // watch state.currentSession 上报(壳更新路由表)
  "request-focus": [];  // 工位任意处 mousedown(壳切聚焦)
  "request-remove": []; // 工位头 ✕(壳决定 unbind 或 removeStation)
}>();

const store = createSessionStore({
  invoke: (m, p) => invoke(m, p),
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (t) => clearTimeout(t),
  userEchoFallbackMs: 1500,
  // 恢复历史跟随会话落盘目录(对齐原 resumeSession 的 setCwd(r.cwd));闭包引用下方 cwd,调用远晚于初始化
  onResumedCwd: (c) => { cwd.value = c; },
  // 队列泄放路径的跨引擎交接落定(T9):同 onSend 的 onHandedOff——清待定 + curAgent 同步;
  // 闭包引用下方 switchAgent/drivers,调用远晚于初始化
  onQueueHandoff: (a) => { switchAgent.value = null; drivers.setCurAgent(a); },
});
const state = store.state;

const drivers = createDriversStore({ invoke: (m, p) => invoke(m, p) });
const dstate = drivers.state;

// ---- 头部控件状态(T6):cwd 真值 / 权限模式 / 跨引擎切换待定(curAgent 在 drivers store) ----
const cwd = ref("");
const curMode = ref("acceptEdits");
const switchAgent = ref<string | null>(null); // 老会话跨引擎切换待定(发送时经 store.handoffSend 摘要移植)
// 跨引擎待定随会话切换/清空一并作废(对齐原 newChat/resumeSession 里的 switchAgent=null);
// sid-change 上报壳(T4 追加,壳维护路由表)
watch(() => state.currentSession, (sid) => {
  switchAgent.value = null;
  emit("sid-change", sid, state.curSessAgent);
});

// busy 排队泄放的发送上下文:随 cwd/mode/引擎选择实时快照(原读全局变量,天然实时);
// switchAgent 一并入快照(T9)——泄放路径的跨引擎交接守卫读它(store 侧判据)
watch([cwd, curMode, () => dstate.curAgent, switchAgent], ([c, m, a, sw]) => {
  store.setQueueContext({ cwd: c, mode: m, agent: a, switchAgent: sw });
}, { immediate: true });

// init 数据入口(expose 给壳:T6 壳 demux 后按 sid 投递;onInit 注册已收归壳,T4 评审收口。
// takeover 标志读取随 T3 退役剥离——行为对齐 handleInitData 的单会话处理)
function onData(data: PanelData) { store.handleData(data); }

// 无桥设计预览(T8;对齐 :2922-2939 renderPreviewSample):静态示例对话走同一渲染管线看样式
// (user 气泡 + AI 气泡 + fileedit 卡 + tooluse 卡 + toolresult + done 气泡)
if (!hasBridge) {
  state.items.push(
    { type: "user", text: "帮我看一下 src/main.rs 的入口在哪" },
    { type: "assistant", raw: "我打开项目看一下 main.rs 的结构…", thinking: [], done: true },
    { type: "fileedit", tool: "Read", path: "src/main.rs", old: null, new: null },
    { type: "tool", tool: "Bash", input: { command: "wc -l src/main.rs", description: "统计行数" },
      results: [{ text: "152 src/main.rs", isError: false }], hasError: false },
    { type: "assistant", raw: "入口在 src/main.rs:12 的 fn main()。需要我加点日志吗？", thinking: [], done: true },
  );
}

// ---- 浮层互收:任一 picker/浮层开时关其他(cwd/agent/history/handoff 单枚举兑现) ----
type Layer = "" | "cwd" | "agent" | "history" | "handoff";
const openLayer = ref<Layer>("");
function toggleLayer(l: Exclude<Layer, "">) { openLayer.value = openLayer.value === l ? "" : l; }
// 点外部关浮层(对齐原 document click 关 cwd 浮层;agent picker 有 backdrop,双路关闭幂等);
// focused 守卫(T4):多工位共存只有聚焦工位响应文档级监听
function onDocClick() { if (!props.focused) return; if (openLayer.value) openLayer.value = ""; }
// esc 优先级:agent-picker → history → handoff → stop(cwd 浮层 Esc 由其 input stopPropagation 消费)
function onDocKeydown(e: KeyboardEvent) {
  if (!props.focused) return; // 多工位共存只有聚焦工位响应
  if (e.key !== "Escape") return;
  if (openLayer.value === "agent") { openLayer.value = ""; return; }
  if (openLayer.value === "history") { openLayer.value = ""; return; }
  if (openLayer.value === "handoff") { openLayer.value = ""; return; }
  if (state.currentSession && (state.streaming || state.sending)) void store.stop();
}

// ---- 顶栏:会话成本聚合(C4,对齐 renderCost):tok 与成本都未见 → 空;codex 无 cost → 只 token 段 ----
const costText = computed(() => {
  const u = state.usage;
  if (!u.tok && !u.hasCost) return "";
  return fmtTok(u.tok) + " tok" + (u.hasCost ? " · " + fmtCost(u.cost) : "");
});
const newChatDisabled = computed(() => state.sending || state.streaming || !state.currentSession);

// ---- footer 状态行:提交中(handoffSend 摘要窗「生成交接摘要…」/ startHandoffSession
// invoke 窗「Codex 接续启动中…」)/运行中(spinner)/完成行(doneStatusText,isFinite 守御)/错误 ----
const briefTarget = ref<string | null>(null); // handoffSend 摘要等待窗目标引擎
const handoffStarting = ref(false);           // Codex→CC startHandoffSession invoke 窗
const status = computed(() => {
  if (state.sending) {
    if (briefTarget.value) return { text: "生成交接摘要，转交 " + agentLabel(briefTarget.value) + "…", spin: true, err: false };
    if (handoffStarting.value) return { text: "Codex 接续启动中…", spin: true, err: false };
    return { text: "提交中…", spin: true, err: false };
  }
  if (state.streaming) return { text: (state.runPrefix || "会话") + " 运行中…", spin: true, err: false };
  if (state.error) return { text: state.error, spin: false, err: true };
  if (state.ended === "done") return { text: doneStatusText(state.lastUsage), spin: false, err: false };
  if (state.ended === "stopped") return { text: "已中断", spin: false, err: false };
  return { text: "", spin: false, err: false };
});

// Composer/头部控件瞬时提示与 store 状态行共一位:store 状态每次变化覆盖提示
// (对齐 chat.html setStatus 覆盖语义——后写赢);空文本 = 清除提示(对齐 setStatus(""))
const tip = ref<{ text: string; spin: boolean; err: boolean } | null>(null);
watch(status, () => { tip.value = null; });
const statusView = computed(() => tip.value ?? status.value);
function onComposerStatus(text: string, err: boolean) { tip.value = text ? { text, spin: false, err } : null; }

// ---- cwd chip + 浮层(对齐 :2620-2665 / :2890-2899)----
// 用户主动切换项目(浮层确认/📁 选目录共用入口):换目录 = 换上下文——当前会话按「新对话」清理,
// 再拉列表刷新 chip 默认引擎 + 自动回放新项目最近会话。运行/发送中拒绝切换(防流被截断)。
function commitCwd(v: string) {
  v = (v || "").trim();
  if (!v || v === cwd.value.trim()) return;
  if (state.streaming || state.sending) { onComposerStatus("会话进行中，先停止再切换项目目录", true); return; }
  cwd.value = v;
  store.newChat();
  invoke<{ sessions?: SessionRow[] }>("coding.list", {})
    .then((r) => refreshCwdState((r && r.sessions) || [], v, true))
    .catch(() => { /* 静默 */ });
}
function onCwdCommit(v: string) { openLayer.value = ""; commitCwd(v); } // 原 closeCwdPop(true):无论受理与否都先关浮层
function onCwdBrowse() {
  if (!hasBridge) { onComposerStatus("未连译宝桥（设计预览模式）", true); return; }
  // native:pick_folder 走 WebviewPanel 白名单旁路直调 Tauri;桥 resolve 路径字符串(取消为 null)
  invoke<string | null>("native:pick_folder", {})
    .then((r) => { if (r) { openLayer.value = ""; commitCwd(String(r)); } })
    .catch(() => onComposerStatus("选择文件夹失败", true));
}

// chip 默认引擎 = 该 cwd 最近会话的 agent(list 按时间倒序,首个 cwd 命中行即最近);
// replay=true 时顺带自动回放(初次进入/切换项目共用)
function refreshCwdState(rows: SessionRow[], c: string, replay: boolean) {
  for (const row of rows) {
    if (row && row.cwd === c) { drivers.applyCwdDefault(row.agent); break; }
  }
  if (replay) void autoReplay(rows, c);
}

// 自动回放(对齐 :2869-2885;纯函数已抽 lib/replay.ts,T7):把当前 cwd 最近一条非空会话
// 回放进对话框。空会话(skipIfEmpty → 0)顺延下一条;让位:attach 接管/手动接续抢占
// (currentSession 出现或 resume 在飞)即停——不重入 resumeSession(pendingResume 排队会反抢 attach)。
async function autoReplay(rows: SessionRow[], c: string) {
  let rest = rows;
  for (;;) {
    if (shouldYieldReplay(!!state.currentSession, store.isResuming())) return;
    const cand = pickReplayCandidate(rest, c, dstate.codexAvailable);
    if (!cand) return;
    const n = await store.resumeSession(cand.sid, cand.agent, { skipIfEmpty: true });
    if (replayStep(n) === "stop") return; // >0 已回放;-1 被归并(他路抢占),保底停
    rest = rest.filter((r) => r.id !== cand.sid); // 空会话顺延:剔除已试
  }
}

// 初次进入:预填 cwd(从最近一次会话取,没填过就留空)+ 刷新 chip 默认引擎 + 自动回放
async function prefillCwd() {
  try {
    const r = await invoke<{ sessions?: SessionRow[] }>("coding.list", {});
    const rows = (r && r.sessions) || [];
    if (rows.length && rows[0]!.cwd && !cwd.value) cwd.value = rows[0]!.cwd;
    const c = cwd.value.trim();
    if (c) refreshCwdState(rows, c, true);
  } catch { /* 静默:首次使用无会话 */ }
}

// ---- 引擎 chip + picker(对齐 :1817-1833)----
function onAgentToggle() {
  // 运行/发送中拦截——切换只在一轮对话间隙有意义
  if (state.streaming || state.sending) { onComposerStatus("会话进行中，先停止再切换引擎", true); return; }
  toggleLayer("agent");
}
// picker 选中:无会话 → curAgent(下一个新会话用);有会话——同引擎清除待定,
// 另一引擎置 switchAgent(chip 立即显示选中项;发送时 store.handoffSend 自动交接摘要,以新引擎继续)
function pickAgent(a: string) {
  openLayer.value = "";
  a = normAgent(a);
  if (state.currentSession) {
    switchAgent.value = a === state.curSessAgent ? null : a;
    if (switchAgent.value) onComposerStatus("已选 " + agentLabel(a) + "：发送时自动生成交接摘要，以 " + agentLabel(a) + " 继续", false);
  } else {
    drivers.setCurAgent(a);
    onComposerStatus("新会话引擎：" + agentLabel(a), false);
  }
}

// ---- 权限模式 pill(对齐 :1856-1864):两态切换;有活动会话时同步通知后端(catch 静默) ----
function toggleMode() {
  curMode.value = curMode.value === "plan" ? "acceptEdits" : "plan";
  if (state.currentSession) void invoke("coding.mode", { id: state.currentSession, mode: curMode.value }).catch(() => {});
}

// ---- 接续浮层(T7;对齐 :2335-2554 openHistory/renderResumePopover/attach)----
const historyLoading = ref(false);
const historyLast = ref<LastSessions | null>(null); // 区 1 跨源检测(失败降级 null)
const historyRows = ref<SessionRow[]>([]);          // 区 2 译宝历史(normCwd 过滤后)
const historyListErr = ref<string | null>(null);

function openHistory() {
  if (!hasBridge) return;
  if (state.streaming) { onComposerStatus("当前会话运行中，请先中断再恢复/接续会话", true); return; }
  const c = cwd.value.trim();
  if (!c) { onComposerStatus("请先选择项目目录", true); openLayer.value = "cwd"; return; }
  openLayer.value = "history"; // 互收:agent/handoff/cwd 浮层同收(原 closeHandoffPicker/closeAgentPicker)
  historyLoading.value = true;
  historyLast.value = null;
  historyRows.value = [];
  historyListErr.value = null;
  // 两路并发:区 1 跨源检测失败(方法缺失/扫描异常)静默降级为无区 1,不拖垮区 2
  const lastP = invoke<LastSessions>("coding.last_sessions", { cwd: c }).catch(() => null);
  const listP = invoke<{ sessions?: SessionRow[] }>("coding.list", {})
    .then((r) => ({ rows: (r && r.sessions) || [], err: null as string | null }))
    .catch((e) => ({ rows: [] as SessionRow[], err: emsg(e) }));
  void Promise.all([lastP, listP]).then(([last, listR]) => {
    if (openLayer.value !== "history") return; // 响应到达时浮层已关,丢弃
    historyLast.value = last;
    historyRows.value = listR.rows.filter((r) => normCwd(r.cwd) === normCwd(c)); // 只显示本项目;跨项目总览归左栏 rail
    historyListErr.value = listR.err;
    historyLoading.value = false;
  });
}

// CC 卡 [继续]:attach_cc 把 transcript 导入会话库(幂等)→ resumeSession(原生 resume,上下文完整)
async function onAttachCc(ccSid: string): Promise<boolean> {
  onComposerStatus("导入 Claude Code 会话…", false);
  try {
    const r = await invoke<{ session_id?: string }>("coding.attach_cc", { cc_session_id: ccSid, cwd: cwd.value.trim() });
    const sid = r && r.session_id;
    if (!sid) throw new Error("attach_cc 未返回 session_id");
    openLayer.value = ""; // 浮层关闭(原 resumeSession 内隐藏 overlay,成败都关)
    const n = await store.resumeSession(sid, "claude-code");
    // 恢复成功反馈(对齐原 resumeSession 的 setStatus);失败由 state.error 承载、顺延(-1)不亮
    if (n >= 0 && !state.error) onComposerStatus("已恢复会话 " + sid + "，发消息将在同一上下文继续", false);
    return true;
  } catch (e) {
    onComposerStatus("导入 Claude Code 会话失败：" + emsg(e), true);
    return false; // 组件据此解锁按钮
  }
}

// Codex 卡 [原生续]:attach_codex 用 codex thread_id 建/取 DB 行(幂等,agent="codex")→ resumeSession
async function onAttachCodex(codexSid: string): Promise<boolean> {
  onComposerStatus("恢复 Codex 会话…", false);
  try {
    const r = await invoke<{ session_id?: string }>("coding.attach_codex", { session_id: codexSid });
    const sid = r && r.session_id;
    if (!sid) throw new Error("attach_codex 未返回 session_id");
    openLayer.value = "";
    const n = await store.resumeSession(sid, "codex");
    // 同 onAttachCc:成功才亮「已恢复会话」提示
    if (n >= 0 && !state.error) onComposerStatus("已恢复会话 " + sid + "，发消息将在同一上下文继续", false);
    return true;
  } catch (e) {
    onComposerStatus("恢复 Codex 会话失败：" + emsg(e), true);
    return false;
  }
}

// 区 2 行点击:resumeSession(row.id, row.agent)(原 resumeSession 关闭 overlay)
function onResumeRow(row: SessionRow) {
  openLayer.value = "";
  void store.resumeSession(row.id, row.agent).then((n) => {
    // 同 attach 两路:成功才亮「已恢复会话」提示(对齐原 setStatus)
    if (n >= 0 && !state.error) onComposerStatus("已恢复会话 " + row.id + "，发消息将在同一上下文继续", false);
  });
}

// ---- Codex→CC 交接(T7;对齐 :2047-2119 handoff/picker + :2158-2172 handoffBrief)----
const handoffSessions = ref<HandoffSessionItem[]>([]);

async function startCodexHandoff() {
  if (!hasBridge) { onComposerStatus("未连译宝桥（设计预览模式）", true); return; }
  if (state.streaming) { onComposerStatus("当前会话运行中，请先中断再接续", true); return; }
  const c = cwd.value.trim();
  if (!c) { onComposerStatus("请先选择项目目录", true); openLayer.value = "cwd"; return; }
  openLayer.value = ""; // 两个浮层不共存
  onComposerStatus("读取 Codex 会话列表…", false);
  try {
    const r = await invoke<{ sessions?: HandoffSessionItem[] }>("coding.handoff_list", { cwd: c });
    const sessions = (r && r.sessions) || [];
    if (!sessions.length) { onComposerStatus("该项目没有 Codex session", false); return; }
    tip.value = null; // 原 setStatus("")
    if (sessions.length === 1) void handoffBrief(sessions[0]!.session_id);
    else { handoffSessions.value = sessions; openLayer.value = "handoff"; }
  } catch (e) {
    onComposerStatus("读取 Codex 会话失败：" + emsg(e), true);
  }
}

// handoff_brief → 交接卡;失败也开卡(红条 + 空 textarea 可手动粘贴),不阻断接续流程
async function handoffBrief(sid: string) {
  if (!sid) return;
  onComposerStatus("生成 Codex 交接 brief…", false);
  try {
    const r = await invoke<{ brief?: string; incomplete?: boolean }>("coding.handoff_brief", { session_id: sid, cwd: cwd.value.trim() });
    store.pushHandoffCard(sid, r && r.brief ? r.brief : null, !!(r && r.incomplete), null);
    tip.value = null;
  } catch (e) {
    store.pushHandoffCard(sid, null, false, "读取 brief 失败：" + emsg(e));
    onComposerStatus("读取 brief 失败，可手动粘贴或取消", true);
  }
}

function onHandoffPick(sid: string) { openLayer.value = ""; void handoffBrief(sid); }
function onCodexCardHandoff() { openLayer.value = ""; void startCodexHandoff(); } // 区 1 Codex 卡 [交接给 CC]
function onHandoffCancel(item: RenderItem) { store.dropHandoffCard(item); }

// 交接卡 [用它开始 → Claude Code]:组件已 seal;brief 作 prompt、source 标来源,受理前即 streaming
function onHandoffStart(item: RenderItem, text: string) {
  const c = cwd.value.trim();
  if (!c) { onComposerStatus("内部错误：cwd 丢失", true); return; }
  const sid = item.type === "handoff" ? item.sid : "";
  handoffStarting.value = true; // 状态行「Codex 接续启动中…」(invoke 窗)
  void store.startHandoffSession(c, sid, text).finally(() => { handoffStarting.value = false; });
}

// ---- ⏪ 回滚(T7;对齐 :958-987):用户气泡 uuid 锚;点击防重 → coding.rewind {id, user_msg_id};
//      结果经事件流 rewind_ok 回(marker,store 已处理);失败亮状态行 ----
const rewinding = ref<Set<string>>(new Set());
async function onRewind(uuid: string) {
  const sid = state.currentSession;
  if (!sid || rewinding.value.has(uuid)) return;
  rewinding.value.add(uuid);
  try {
    const r = await invoke<{ ok?: boolean; error?: string }>("coding.rewind", { id: sid, user_msg_id: uuid });
    if (r && r.ok === false) onComposerStatus("回滚失败：" + (r.error || ""), true);
    else onComposerStatus("已提交回滚…", false);
  } catch (e) {
    onComposerStatus("回滚失败：" + emsg(e), true);
  } finally {
    rewinding.value.delete(uuid);
  }
}

// ---- Composer 接线(T5;stop 受理信号修复见 T6)----
const composerRef = ref<{ clear: () => void; focus: () => void; fillDraft: (t: string) => void } | null>(null);
const busy = computed(() => state.sending || state.streaming);

// 跨引擎交接(:1884-1887;T9 抽出):老会话上 chip 选了另一引擎 → handoffSend 摘要移植开新会话
// (不走 coding.send,引擎间无法原生互续);编排在 store,选择态经回调同步。busy 排队泄放路径
// 不走这里——store 侧 pendingSendFromQueue 持同一判据(queueContext.switchAgent 快照)。
// 返回 null = 无待定切换(调用方走普通 send)
async function runHandoff(prompt: string, refs: string[]): Promise<HandoffSendResult | null> {
  if (!(state.currentSession && switchAgent.value && switchAgent.value !== state.curSessAgent)) return null;
  const target = switchAgent.value;
  briefTarget.value = target; // 状态行「生成交接摘要，转交 X…」
  try {
    return await store.handoffSend(target, {
      cwd: cwd.value.trim(), userText: prompt, mode: curMode.value, refs,
      isStale: () => switchAgent.value !== target, // 等待期 chip 改选 → 丢弃
      onHandedOff: () => {
        switchAgent.value = null;
        drivers.setCurAgent(target); // 无会话态默认值同步,防之后新对话落回旧引擎
        briefTarget.value = null;    // 进入正式 send 窗,状态行回到「提交中…」
      },
    });
  } catch (e) {
    onComposerStatus("交接摘要生成失败：" + emsg(e), true); // 原仅状态行,不进 errbar
    return "failed";
  } finally {
    briefTarget.value = null;
  }
}

async function onSend(text: string, refs: string[]) {
  if (!hasBridge) { onComposerStatus("设计预览模式：未连译宝桥，无法发送", true); return; }
  const prompt = text.trim();
  // cwd 空拦阻并开 cwd 浮层;校验顺序对齐原 send():先 cwd 后 prompt(busy 入队同组校验,拒发不入队)
  if (!cwd.value.trim()) { onComposerStatus("请先选择项目目录", true); openLayer.value = "cwd"; return; }
  if (!prompt) { onComposerStatus("请输入任务描述", true); composerRef.value?.focus(); return; }
  // busy 排队(T4 修订,原静默丢弃):校验过了才入队,终态由 store 泄放(对齐原 takeover-input
  // 排队路径:泄放时 cwd/mode/agent/switchAgent 取 queueContext 实时快照,交接守卫 store 侧同判据)
  if (state.sending || state.streaming) {
    store.queueInput(prompt, refs, cwd.value.trim(), curMode.value, dstate.curAgent);
    composerRef.value?.clear(); // 入队即消费 prompt+chips(防滞留被二次发出)
    onComposerStatus("已排队，本轮结束后自动发送", false);
    return;
  }
  const hr = await runHandoff(prompt, refs);
  if (hr !== null) { if (hr === "sent") composerRef.value?.clear(); return; } // 成功才消费 prompt+chips;stale/failed 保留
  try {
    await store.send(cwd.value.trim(), prompt, curMode.value, dstate.curAgent, { refs });
    composerRef.value?.clear(); // 成功才消费 prompt+chips;失败保留可重试(对齐原清空时机)
  } catch { /* 失败文本已由 state.error 进状态行/errbar,prompt+refs 留存 */ }
}

// expose 的 stop = 原 onComposerStop:对齐原 #stop,仅 streaming 期受理(sending 窗会话 id 未回填
// 是死点击)——false = 拒理,Composer 据此立即解锁中断钮(T5 评审修复:锁不再留存过 sending 窗)
function stop(): Promise<boolean> {
  return state.streaming ? store.stop() : Promise.resolve(false);
}

// 壳接口:绑定/解绑会话(T6 工位加入/移出)。busy 守卫——streaming/sending 中拒绝并状态行提示
// (防流被截断,同 commitCwd/openHistory 的拦截语义)。T8:bindSession 返回 boolean
// (受理 true/守卫拒绝 false),壳按返回值回滚路由表
function bindSession(sid: string, agent: string): boolean {
  if (state.sending || state.streaming) { onComposerStatus("会话进行中", true); return false; }
  void store.resumeSession(sid, agent);
  return true;
}
function unbindSession() {
  if (state.sending || state.streaming) { onComposerStatus("会话进行中", true); return; }
  store.newChat();
}
// 壳侧提示通道(T8):壳(路由守卫等)借本工位状态行亮瞬时提示——内调 onComposerStatus(tip 机制)
function hint(text: string, err = false) { onComposerStatus(text, err); }

// ---- RunPill 布局(C3):bottom = footer 高 + 10 + (errbar 可见时)errbar 高,现算现贴 ----
const footerEl = ref<HTMLElement | null>(null);
const errbarRef = ref<{ root: HTMLElement | null } | null>(null);
const pillBottom = ref(110);
const pillVisible = computed(() => state.sending || state.streaming);
// footer 实时高度(T4 expose 给壳做停靠布局;与 pill 同一 RO/nextTick 节奏刷新)
const dockH = ref(0);

function relayout() {
  void nextTick(() => {
    const fh = footerEl.value ? footerEl.value.offsetHeight : 0;
    dockH.value = fh;
    let b = fh + 10;
    const e = errbarRef.value?.root;
    if (e) b += e.offsetHeight;
    pillBottom.value = b;
  });
}

// errbar 出现/消失(内容变化)→ 重算;详情开合由 ErrBar 的 layout 事件上来
watch(() => state.error, relayout);
watch(pillVisible, relayout); // pill 现身/消失各算一次(对齐 showRunPill/hideRunPill 里的 layoutRunPill)

let ro: ResizeObserver | null = null;
onMounted(() => {
  relayout();
  // footer 换行/字号变化导致高度变 → pill 重新贴合(对齐 window resize 监听;RO 覆盖内容撑变)
  if (typeof ResizeObserver !== "undefined" && footerEl.value) {
    ro = new ResizeObserver(relayout);
    ro.observe(footerEl.value);
  }
  window.addEventListener("resize", relayout);
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onDocKeydown);
  if (hasBridge) {
    void drivers.probe(); // v2 双驱动:探测 codex 可用性(模块级缓存多工位共享;与 prefillCwd 并发,各自完成各自重渲染 chip)
    // 仅 autoplay 工位预填 cwd + 自动回放(多工位只有主工位自动回放);其余工位 defaultCwd 预填不回放
    if (props.autoplay) void prefillCwd();
    else if (props.defaultCwd) cwd.value = props.defaultCwd;
  }
});
onBeforeUnmount(() => {
  if (ro) ro.disconnect();
  window.removeEventListener("resize", relayout);
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onDocKeydown);
});

// 工位接口(T5/T6 壳依赖):state=store 响应式态(壳读 busy/waiting/usage);dockH=footer 实时高;
// onData=init 数据投递(内调 store.handleData);bindSession=绑定(返回受理与否,busy 守卫)/
// unbindSession=解绑(busy 守卫);stop=esc/中断(仅 streaming 受理);isBusy=sending||streaming;
// hint=壳侧状态行提示通道(T8)
// handoff 草稿随迁：壳路由 → 本工位 Composer（壳只管投递，工位内直转）
function fillDraft(text: string) { composerRef.value?.fillDraft(text); }
defineExpose({ state, dockH, onData, bindSession, unbindSession, stop, isBusy: busy, hint, fillDraft });
</script>

<template>
  <!-- 工位根(T6 起正式盒模型:flex 列,布局/停靠样式由壳 style.css 落;T4 的 display:contents
       过渡技巧已退役);根同时是浮层定位锚(position:relative)与 request-focus 事件锚 -->
  <div class="station" @mousedown="emit('request-focus')">
    <!-- 桥缺失时可见,提示这是设计预览 -->
    <div v-if="!hasBridge" id="bridge-warn">设计预览：未检测到译宝桥（window.yibao），起停/流式回显不可用。</div>

    <!-- 工位头(T4,替换原顶栏「编码对话」标题):左 会话标识 + 引擎徽标 + waiting/busy 状态点
         (dot-waiting 黄点待批 / dot-running 绿点运行中,语义 class,样式 T6 落);右 成本聚合 +
         会话(接续浮层)/ 新对话 + 移出 ✕(壳决定 unbind 或 removeStation) -->
    <header>
      <span
        class="title"
        :title="state.currentSession ? '当前会话 ' + state.currentSession : '新会话（未绑定，发送即开）'"
      >{{ state.currentSession ? "会话 " + state.currentSession.slice(0, 8) : "新会话" }}</span>
      <span class="agent-badge" :title="'当前会话引擎：' + agentLabel(state.curSessAgent)">{{ agentLabel(state.curSessAgent) }}</span>
      <span v-if="state.waiting" class="dot-waiting" title="有权限请求待审批"></span>
      <span v-else-if="state.streaming" class="dot-running" title="会话运行中"></span>
      <span class="spacer"></span>
      <span id="cost" title="本会话累计 token 与成本（done 事件累加；新对话/恢复历史后清零重计）">{{ costText }}</span>
      <button
        id="history"
        type="button"
        title="接续历史会话：上次会话（CC 继续 / Codex 原生续或交接）+ 本项目译宝历史，恢复后继续在同一上下文聊"
        :disabled="state.sending || state.streaming"
        @click.stop="openHistory"
      >接续</button>
      <button id="new-chat" type="button" title="清空当前对话，开新会话（下次发送走 coding.start）" :disabled="newChatDisabled" @click="store.newChat()">新对话</button>
      <button class="station-remove" type="button" title="移出本会话（由壳决定解绑或移除工位）" @click="emit('request-remove')">✕</button>
    </header>

    <!-- 空工位态(验收样式收敛):无会话无消息时的居中淡指引,替代大片灰底 -->
    <div v-if="!state.items.length" class="station-empty">输入消息开始新会话<br>或点右上「接续」恢复历史会话</div>
    <MessageList
      v-show="state.items.length > 0"
      :items="state.items"
      :pad-for-pill="pillVisible"
      :streaming="state.streaming"
      :rewind-pending="rewinding"
      @rewind="onRewind"
      @handoff-cancel="onHandoffCancel"
      @handoff-start="onHandoffStart"
      @status="onComposerStatus"
    />

    <ErrBar v-if="state.error" ref="errbarRef" :text="state.error" @layout="relayout" />
    <RunPill
      :bottom="pillBottom"
      :sending="state.sending"
      :streaming="state.streaming"
      :prefix="state.runPrefix"
      :tok="state.usage.tok"
      :on-stop="store.stop"
    />

    <!-- Composer(T5):输入框 / @ chips / 文件补全 / 快捷键行;段②上下文行经 ctx slot 注入
         (T6 头部控件:cwd chip + 浮层 / mode pill / 引擎 chip + picker),状态行经 status slot -->
    <footer ref="footerEl">
      <Composer
        ref="composerRef"
        :busy="busy"
        :cwd="cwd"
        :on-stop="stop"
        @send="onSend"
        @status="onComposerStatus"
      >
        <template #ctx>
          <CwdChip
            :cwd="cwd"
            :open="openLayer === 'cwd'"
            @toggle="toggleLayer('cwd')"
            @commit="onCwdCommit"
            @cancel="openLayer = ''"
            @browse="onCwdBrowse"
          />
          <ModePill :mode="curMode" @toggle="toggleMode" />
          <AgentChip
            :cur-agent="dstate.curAgent"
            :cur-sess-agent="state.curSessAgent"
            :switch-agent="switchAgent"
            :has-session="!!state.currentSession"
            :codex-available="dstate.codexAvailable"
            :open="openLayer === 'agent'"
            @toggle="onAgentToggle"
            @pick="pickAgent"
            @close="openLayer = ''"
          />
        </template>
        <template #status>
          <StatusLine v-if="statusView.text" :text="statusView.text" :spin="statusView.spin" :err="statusView.err" />
        </template>
      </Composer>
    </footer>

    <!-- 接续浮层(T7):区 1 上次会话(CC 继续 / Codex 原生续·交接)+ 区 2 译宝历史 + 空态;
         留在工位内(T6:position:absolute 相对工位根定位,样式壳 style.css 落) -->
    <HistoryOverlay
      v-if="openLayer === 'history'"
      :loading="historyLoading"
      :last="historyLast"
      :rows="historyRows"
      :list-err="historyListErr"
      :cur-agent="dstate.curAgent"
      :on-attach-cc="onAttachCc"
      :on-attach-codex="onAttachCodex"
      @close="openLayer = ''"
      @resume="onResumeRow"
      @handoff="onCodexCardHandoff"
    />
    <!-- Codex session 选择器(T7):handoff_list 多条时弹出 -->
    <HandoffPicker
      v-if="openLayer === 'handoff'"
      :sessions="handoffSessions"
      @pick="onHandoffPick"
      @close="openLayer = ''"
    />
  </div>
</template>
