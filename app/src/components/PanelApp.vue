<script setup lang="ts">
// 面板窗根组件：标题栏（可拖动 + 面板名 + 关闭）/ 单条快批 / 错误细条 / SchemaPanel 撑满。
// 工作台条（v2 §5）：面板是手、译宝是脑——条上有团子（状态同步）+ 上下文 chip + 输入条，
// 对话走同一大脑；面板内容作为 focus 上报，注入 LLM 上下文（「这个/它」有解）。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import SchemaPanel from "./SchemaPanel.vue";
import WebviewPanel from "./WebviewPanel.vue";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import YbIcon from "./YbIcon.vue";
import {
  onBrainEvent,
  onPendingConfirms,
  openHomeWindow,
  panelAction,
  sendConfirmBatch,
  runInput,
  voiceStart,
  interrupt,
  reportPanelContext,
  setSurface,
  type BrainEvent,
  type PendingConfirm,
  type PanelFocus,
  canRememberSkill,
  rememberLabelForSkill,
} from "../lib/brain";
import { procLabel, procSkip, procResultSuffix } from "../lib/proc";
import { formatContextPrefix, type InputContext } from "../lib/at-mention";
import type { WebviewPayload } from "../lib/webview-source";

// 当前面板：kind="panel" 事件整体替换刷新（webview 非空 → webview 面板，否则 schema 面板）
const current = ref<{
  panel: string;
  title: string;
  schema: any;
  webview: WebviewPayload | null;
  data: Record<string, unknown>;
  input?: "inherit" | "coexist" | "handoff" | "none";
} | null>(null);
const errorText = ref(""); // 面板内顶部错误细条（不进对话气泡）
const pendingConfirms = ref<PendingConfirm[]>([]);
const pending = computed(() => pendingConfirms.value[0] ?? null);
const pendingCanRemember = computed(() => canRememberSkill(pending.value?.skill ?? ""));
const rememberPending = ref(false);
let unlisten: (() => void) | null = null;
let unlistenFocus: (() => void) | null = null;
let unlistenApprovals: (() => void) | null = null;

// ---- 工作台条状态 ----
type AvatarState = "idle" | "listen" | "think" | "work" | "say";
const state = ref<AvatarState>("idle");
const busy = computed(() => state.value !== "idle");
const focus = ref<PanelFocus | null>(null); // 当前面板焦点（同步给大脑）
const chipText = computed(() => {
  const t = focus.value?.item?.title;
  if (typeof t !== "string" || !t) return "";
  return `在看：${t}`;
});
// ---- 输入条 handoff(input-handoff spec):声明制(panel-input-modes spec)——
//      input ∈ {handoff, none} 壳条让位;随迁仅 handoff(none 无 Composer,随迁=丢稿) ----
const handoff = computed(() => {
  const m = current.value?.input;
  return m === "handoff" || m === "none";
});
// ---- 对话浮层（工作台条上方）：输入/回复都留痕成时间线；一轮结束几秒后自动收起，角标可重开 ----
// proc = 过程展示行（工具调用，样式同 hint 淡色小字）
// pstate 驱动图标与颜色，不再把状态符号拼进 text——文案与呈现分离，图标才能统一走 YbIcon
type ThreadMsg = {
  role: "user" | "ai" | "hint" | "proc";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean; // 被打断：行尾显示中止图标
};
const msgs = ref<ThreadMsg[]>([]);
// 过程展示：action.id → 过程行下标，结果回来原地更新
const procIdx = new Map<string, number>();
const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 ai 气泡下标
const layerVisible = ref(false);
const listeningHint = ref(false); // 聆听占位行（识别完替换为用户气泡）
const layerRef = ref<HTMLElement | null>(null);
let collapseTimer: ReturnType<typeof setTimeout> | null = null;

function openLayer() {
  layerVisible.value = true;
  if (collapseTimer !== null) {
    clearTimeout(collapseTimer);
    collapseTimer = null;
  }
}

/** 一轮结束后自动收起：浮层是干活时的环境反馈，不是常驻聊天窗。 */
function scheduleCollapse(ms: number) {
  if (collapseTimer !== null) clearTimeout(collapseTimer);
  collapseTimer = setTimeout(() => {
    layerVisible.value = false;
    collapseTimer = null;
  }, ms);
}

function pushMsg(role: ThreadMsg["role"], text: string) {
  msgs.value.push({ role, text });
  openLayer();
  scrollSoon();
}

function scrollSoon() {
  void nextTick(() => {
    const el = layerRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

/** 面板内容 → 焦点：rows 恰好一条 = 选中条目（详情页）；多条/没有 = 只有面板。 */
function computeFocus(cur: typeof current.value): PanelFocus | null {
  if (!cur?.panel) return null;
  const [plugin, panel] = cur.panel.split(":");
  if (!plugin) return null;
  const rows = (cur.data as any)?.rows;
  const r0 = Array.isArray(rows) && rows.length === 1 ? rows[0] : null;
  return {
    plugin,
    panel: panel ?? "",
    item: r0 ? { id: r0.id, title: r0.title, status: r0.status } : null,
  };
}

/** 面板内容统一入口:赋值 + 重算焦点 + 上报大脑 + 会话分流 surface 随插件切换;
 *  handoff 草稿随迁:进 handoff 面板瞬间取走译宝条草稿移交聚焦工位 Composer(单向)。
 *  取稿必须先于 current 赋值——handoff 后 bench-bar 移除,InputBar 随之卸载。 */
function setCurrent(v: typeof current.value) {
  const entering = current.value?.input !== "handoff" && v?.input === "handoff";
  const draft = entering ? (inputBarRef.value?.takeDraft?.() ?? "") : "";
  current.value = v;
  focus.value = computeFocus(v);
  if (focus.value) setSurface(`panel:${focus.value.plugin}`);
  void reportPanelContext(focus.value).catch(() => {});
  if (draft) void nextTick(() => {
    if (current.value?.input !== "handoff") return; // 同 tick 已切走,不投给错误 iframe
    webviewRef.value?.postToIframe({ type: "handoff-draft", text: draft });
  });
}

function onEvent(e: BrainEvent) {
  // 会话分流：宠物窗的对话事件不归这里；panel/panel_data 例外（面板内容必须接）
  if (e.kind !== "panel" && e.kind !== "panel_data" && e.surface === "pet") return;
  switch (e.kind) {
    case "panel_data":
      // 流式增量：同面板才合并；只动 data（webview/schema/title 不动 → srcdoc 不变 → iframe 不重载）
      if (current.value?.panel === (e.payload?.panel ?? "")) {
        current.value = {
          ...current.value,
          data: { ...(current.value.data ?? {}), ...(e.payload?.data ?? {}) },
        };
      }
      break;
    case "panel":
      setCurrent({
        panel: e.payload?.panel ?? "",
        title: e.payload?.title ?? e.payload?.panel ?? "",
        schema: (e.payload?.schema as any) ?? null,
        webview: (e.payload?.webview as WebviewPayload | null) ?? null,
        data: e.payload?.data ?? {},
        input: e.payload?.input,
      });
      break;
    case "action_proposed":
      state.value = "work";
      // 过程行：技能短标签 + 进行中状态（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, msgs.value.length);
        msgs.value.push({ role: "proc", text: procLabel(e.action), pstate: "run" });
        scrollSoon();
      }
      break;
    case "action_result": {
      const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
      if (idx !== undefined) {
        // 过程行收尾：成功/失败改 pstate（失败带 error 摘要）
        const ok = e.result?.success !== false;
        msgs.value[idx].pstate = ok ? "ok" : "fail";
        msgs.value[idx].text = procLabel(e.action) + procResultSuffix(e.result);
        procIdx.delete(e.action!.id!);
      } else if (e.result && !e.result.success) {
        // 直调失败（如「看 PRD」但还没生成）：结果不是 error 事件，得亮出来，否则点了没反应
        errorText.value = e.result.error || "操作失败";
      }
      break;
    }
    case "final_reply_chunk":
      // 流式增量：拼到当前 streaming 气泡（首片时新建）
      if (streamingIdx.value === null) {
        msgs.value.push({ role: "ai", text: e.text ?? "" });
        streamingIdx.value = msgs.value.length - 1;
        openLayer();
      } else {
        msgs.value[streamingIdx.value].text += e.text ?? "";
      }
      scrollSoon();
      break;
    case "final_reply":
      // 以完整文本为准收尾（兜底 chunk 丢失）
      if (streamingIdx.value !== null) {
        msgs.value[streamingIdx.value].text = e.text ?? "";
        streamingIdx.value = null;
      } else {
        pushMsg("ai", e.text ?? "");
      }
      scrollSoon();
      if (state.value !== "say") {
        state.value = "idle";
        scheduleCollapse(6000);
      }
      break;
    case "interrupted":
      listeningHint.value = false;
      if (streamingIdx.value !== null) {
        msgs.value[streamingIdx.value].halted = true;
        streamingIdx.value = null;
      }
      state.value = "idle";
      scheduleCollapse(3000);
      break;
    case "listening":
      listeningHint.value = true; // 占位行：识别中先给个看得着的反馈
      openLayer();
      state.value = "listen";
      break;
    case "listening_done":
      listeningHint.value = false;
      if (e.text) {
        pushMsg("user", e.text); // 语音转文字落气泡：识别错了能看出来
        state.value = "think";
      } else {
        pushMsg("hint", "没听清，再试一次？");
        state.value = "idle";
        scheduleCollapse(4000);
      }
      break;
    case "speaking":
      state.value = "say";
      break;
    case "notice":
      // 轻提示（插件展开等，§12-2 要知情）：hint 行展示，不改变状态
      pushMsg("hint", e.text ?? "");
      openLayer();
      break;
    case "speaking_done":
      state.value = "idle";
      scheduleCollapse(4000);
      break;
    case "error":
      errorText.value = e.text ?? "出错了";
      state.value = "idle";
      break;
  }
}

async function decide(approved: boolean, remember = false) {
  if (!pending.value) return;
  const { id } = pending.value;
  try {
    await sendConfirmBatch([{ id, approved, remember: pendingCanRemember.value && remember }]);
    rememberPending.value = false;
  } catch (err) {
    errorText.value = "确认失败：" + String(err);
  }
}

function openInbox() {
  void openHomeWindow().catch((err) => {
    errorText.value = "打开收件箱失败：" + String(err);
  });
}

async function onAction(a: { method: string; params: Record<string, unknown> }) {
  errorText.value = "";
  try {
    await panelAction(a.method, a.params);
  } catch (err) {
    errorText.value = "面板操作失败：" + String(err);
  }
}

// 工作台条交互：提交走同一 runInput（focus 已在大脑上下文里）；mic/长按团子 = 语音
function submit(text: string, contexts: InputContext[] = []) {
  errorText.value = "";
  const t = formatContextPrefix(contexts) + text;
  pushMsg("user", t); // 输入立刻有落点（浮层时间线）
  void runInput(t).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
}

/** 逃生口「问团子」(handoff 期浮层底部 mini 输入):直走译宝大脑(原 runInput 路径,
 *  面板 focus 已在大脑上下文里),不打断编码会话。复活自 30dd8c9,takeover 路由已退役。 */
const askText = ref("");
// 浮层收起即清空 mini 输入,下次打开不留残稿
watch(layerVisible, (v) => {
  if (!v) askText.value = "";
});
// handoff 结束(切走)同样清残稿——浮层可能未收起,下次进 coding 不复活旧稿
watch(handoff, (v) => {
  if (!v) askText.value = "";
});

function submitBrain(text: string) {
  const t = text.trim();
  if (!t) return;
  errorText.value = "";
  askText.value = "";
  pushMsg("user", t); // 输入立刻有落点(浮层时间线)
  void runInput(t).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
}

// mini 输入 IME 守卫(同 InputBar:WebKit 下 compositionend 先于确认 Enter 的 keydown,
// 该 keydown 的 isComposing 已为 false——记 compositionend 时间戳,50ms 窗口内的 Enter 一并拦截)
let askCompEnd = 0;
function onAskEnter(e: KeyboardEvent) {
  if (e.isComposing || Date.now() - askCompEnd < 50) return;
  submitBrain(askText.value);
}

function onMic() {
  void voiceStart().catch((err) => {
    errorText.value = "语音失败：" + String(err);
  });
}

function onInterrupt() {
  if (!busy.value) return;
  void interrupt().catch(() => {});
}

/** 聆听中点团子 = 取消录音；否则聚焦输入框。 */
function onPetTap() {
  if (state.value === "listen") onInterrupt();
  else focusInput();
}

function focusInput() {
  // 经 ref 调 InputBar expose 的 focus()——querySelector("input") 会误中隐藏 file-input（主输入是 textarea）
  inputBarRef.value?.focus();
}

function close() {
  void reportPanelContext(null).catch(() => {});
  void invoke("close_panel_window");
}

async function pullCache() {
  try {
    const cached = await invoke<{
      panel: string;
      title?: string;
      schema: any;
      webview: WebviewPayload | null;
      data: Record<string, unknown>;
      input?: "inherit" | "coexist" | "handoff" | "none";
    } | null>("get_current_panel");
    if (cached && current.value === null) {
      setCurrent({ ...cached, title: cached.title ?? cached.panel });
    }
  } catch (err) {
    // 命令缺失（旧壳进程）等问题要看得见，不能静默停在占位页
    errorText.value = "面板数据拉取失败：" + String(err);
  }
}

// webview 面板 html（空串 → 走 schema 面板/占位）
const webviewHtml = computed(() => current.value?.webview?.html ?? "");
// module 面板(R4):url/v 直传 WebviewPanel;空串 → 与 html 一起判空走 schema/占位
const webviewUrl = computed(() => current.value?.webview?.url ?? "");
const webviewV = computed(() => current.value?.webview?.v ?? 0);

const inputBarRef = ref();
const webviewRef = ref<InstanceType<typeof WebviewPanel> | null>(null);

/** iframe 面板事件：insert-draft 回输 InputBar 草稿。 */
function onPanelEvent(name: string, payload: any) {
  if (name === "insert-draft") {
    inputBarRef.value?.insertText(payload?.text ?? "");
  }
}

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
  unlistenApprovals = onPendingConfirms((items) => {
    pendingConfirms.value = items.filter((item) => item.surface?.startsWith("panel"));
    if (pendingConfirms.value.length === 0) {
      rememberPending.value = false;
    } else {
      state.value = "idle";
    }
  });
  // 首开竞态：panel 事件先于本窗口订阅发出，从 Rust 缓存补拉最近一次面板
  await pullCache();
  // 兜底：窗口再聚焦时若仍是占位页，重拉一次（覆盖旧壳残留窗口等边角）
  unlistenFocus = await getCurrentWindow().onFocusChanged(({ payload: focused }) => {
    if (focused && current.value === null) void pullCache();
  });
});
onUnmounted(() => {
  unlisten?.();
  unlistenFocus?.();
  unlistenApprovals?.();
  if (collapseTimer !== null) clearTimeout(collapseTimer);
  // 窗口销毁（重载等）也清焦点，避免大脑留着旧上下文
  void reportPanelContext(null).catch(() => {});
});
</script>

<template>
  <div class="panel-shell">
    <div class="titlebar" data-tauri-drag-region>
      <span class="name">
        <!-- handoff 逃生口:团子搬到壳标题栏(点击开浮层问译宝,mini 输入见 Task 2) -->
        <Avatar v-if="handoff" class="pet titlebar-pet" :state="state" :size="20" @click="openLayer" />
        {{ current?.title ?? "面板" }}
      </span>
      <button class="x" title="关闭" @click="close">×</button>
    </div>

    <div v-if="pendingConfirms.length > 1" class="confirm-bar batch-confirm">
      <span class="c-text"><strong>{{ pendingConfirms.length }} 项待批准，去大窗批量处理</strong></span>
      <span class="c-btns">
        <button class="ok" @click="openInbox">打开收件箱</button>
      </span>
    </div>
    <div v-else-if="pending" class="confirm-bar">
      <span class="c-text"><YbIcon class="c-ic" name="alert" :size="14" />{{ pending.label || pending.skill }}{{ pending.desc ? " · " + pending.desc : "" }}</span>
      <label v-if="pendingCanRemember" class="c-remember">
        <input v-model="rememberPending" type="checkbox" />
        {{ rememberLabelForSkill(pending.skill) }}
      </label>
      <span class="c-btns">
        <button class="deny" @click="decide(false)">拒绝</button>
        <button class="ok" @click="decide(true, rememberPending)">允许</button>
      </span>
    </div>

    <div v-if="errorText" class="error-bar"><YbIcon name="alert" :size="14" />{{ errorText }}</div>

    <div class="content">
      <WebviewPanel
        v-if="current && (webviewHtml || webviewUrl)"
        :key="current.panel"
        ref="webviewRef"
        :panel="current.panel"
        :html="webviewHtml"
        :url="webviewUrl"
        :v="webviewV"
        :data="current.data"
        @panel-event="onPanelEvent"
      />
      <SchemaPanel
        v-else-if="current"
        :panel="current.panel"
        :schema="current.schema"
        :data="current.data"
        @action="onAction"
      />
      <div v-else class="placeholder">
        <div class="ph-icon"><YbIcon name="dumpling" :size="26" :stroke="1.5" /></div>
        <div class="ph-title">这里还空空的</div>
        <div class="ph-hint">去跟译宝说一句试试，让它帮你打开想看的面板</div>
      </div>
    </div>

    <!-- 工作台条：对话浮层（输入/回复时间线）+ 团子 + 上下文 chip + 输入条 -->
    <div class="bench">
      <transition name="pop">
        <div v-if="layerVisible && (msgs.length || listeningHint || handoff)" ref="layerRef" class="thread">
          <button class="thread-x" title="收起" aria-label="收起对话" @click="layerVisible = false">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
              stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
          <div
            v-for="(m, i) in msgs"
            :key="i"
            class="t-row"
            :class="[m.role, m.pstate && `is-${m.pstate}`]"
            :title="m.role === 'user' ? m.text : undefined"
          >
            <YbIcon
              v-if="m.pstate"
              class="t-ic"
              :name="m.pstate === 'run' ? 'spinner' : m.pstate === 'ok' ? 'check' : 'x'"
              :spin="m.pstate === 'run'"
              :size="12"
            />
            <span>{{ m.text }}</span>
            <YbIcon v-if="m.halted" class="t-ic" name="stop" :size="12" title="已中止" />
          </div>
          <div v-if="listeningHint" class="t-row hint">
            <YbIcon class="t-ic" name="mic" :size="12" />
            <span>聆听中…（点团子取消）</span>
          </div>
          <!-- 逃生口 mini 输入行(仅 handoff 渲染):单行 input + 发送钮,直问译宝大脑 -->
          <div v-if="handoff" class="ask-row">
            <input
              v-model="askText"
              class="ask-input"
              type="text"
              placeholder="问团子…（不打断编码会话）"
              @keydown.enter.exact.prevent="onAskEnter"
              @compositionend="askCompEnd = Date.now()"
            />
            <button type="button" class="ask-send" :disabled="!askText.trim()" @click="submitBrain(askText)">
              发送
            </button>
          </div>
        </div>
      </transition>
      <div v-if="!handoff" class="bench-bar">
        <Avatar class="pet" :state="state" :size="30" @click="onPetTap" @longpress="onMic" />
        <button v-if="!layerVisible && msgs.length" class="thread-open" title="查看对话" @click="openLayer">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <span v-if="chipText" class="chip" :title="chipText">{{ chipText }}</span>
        <InputBar
          class="bench-input"
          ref="inputBarRef"
          :busy="busy"
          :listening="state === 'listen'"
          @submit="submit"
          @mic="onMic"
          @interrupt="onInterrupt"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-shell {
  height: 100vh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--yb-font);
  color: var(--yb-text);
  background: var(--yb-shell-bg);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-xl);
  box-shadow: var(--yb-shadow);
}
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--yb-space-3) var(--yb-space-4);
  user-select: none;
}
.name {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.titlebar-pet {
  flex-shrink: 0;
  cursor: pointer;
}
.x {
  border: none;
  background: transparent;
  font-size: 18px; /* × 字形尺寸，非文本字号 */
  line-height: 1;
  cursor: pointer;
  color: var(--yb-text-dim);
  padding: 2px 8px;
  border-radius: var(--yb-radius-sm);
}
.x:hover {
  background: var(--yb-btn-neutral);
}
.confirm-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  margin: 0 var(--yb-space-4) var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-danger-soft);
  font-size: var(--yb-fs-md);
}
.c-text {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  line-height: var(--yb-lh-ui);
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 待批准提示图标：意图琥珀，与收件箱待批准区同语言 */
.c-ic {
  color: var(--yb-intent-pending-ink);
  margin-right: var(--yb-space-1);
}
.c-remember {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  white-space: nowrap;
}
.c-remember input {
  margin: 0;
  accent-color: var(--yb-accent);
}
.c-btns {
  display: flex;
  gap: var(--yb-space-2);
  flex-shrink: 0;
}
.c-btns button {
  padding: 5px 14px;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  font-size: var(--yb-fs-md);
  font-weight: 500;
}
.ok {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.deny {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
.error-bar {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  margin: 0 var(--yb-space-4) var(--yb-space-2);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
.content {
  flex: 1;
  min-height: 0;
  margin: 0 var(--yb-space-2) var(--yb-space-2);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface);
}
.placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 0 24px;
  text-align: center;
  background: var(--yb-surface-2);
  border-radius: inherit;
}
.ph-icon {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  display: grid;
  place-items: center;
  margin-bottom: 4px;
}
.ph-title {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
}
.ph-hint {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}

/* ---- 工作台条 ---- */
/* 水平 margin 锚在 .bench:浮层 .thread 以它为定位锚(left/right:0),边缘与 bench-bar 对齐;
   空 .bench 高 0 且无垂直 margin,handoff 让位后不留缝隙 */
.bench {
  position: relative;
  margin: 0 var(--yb-space-2);
}
/* thread：取消 box-shadow / 独立圆角 / 独立边框。
   原版像独立 popover（圆角+边框+阴影+实色背景），与下方 .bar (InputBar)
   视觉割裂，弹出显得"飘着"且生硬。
   新版：用半透玻璃与 .bar 同源，顶端一根细 hairline 给出"起点"，不再是 popover；
   bottom 紧贴 .bar 顶端 4px，让两者像「输入条向上延伸出的时间线」。
   hover 才出关闭按钮，不常驻占位。 */
.thread {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 4px);
  max-height: 240px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* top padding 24px = thread-x (top:4 + 20) 留位；首行不被关闭按钮遮挡 */
  padding: 24px 10px 6px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-surface-border);
  box-shadow: none;
  scrollbar-width: thin;
}
/* 顶端 hairline：用伪元素画一根从透明到描边的渐变，给 thread 一个"起点"，
   同时不让 thread 显得从内容区凭空冒出 */
.thread::before {
  content: "";
  position: absolute;
  top: 0;
  left: var(--yb-space-3);
  right: var(--yb-space-3);
  height: 1px;
  background: linear-gradient(
    to right,
    transparent,
    var(--yb-card-row-line) 20%,
    var(--yb-card-row-line) 80%,
    transparent
  );
  pointer-events: none;
}
.thread-x {
  position: absolute;
  top: 4px;
  right: 6px;
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--yb-text-faint);
  cursor: pointer;
  display: grid;
  place-items: center;
  border-radius: 50%;
  opacity: 0;
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out),
    background var(--yb-dur-fast) var(--yb-ease-out);
  flex-shrink: 0;
  z-index: 1;
}
.thread-x svg {
  width: 10px;
  height: 10px;
}
.thread:hover .thread-x,
.thread-x:focus-visible {
  opacity: 1;
}
.thread-x:hover {
  background: var(--yb-btn-neutral);
  color: var(--yb-text);
}
.t-row {
  padding: 4px 10px;
  border-radius: var(--yb-radius-md);
  font-size: var(--yb-fs-md);
  line-height: var(--yb-lh-base);
  white-space: pre-wrap;
  word-break: break-word;
}
/* 用户输入：小气泡靠右、最多两行（全文在 title），回复才是主角 */
.t-row.user {
  align-self: flex-end;
  max-width: 82%;
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
/* 回复：字幕感——大一号、宽松行距、无边框 */
.t-row.ai {
  align-self: stretch;
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
}
/* 提示行与过程行：图标 + 文字横排（不能给 .t-row 全局设 flex——user 行依赖 -webkit-box 截断） */
.t-row.hint {
  align-self: center;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
/* 过程行：同 hint 淡色小字调性，状态由图标色承载 */
.t-row.proc {
  align-self: center;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  padding-top: 0;
  padding-bottom: 0;
}
.t-ic {
  flex-shrink: 0;
}
.t-row.is-run .t-ic {
  color: var(--yb-accent);
}
.t-row.is-ok .t-ic {
  color: var(--yb-intent-ok);
}
.t-row.is-fail {
  color: var(--yb-danger);
}
/* 逃生口 mini 输入行:thread 底部单行 input + accent 发送钮,与 thread 同玻璃调性 */
.ask-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding-top: 2px;
}
.ask-input {
  flex: 1;
  min-width: 0;
  padding: 5px 10px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  outline: none;
}
.ask-input:focus {
  border-color: var(--yb-accent);
}
.ask-send {
  flex-shrink: 0;
  border: none;
  border-radius: var(--yb-radius-md);
  padding: 5px 14px;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: var(--yb-fs-md);
  cursor: pointer;
}
.ask-send:disabled {
  opacity: 0.4;
  cursor: default;
}
.thread-open {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border: none;
  border-radius: 50%;
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: filter var(--yb-dur-fast), color var(--yb-dur-fast);
}
.thread-open:hover {
  color: var(--yb-text);
  filter: brightness(0.96);
}
.thread-open svg {
  width: 14px;
  height: 14px;
}
.pop-enter-active,
.pop-leave-active {
  transition: opacity var(--yb-dur) var(--yb-ease-out), transform var(--yb-dur) var(--yb-ease-out);
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
.bench-bar {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  margin-bottom: var(--yb-space-2);
}
.pet {
  flex-shrink: 0;
  cursor: pointer;
}
.chip {
  flex-shrink: 1;
  min-width: 0;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 3px var(--yb-space-3);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  font-size: var(--yb-fs-md);
  user-select: none;
}
.bench-input {
  flex: 1;
  min-width: 0;
}
</style>
