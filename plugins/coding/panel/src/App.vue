<script setup lang="ts">
// coding:studio(R4 阶段二 T8):takeover 接线收尾——onInit takeover 标志(退出清 store 队列)、
// onHostMessage(takeover-input 排队/直发 + takeover-stop)、takeover-state 经真桥上报(见 store report hook)、
// esc 优先级链(agent→history→handoff→stop,T6 已接,T8 对齐 :2825-2831 复核)、无桥预览静态样例。
// T9 评审修复:takeover-input 补回 onSend 同组校验(cwd/文本)与跨引擎交接守卫(直发走 runHandoff,
// 队列泄放走 store 侧 queueContext.switchAgent 快照)——原 chat.html 由 send() 内联分支覆盖这两条
// 路径,重写后 store.takeoverInput 直驱绕过。
// T7:交接双路径 + rewind + 接续浮层 + autoReplay 抽取。
// T6 已接:cwd chip + 浮层 / 引擎 chip + picker / mode pill / 状态行 + drivers store。
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
import { hasBridge, invoke, onHostMessage, onInit } from "./lib/bridge";
import type { HandoffSessionItem, LastSessions, PanelData, SessionRow } from "./lib/types";
import { doneStatusText, emsg, fmtCost, fmtTok, normCwd } from "./lib/format";
import { pickReplayCandidate, replayStep, shouldYieldReplay } from "./lib/replay";
import { createSessionStore, type HandoffSendResult, type RenderItem } from "./stores/session";
import { agentLabel, createDriversStore, normAgent } from "./stores/drivers";
import MessageList from "./components/MessageList.vue";
import ErrBar from "./components/ErrBar.vue";
import RunPill from "./components/RunPill.vue";
import Composer from "./components/Composer.vue";
import CwdChip from "./components/CwdChip.vue";
import ModePill from "./components/ModePill.vue";
import AgentChip from "./components/AgentChip.vue";
import StatusLine from "./components/StatusLine.vue";
import HistoryOverlay from "./components/HistoryOverlay.vue";
import HandoffPicker from "./components/HandoffPicker.vue";

const takeover = ref(false);

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
// 跨引擎待定随会话切换/清空一并作废(对齐原 newChat/resumeSession 里的 switchAgent=null)
watch(() => state.currentSession, () => { switchAgent.value = null; });

// takeover 队列泄放的发送上下文:随 cwd/mode/引擎选择实时快照(原读全局变量,天然实时);
// switchAgent 一并入快照(T9)——泄放路径的跨引擎交接守卫读它(store 侧判据)
watch([cwd, curMode, () => dstate.curAgent, switchAgent], ([c, m, a, sw]) => {
  store.setQueueContext({ cwd: c, mode: m, agent: a, switchAgent: sw });
}, { immediate: true });

// init 回调第二参是完整载荷:takeover 标志随每条 panel_data 重提(旧桥只传 data → undefined 视为 false;
// ref 赋同值不触发 watch,天然幂等——对齐 setTakeover :802-805 态不变不动 DOM)
onInit((data, msg) => {
  takeover.value = !!(msg && msg.takeover);
  store.handleData(data as PanelData);
});
// body.takeover 驱动 CSS 隐藏输入区;退出接管清空 store 排队输入(对齐 :806-808——
// 父侧输入条已交还译宝大脑,滞留队列会在非接管态被意外发出)
watch(takeover, (on) => {
  document.body.classList.toggle("takeover", on);
  if (!on) { /* TODO(T4): 壳重写时移除(takeover 退役,清队列调用随之作废) */ }
}, { immediate: true });

// 宿主接管消息(T8;对齐 :829-847 onHostMessage):按 msg.type 判别,非接管词汇静默忽略。
// takeover-input → onTakeoverInput(校验/排队/交接守卫,见 Composer 接线段);
// takeover-stop 等价点 iframe 内 stop 钮(store.takeoverStop 自判 busy)。
onHostMessage((msg) => {
  if (!msg || typeof msg.type !== "string") return;
  if (msg.type === "takeover-input") { /* TODO(T4): 壳重写时移除(takeover-input 转发层已退役) */ }
  else if (msg.type === "takeover-stop") { /* TODO(T4): 壳重写时移除 */ }
});

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
// 点外部关浮层(对齐原 document click 关 cwd 浮层;agent picker 有 backdrop,双路关闭幂等)
function onDocClick() { if (openLayer.value) openLayer.value = ""; }
// esc 优先级:agent-picker → history → handoff → stop(cwd 浮层 Esc 由其 input stopPropagation 消费)
function onDocKeydown(e: KeyboardEvent) {
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
    historyRows.value = listR.rows.filter((r) => normCwd(r.cwd) === normCwd(c)); // 只显示本项目;跨项目总览归会话墙
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
const composerRef = ref<{ clear: () => void; focus: () => void } | null>(null);
const busy = computed(() => state.sending || state.streaming);

// 跨引擎交接(:1884-1887;T9 抽出,onSend 与 takeover-input 直发共用):老会话上 chip 选了
// 另一引擎 → handoffSend 摘要移植开新会话(不走 coding.send,引擎间无法原生互续);
// 编排在 store,选择态经回调同步。返回 null = 无待定切换(调用方走普通 send)
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
  if (state.sending || state.streaming) return; // 重入守卫(对齐 send();store.send 内同有)
  if (!hasBridge) { onComposerStatus("设计预览模式：未连译宝桥，无法发送", true); return; }
  const prompt = text.trim();
  // cwd 空拦阻并开 cwd 浮层;校验顺序对齐原 send():先 cwd 后 prompt
  if (!cwd.value.trim()) { onComposerStatus("请先选择项目目录", true); openLayer.value = "cwd"; return; }
  if (!prompt) { onComposerStatus("请输入任务描述", true); composerRef.value?.focus(); return; }
  const hr = await runHandoff(prompt, refs);
  if (hr !== null) { if (hr === "sent") composerRef.value?.clear(); return; } // 成功才消费 prompt+chips;stale/failed 保留
  try {
    await store.send(cwd.value.trim(), prompt, curMode.value, dstate.curAgent, { refs });
    composerRef.value?.clear(); // 成功才消费 prompt+chips;失败保留可重试(对齐原清空时机)
  } catch { /* 失败文本已由 state.error 进状态行/errbar,prompt+refs 留存 */ }
}

function onComposerStop(): Promise<boolean> {
  // 对齐原 #stop:仅 streaming 期受理(sending 窗会话 id 未回填是死点击)——
  // false = 拒理,Composer 据此立即解锁中断钮(T5 评审修复:锁不再留存过 sending 窗)
  return state.streaming ? store.stop() : Promise.resolve(false);
}

// ---- RunPill 布局(C3):bottom = footer 高 + 10 + (errbar 可见时)errbar 高,现算现贴 ----
const footerEl = ref<HTMLElement | null>(null);
const errbarRef = ref<{ root: HTMLElement | null } | null>(null);
const pillBottom = ref(110);
const pillVisible = computed(() => state.sending || state.streaming);

function relayout() {
  void nextTick(() => {
    let b = (footerEl.value ? footerEl.value.offsetHeight : 0) + 10;
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
    void drivers.probe(); // v2 双驱动:探测 codex 可用性(与 prefillCwd 并发,各自完成各自重渲染 chip)
    void prefillCwd();
  }
});
onBeforeUnmount(() => {
  if (ro) ro.disconnect();
  window.removeEventListener("resize", relayout);
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onDocKeydown);
});
</script>

<template>
  <!-- 桥缺失时可见,提示这是设计预览 -->
  <div v-if="!hasBridge" id="bridge-warn">设计预览：未检测到译宝桥（window.yibao），起停/流式回显不可用。</div>

  <!-- 顶栏:标题 + 成本聚合 + 会话(接续浮层)/ 新对话 -->
  <header>
    <span class="title">编码对话</span>
    <span class="spacer"></span>
    <span id="cost" title="本会话累计 token 与成本（done 事件累加；新对话/恢复历史后清零重计）">{{ costText }}</span>
    <button
      id="history"
      type="button"
      title="会话：上次会话（CC 继续 / Codex 原生续或交接）+ 本项目译宝历史，恢复后继续在同一上下文聊"
      :disabled="state.sending || state.streaming"
      @click.stop="openHistory"
    >会话</button>
    <button id="new-chat" type="button" title="清空当前对话，开新会话（下次发送走 coding.start）" :disabled="newChatDisabled" @click="store.newChat()">新对话</button>
  </header>

  <MessageList
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
       (T6 头部控件:cwd chip + 浮层 / mode pill / 引擎 chip + picker),状态行经 status slot。
       takeover 态段①与发送钮经 body.takeover 隐藏,输入由宿主 InputBar 直驱 store.takeoverInput -->
  <footer ref="footerEl">
    <Composer
      ref="composerRef"
      :busy="busy"
      :cwd="cwd"
      :on-stop="onComposerStop"
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
        <StatusLine :text="statusView.text" :spin="statusView.spin" :err="statusView.err" />
      </template>
    </Composer>
  </footer>

  <!-- 接续浮层(T7):区 1 上次会话(CC 继续 / Codex 原生续·交接)+ 区 2 译宝历史 + 空态 -->
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
</template>
