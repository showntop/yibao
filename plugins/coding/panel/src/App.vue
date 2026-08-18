<script setup lang="ts">
// coding:studio(R4 阶段二 T6):头部控件接入——cwd chip + 浮层 / 引擎 chip + picker /
// mode pill / 状态行 + drivers store(coding.drivers 探测 + 新会话引擎选择)。
// 行为逐条对齐 chat.html:
//   commitCwd(:2890-2899):空/同值忽略、running 拒(状态行提示)、setCwd + newChat +
//     coding.list → refreshCwdState(默认引擎记忆 = 该 cwd 时间倒序首个命中行的 agent)+ autoReplay
//   prefillCwd(:2837-2847):init 时 coding.list 首行预填 cwd(与 probeDrivers 并发)
//   autoReplay(:2869-2885):回放该 cwd 最近一条非空会话;活体/codex 不可用过滤;attach 抢占让位
//   pickAgent(:1817-1827):有会话同引擎清待定/异引擎置 switchAgent + 状态行提示;无会话设 curAgent
//   互收:单一 openLayer 兑现(开任一浮层即关其他;T7 的 history/handoff 直接加枚举值);
//   esc 优先级(:2825-2831):agent-picker → history → handoff → stop(cwd 浮层 Esc 由其 input 消费)
// 跨引擎交接发送(handoffSend)本体在 T7——本任务的 switchAgent 选择态 + 状态行提示已生效。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { emitPanelEvent, hasBridge, invoke, onInit } from "./lib/bridge";
import type { PanelData, SessionRow } from "./lib/types";
import { doneStatusText, fmtCost, fmtTok } from "./lib/format";
import { createSessionStore } from "./stores/session";
import { agentLabel, createDriversStore, normAgent } from "./stores/drivers";
import MessageList from "./components/MessageList.vue";
import ErrBar from "./components/ErrBar.vue";
import RunPill from "./components/RunPill.vue";
import Composer from "./components/Composer.vue";
import CwdChip from "./components/CwdChip.vue";
import ModePill from "./components/ModePill.vue";
import AgentChip from "./components/AgentChip.vue";
import StatusLine from "./components/StatusLine.vue";

const takeover = ref(false);

const store = createSessionStore({
  invoke: (m, p) => invoke(m, p),
  // takeover-state 上报:仅 takeover 态真正发(store 语义;对齐 chat.html reportState)
  report: (st, hasSession) => {
    if (takeover.value) emitPanelEvent("takeover-state", { state: st, session: hasSession });
  },
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (t) => clearTimeout(t),
  userEchoFallbackMs: 1500,
});
const state = store.state;

const drivers = createDriversStore({ invoke: (m, p) => invoke(m, p) });
const dstate = drivers.state;

// ---- 头部控件状态(T6):cwd 真值 / 权限模式 / 跨引擎切换待定(curAgent 在 drivers store) ----
const cwd = ref("");
const curMode = ref("acceptEdits");
const switchAgent = ref<string | null>(null); // 老会话跨引擎切换待定(发送时交接摘要移植,T7)
// 跨引擎待定随会话切换/清空一并作废(对齐原 newChat/resumeSession 里的 switchAgent=null)
watch(() => state.currentSession, () => { switchAgent.value = null; });

// takeover 队列泄放的发送上下文:随 cwd/mode/引擎选择实时快照(原读全局变量,天然实时)
watch([cwd, curMode, () => dstate.curAgent], ([c, m, a]) => {
  store.setQueueContext({ cwd: c, mode: m, agent: a });
}, { immediate: true });

// init 回调第二参是完整载荷:takeover 标志随每条 panel_data 重提(旧桥只传 data → undefined 视为 false)
onInit((data, msg) => {
  takeover.value = !!(msg && msg.takeover);
  store.handleData(data as PanelData);
});
// body.takeover 驱动 CSS 隐藏输入区(T8 接 takeover-input/-stop 宿主消息)
watch(takeover, (on) => { document.body.classList.toggle("takeover", on); }, { immediate: true });

// ---- 浮层互收:任一 picker/浮层开时关其他;T7 history/handoff 加枚举值即接入 ----
type Layer = "" | "cwd" | "agent" | "history" | "handoff";
const openLayer = ref<Layer>("");
function toggleLayer(l: Exclude<Layer, "">) { openLayer.value = openLayer.value === l ? "" : l; }
// 点外部关浮层(对齐原 document click 关 cwd 浮层;agent picker 有 backdrop,双路关闭幂等)
function onDocClick() { if (openLayer.value) openLayer.value = ""; }
// esc 优先级:agent-picker → history → handoff → stop(cwd 浮层 Esc 由其 input stopPropagation 消费)
function onDocKeydown(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  if (openLayer.value === "agent") { openLayer.value = ""; return; }
  if (openLayer.value === "history") { openLayer.value = ""; return; } // T7 接入
  if (openLayer.value === "handoff") { openLayer.value = ""; return; } // T7 接入
  if (state.currentSession && (state.streaming || state.sending)) void store.stop();
}

// ---- 顶栏:会话成本聚合(C4,对齐 renderCost):tok 与成本都未见 → 空;codex 无 cost → 只 token 段 ----
const costText = computed(() => {
  const u = state.usage;
  if (!u.tok && !u.hasCost) return "";
  return fmtTok(u.tok) + " tok" + (u.hasCost ? " · " + fmtCost(u.cost) : "");
});
const newChatDisabled = computed(() => state.sending || state.streaming || !state.currentSession);

// ---- footer 状态行:提交中/运行中(spinner)/完成行(doneStatusText,isFinite 守御)/错误 ----
const status = computed(() => {
  if (state.sending) return { text: "提交中…", spin: true, err: false };
  if (state.streaming) return { text: (state.runPrefix || "会话") + " 运行中…", spin: true, err: false };
  if (state.error) return { text: state.error, spin: false, err: true };
  if (state.ended === "done") return { text: doneStatusText(state.lastUsage), spin: false, err: false };
  if (state.ended === "stopped") return { text: "已中断", spin: false, err: false };
  return { text: "", spin: false, err: false };
});

// Composer/头部控件瞬时提示与 store 状态行共一位:store 状态每次变化覆盖提示
// (对齐 chat.html setStatus 覆盖语义——后写赢)
const tip = ref<{ text: string; spin: boolean; err: boolean } | null>(null);
watch(status, () => { tip.value = null; });
const statusView = computed(() => tip.value ?? status.value);
function onComposerStatus(text: string, err: boolean) { tip.value = { text, spin: false, err }; }

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

// 自动回放(对齐 :2869-2885):把当前 cwd 最近一条非空会话回放进对话框。候选过滤:
// 活体会话(running/waiting 发送会被拒)、codex 已探测不可用;空会话经 skipIfEmpty 顺延。
// 让位:attach 接管/手动接续抢占(currentSession 出现或 resume 在飞)即停——原查 resuming
// 全局,这里经 store.isResuming();不重入 resumeSession(pendingResume 排队会反抢 attach)。
async function autoReplay(rows: SessionRow[], c: string) {
  if (state.currentSession || store.isResuming()) return;
  const cands = rows.filter((row) =>
    row && row.id && row.cwd === c &&
    row.live !== "running" && row.live !== "waiting" &&
    !(normAgent(row.agent) === "codex" && dstate.codexAvailable === false));
  for (const cand of cands) {
    if (state.currentSession || store.isResuming()) return;
    const n = await store.resumeSession(cand.id, cand.agent, { skipIfEmpty: true });
    if (n !== 0) return; // >0 已回放;-1 理论不可达(上方已守),保底停
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
// 另一引擎置 switchAgent(chip 立即显示选中项;发送时自动交接摘要,以新引擎继续——T7 接 handoffSend)
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

// ---- Composer 接线(T5;stop 受理信号修复见 T6)----
const composerRef = ref<{ clear: () => void; focus: () => void } | null>(null);
const busy = computed(() => state.sending || state.streaming);

async function onSend(text: string, refs: string[]) {
  if (state.sending || state.streaming) return; // 重入守卫(对齐 send();store.send 内同有)
  if (!hasBridge) { onComposerStatus("设计预览模式：未连译宝桥，无法发送", true); return; }
  const prompt = text.trim();
  // cwd 空拦阻并开 cwd 浮层;校验顺序对齐原 send():先 cwd 后 prompt
  if (!cwd.value.trim()) { onComposerStatus("请先选择项目目录", true); openLayer.value = "cwd"; return; }
  if (!prompt) { onComposerStatus("请输入任务描述", true); composerRef.value?.focus(); return; }
  // 跨引擎交接分支(currentSession && switchAgent !== curSessAgent → handoffSend)属 T7
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

  <!-- 顶栏:标题 + 成本聚合 + 新对话(「接续」popover 属 T7) -->
  <header>
    <span class="title">编码对话</span>
    <span class="spacer"></span>
    <span id="cost" title="本会话累计 token 与成本（done 事件累加；新对话/恢复历史后清零重计）">{{ costText }}</span>
    <button id="new-chat" type="button" title="清空当前对话，开新会话（下次发送走 coding.start）" :disabled="newChatDisabled" @click="store.newChat()">新对话</button>
  </header>

  <MessageList :items="state.items" :pad-for-pill="pillVisible" />

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
</template>
