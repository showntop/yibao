<script setup lang="ts">
// 大窗「插件」页：插件列表（点击直达主面板）+ 面板嵌入视图（SchemaPanel/WebviewPanel + 工作台条）。
// 面板逻辑同源 PanelApp.vue（浮窗版）；差异：
//   ① 面板嵌在主区不弹窗，「返回插件列表」≈ 浮窗的关闭（清焦点上下文）；
//   ② 与 HomeChat 同窗共享 JS 上下文，surface 一律显式传参（不走模块级 setSurface）。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { emit as emitTauri } from "@tauri-apps/api/event";
import SchemaPanel from "./SchemaPanel.vue";
import WebviewPanel from "./WebviewPanel.vue";
import Avatar from "./Avatar.vue";
import InputBar from "./InputBar.vue";
import {
  onBrainEvent,
  panelAction,
  runInput,
  voiceStart,
  interrupt,
  reportPanelContext,
  type BrainEvent,
  type PanelFocus,
} from "../lib/brain";
import { procLabel, procSkip, procResultSuffix } from "../lib/proc";

type AvatarState = "idle" | "listen" | "think" | "work" | "say";
// state：同步给父级侧边栏团子（插件页活跃时团子跟着面板会话走）
// panel：新面板打开时外发（父级自动切到本页；同面板刷新/挂载补拉不发，不抢用户所在页）
const emit = defineEmits<{ state: [AvatarState]; panel: [] }>();

// ---- 插件列表 ----
interface PluginInfo { id: string; name: string }
const plugins = ref<PluginInfo[]>([]);
const pluginErr = ref("");
const viewingList = ref(true); // true=插件列表；false=面板视图（panel 事件到来自动切入）

async function loadPlugins() {
  pluginErr.value = "";
  try {
    plugins.value = await invoke<PluginInfo[]>("list_plugins");
  } catch (err) {
    plugins.value = [];
    pluginErr.value = String(err);
  }
}

/** 点插件 → 调它的 list 直调（约定的主面板入口）；panel 事件回来 setCurrent 自动切到面板视图。 */
async function launchPlugin(p: PluginInfo) {
  pluginErr.value = "";
  try {
    await panelAction(`${p.id}.list`, {}, undefined, `panel:${p.id}`);
  } catch (err) {
    pluginErr.value = "启动失败：" + String(err);
  }
}

// ---- 当前面板：kind="panel" 事件整体替换刷新（webview 非空 → webview 面板，否则 schema 面板）----
const current = ref<{
  panel: string;
  title: string;
  schema: any;
  webview: { html?: string } | null;
  data: Record<string, unknown>;
} | null>(null);
const errorText = ref(""); // 面板内顶部错误细条（不进对话气泡）
let unlisten: (() => void) | null = null;

// ---- 工作台条状态 ----
const state = ref<AvatarState>("idle");
const busy = computed(() => state.value !== "idle");
const focus = ref<PanelFocus | null>(null); // 当前面板焦点（同步给大脑）
/** 本页后续请求的 surface：面板活跃 = panel:<plugin>；列表态无面板会话（不会用到） */
const surface = computed(() => (focus.value ? `panel:${focus.value.plugin}` : "panel"));
const chipText = computed(() => {
  const t = focus.value?.item?.title;
  return t ? `在看：${t}` : "";
});
// ---- 对话浮层（工作台条上方）：输入/回复都留痕成时间线；一轮结束几秒后自动收起，角标可重开 ----
// proc = 过程展示行（工具调用 🔧→✅/❌，样式同 hint 淡色小字）
type ThreadMsg = { role: "user" | "ai" | "hint" | "proc"; text: string };
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

/** 面板内容统一入口：赋值 + 重算焦点 + 上报大脑 + 切到面板视图。
 *  silent=true（挂载补拉缓存）不外发 panel 信号——旧缓存不该把用户从别的页拽过来。 */
function setCurrent(v: NonNullable<typeof current.value>, silent = false) {
  const isNewPanel = current.value?.panel !== v.panel;
  current.value = v;
  viewingList.value = false;
  focus.value = computeFocus(v);
  void reportPanelContext(focus.value).catch(() => {});
  if (isNewPanel && !silent) emit("panel");
}

/** 返回插件列表 ≈ 浮窗的关闭：清焦点上下文（大脑不再注入旧面板），面板内容留着（再进秒开）。
 *  广播 panel-closed：对话页的「⇢ 协作」关联气泡收到收尾信号（浮窗模式由 Rust 窗隐发，大窗在这里发）。 */
function backToList() {
  viewingList.value = true;
  focus.value = null;
  void reportPanelContext(null).catch(() => {});
  void emitTauri("panel-closed").catch(() => {});
}

function onEvent(e: BrainEvent) {
  // 会话分流：宠物场景（对话页）的对话事件不归这里；panel 事件例外（新面板内容必须接）
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
    case "action_proposed":
      state.value = "work";
      // 过程行：🔧 技能短标签（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, msgs.value.length);
        msgs.value.push({ role: "proc", text: "🔧 " + procLabel(e.action) });
        scrollSoon();
      }
      break;
    case "action_result": {
      // 直调失败在此亮出（不是 error 事件，否则点了没反应）
      const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
      if (idx !== undefined) {
        // 过程行收尾：✅/❌（失败带 error 摘要）
        const ok = e.result?.success !== false;
        msgs.value[idx].text = (ok ? "✅ " : "❌ ") + procLabel(e.action) + procResultSuffix(e.result);
        procIdx.delete(e.action!.id!);
      } else if (e.result && !e.result.success) {
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
      errorText.value = e.text ?? "出错了";
      state.value = "idle";
      break;
  }
}

async function onAction(a: { method: string; params: Record<string, unknown> }) {
  errorText.value = "";
  try {
    await panelAction(a.method, a.params, undefined, surface.value);
  } catch (err) {
    errorText.value = "面板操作失败：" + String(err);
  }
}

// 工作台条交互：提交走同一 runInput（focus 已在大脑上下文里）；mic/长按团子 = 语音
const barRef = ref<HTMLElement | null>(null);

function submit(text: string) {
  errorText.value = "";
  pushMsg("user", text); // 输入立刻有落点（浮层时间线）
  void runInput(text, surface.value).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
}

function onMic() {
  void voiceStart(surface.value).catch((err) => {
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

/** 挂载补拉最近一次 panel 载荷：大窗可能在协作中途才打开（panel 事件先于本页订阅发出）。 */
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
      setCurrent({ ...cached, title: cached.title ?? cached.panel }, true);
    }
  } catch { /* 缓存缺失就停在列表页，无妨 */ }
}

// webview 面板 html（空串 → 走 schema 面板）
const webviewHtml = computed(() => current.value?.webview?.html ?? "");

watch(state, (s) => emit("state", s));

onMounted(async () => {
  unlisten = await onBrainEvent(onEvent);
  void loadPlugins();
  await pullCache();
  emit("state", state.value); // 父级侧边栏团子拿初始态
});
onUnmounted(() => {
  unlisten?.();
  if (collapseTimer !== null) clearTimeout(collapseTimer);
});
</script>

<template>
  <div class="plugins-page">
    <header class="page-head" data-tauri-drag-region>
      <template v-if="viewingList">
        <span class="pg-title" data-tauri-drag-region>插件</span>
      </template>
      <template v-else>
        <button class="back" @click="backToList">‹ 插件</button>
        <span class="pg-title">{{ current?.title ?? "面板" }}</span>
      </template>
    </header>

    <!-- 插件列表：点击直达它的主面板 -->
    <div v-if="viewingList" class="plist">
      <div v-if="pluginErr" class="pl-err">⚠️ {{ pluginErr }}</div>
      <button v-for="p in plugins" :key="p.id" class="pl-row" @click="launchPlugin(p)">
        <span class="pl-name">{{ p.name }}</span>
        <span class="pl-id">{{ p.id }}</span>
      </button>
      <div v-if="!plugins.length && !pluginErr" class="pl-empty">没有发现插件</div>
    </div>

    <!-- 面板视图：确认统一进主屏收件箱；这里只保留错误细条 / 面板内容 / 工作台条 -->
    <template v-else>
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
    </template>
  </div>
</template>

<style scoped>
.plugins-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 0 var(--yb-space-3) var(--yb-space-2);
}
/* 页头：标题（面板视图带返回）；整条兼作拖动区 */
.page-head {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) 2px var(--yb-space-2);
  user-select: none;
}
.pg-title {
  font-size: var(--yb-fs-xl);
  font-weight: 650;
  letter-spacing: 0.01em;
}
.back {
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: 13px;
  cursor: pointer;
  padding: 3px 8px;
  border-radius: 10px;
  transition: all 0.15s ease;
}
.back:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-surface-solid);
}

/* ---- 插件列表 ---- */
.plist {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: 2px;
  scrollbar-width: thin;
}
.plist::-webkit-scrollbar {
  width: 6px;
}
.plist::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: 3px;
}
.pl-err {
  padding: 6px var(--yb-space-3);
  border-radius: 10px;
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: 13px;
}
.pl-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  border: 1px solid var(--yb-surface-border);
  border-radius: 14px;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
  cursor: pointer;
  font-family: inherit;
  text-align: left;
  transition: all 0.15s ease;
}
.pl-row:hover {
  border-color: var(--yb-accent);
  transform: translateY(-1px);
}
.pl-name {
  font-size: var(--yb-fs-lg);
  font-weight: 500;
  color: var(--yb-text);
}
.pl-id {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.pl-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: 13px;
}

/* ---- 面板视图（与浮窗同款） ---- */
.error-bar {
  margin: 0 var(--yb-space-2) var(--yb-space-2);
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

/* ---- 工作台条 ---- */
.bench {
  position: relative;
  margin: 0 var(--yb-space-2);
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
/* 过程行：同 hint 淡色小字调性（🔧→✅/❌） */
.t-row.proc {
  align-self: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
  padding-top: 0;
  padding-bottom: 0;
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
