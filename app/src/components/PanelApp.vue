<script setup lang="ts">
// 面板窗根组件：标题栏（可拖动 + 面板名 + 关闭）/ 内嵌确认条 / 错误细条 / SchemaPanel 撑满。
// 工作台条（v2 §5）：面板是手、译宝是脑——条上有团子（状态同步）+ 上下文 chip + 输入条，
// 对话走同一大脑；面板内容作为 focus 上报，注入 LLM 上下文（「这个/它」有解）。
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import SchemaPanel from "./SchemaPanel.vue";
import WebviewPanel from "./WebviewPanel.vue";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import {
  onBrainEvent,
  panelAction,
  sendConfirm,
  runInput,
  voiceStart,
  interrupt,
  reportPanelContext,
  setSurface,
  type BrainEvent,
  type PanelFocus,
} from "../lib/brain";

// 当前面板：kind="panel" 事件整体替换刷新（webview 非空 → webview 面板，否则 schema 面板）
const current = ref<{
  panel: string;
  title: string;
  schema: any;
  webview: { html?: string } | null;
  data: Record<string, unknown>;
} | null>(null);
const errorText = ref(""); // 面板内顶部错误细条（不进对话气泡）
const pending = ref<{ id: string; skill: string; desc: string } | null>(null); // 内嵌确认条
let unlisten: (() => void) | null = null;
let unlistenFocus: (() => void) | null = null;

// ---- 工作台条状态 ----
type AvatarState = "idle" | "listen" | "think" | "work" | "say";
const state = ref<AvatarState>("idle");
const busy = computed(() => state.value !== "idle");
const focus = ref<PanelFocus | null>(null); // 当前面板焦点（同步给大脑）
const chipText = computed(() => {
  const t = focus.value?.item?.title;
  return t ? `在看：${t}` : "";
});
// ---- 对话浮层（工作台条上方）：输入/回复都留痕成时间线；一轮结束几秒后自动收起，角标可重开 ----
type ThreadMsg = { role: "user" | "ai" | "hint"; text: string };
const msgs = ref<ThreadMsg[]>([]);
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

/** 面板内容统一入口：赋值 + 重算焦点 + 上报大脑 + 会话分流 surface 随插件切换。 */
function setCurrent(v: typeof current.value) {
  current.value = v;
  focus.value = computeFocus(v);
  if (focus.value) setSurface(`panel:${focus.value.plugin}`);
  void reportPanelContext(focus.value).catch(() => {});
}

function onEvent(e: BrainEvent) {
  // 会话分流：宠物窗的对话事件不归这里；panel 事件例外（新面板内容必须接）
  if (e.kind !== "panel" && e.surface === "pet") return;
  switch (e.kind) {
    case "panel":
      setCurrent({
        panel: e.payload?.panel ?? "",
        title: e.payload?.title ?? e.payload?.panel ?? "",
        schema: (e.payload?.schema as any) ?? null,
        webview: (e.payload?.webview as { html?: string } | null) ?? null,
        data: e.payload?.data ?? {},
      });
      break;
    case "confirmation_needed":
      // 面板 action 触发的 L2+ 确认在面板内解决，不跳宠物窗
      pending.value = {
        id: e.confirmation_id ?? "",
        skill: e.action?.skill_id ?? "",
        desc: e.action?.description ?? "",
      };
      state.value = "idle";
      break;
    case "action_proposed":
      state.value = "work";
      break;
    case "action_result":
      pending.value = null; // 确认流结束（批准路径：执行结果回来了）
      break;
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
        msgs.value[streamingIdx.value].text += " ⛔";
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
      pending.value = null; // 确认流结束（拒绝路径）或执行失败
      errorText.value = e.text ?? "出错了";
      state.value = "idle";
      break;
  }
}

async function decide(approved: boolean) {
  if (!pending.value) return;
  const { id } = pending.value;
  pending.value = null;
  try {
    await sendConfirm(id, approved);
  } catch (err) {
    errorText.value = "确认失败：" + String(err);
  }
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
const barRef = ref<HTMLElement | null>(null);

function submit(text: string) {
  errorText.value = "";
  pushMsg("user", text); // 输入立刻有落点（浮层时间线）
  void runInput(text).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
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
  barRef.value?.querySelector("input")?.focus();
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
      webview: { html?: string } | null;
      data: Record<string, unknown>;
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

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
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
  if (collapseTimer !== null) clearTimeout(collapseTimer);
  // 窗口销毁（重载等）也清焦点，避免大脑留着旧上下文
  void reportPanelContext(null).catch(() => {});
});
</script>

<template>
  <div class="panel-shell">
    <div class="titlebar" data-tauri-drag-region>
      <span class="name">{{ current?.title ?? "面板" }}</span>
      <button class="x" title="关闭" @click="close">×</button>
    </div>

    <div v-if="pending" class="confirm-bar">
      <span class="c-text">⚠️ {{ pending.skill }}{{ pending.desc ? " · " + pending.desc : "" }}</span>
      <span class="c-btns">
        <button class="deny" @click="decide(false)">拒绝</button>
        <button class="ok" @click="decide(true)">允许</button>
      </span>
    </div>

    <div v-if="errorText" class="error-bar">⚠️ {{ errorText }}</div>

    <div class="content">
      <WebviewPanel
        v-if="current && webviewHtml"
        :key="current.panel"
        :panel="current.panel"
        :html="webviewHtml"
        :data="current.data"
      />
      <SchemaPanel
        v-else-if="current"
        :panel="current.panel"
        :schema="current.schema"
        :data="current.data"
        @action="onAction"
      />
      <div v-else class="placeholder">
        <div class="ph-icon">🍡</div>
        <div class="ph-title">这里还空空的</div>
        <div class="ph-hint">去跟译宝说一句试试，让它帮你打开想看的面板</div>
      </div>
    </div>

    <!-- 工作台条：对话浮层（输入/回复时间线）+ 团子 + 上下文 chip + 输入条 -->
    <div ref="barRef" class="bench">
      <transition name="pop">
        <div v-if="layerVisible && (msgs.length || listeningHint)" ref="layerRef" class="thread">
          <button class="thread-x" title="收起" @click="layerVisible = false">×</button>
          <div
            v-for="(m, i) in msgs"
            :key="i"
            class="t-row"
            :class="m.role"
            :title="m.role === 'user' ? m.text : undefined"
          >
            {{ m.text }}
          </div>
          <div v-if="listeningHint" class="t-row hint">🎙 聆听中…（点团子取消）</div>
        </div>
      </transition>
      <div class="bench-bar">
        <Avatar class="pet" :state="state" :size="30" @click="onPetTap" @longpress="onMic" />
        <button v-if="!layerVisible && msgs.length" class="thread-open" title="查看对话" @click="openLayer">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <span v-if="chipText" class="chip" :title="chipText">{{ chipText }}</span>
        <InputBar class="bench-input" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
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
  font-family: -apple-system, "PingFang SC", system-ui, sans-serif;
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
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.x {
  border: none;
  background: transparent;
  font-size: 18px;
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
  line-height: 1.4;
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
  color: #fff;
}
.deny {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
.error-bar {
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
  background: #f6f1ea;
  border-radius: inherit;
}
.ph-icon {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: #fff0e8;
  display: grid;
  place-items: center;
  font-size: 24px;
  margin-bottom: 4px;
}
.ph-title {
  font-size: 13px;
  font-weight: 600;
  color: #a89a86;
}
.ph-hint {
  font-size: 12px;
  color: #c9bcab;
}

/* ---- 工作台条 ---- */
.bench {
  position: relative;
  margin: 0 var(--yb-space-2) var(--yb-space-2);
}
.thread {
  position: absolute;
  left: 4px;
  right: 4px;
  bottom: calc(100% + 6px);
  max-height: 260px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--yb-space-3) var(--yb-space-4);
  border-radius: var(--yb-radius-lg);
  background: var(--yb-surface-solid);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow-soft);
  scrollbar-width: thin;
}
.thread-x {
  position: absolute;
  top: 6px;
  right: 8px;
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--yb-radius-sm);
}
.thread-x:hover {
  background: var(--yb-btn-neutral);
}
.t-row {
  padding: 4px 10px;
  border-radius: var(--yb-radius-md);
  font-size: var(--yb-fs-md);
  line-height: 1.6;
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
  line-height: 1.7;
}
.t-row.hint {
  align-self: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
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
  transition: filter 0.15s, color 0.15s;
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
  transition: opacity 0.25s ease, transform 0.25s ease;
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
