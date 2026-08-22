<script setup lang="ts">
// 大窗「插件」页：插件列表（点击直达主面板）+ 面板嵌入视图（SchemaPanel/WebviewPanel + 工作台条）。
// 面板逻辑同源 PanelApp.vue（浮窗版）；差异：
//   ① 面板嵌在主区不弹窗，「返回插件列表」≈ 浮窗的关闭（清焦点上下文）；
//   ② 与 HomeChat 同窗共享 JS 上下文，surface 一律显式传参（不走模块级 setSurface）。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { emit as emitTauri } from "@tauri-apps/api/event";
import SchemaPanel from "../../components/panel/SchemaPanel.vue";
import PluginGrid from "./PluginGrid.vue";
import WebviewPanel from "../../components/panel/WebviewPanel.vue";
import Avatar from "../../components/pet/Avatar.vue";
import InputBar from "../../components/common/InputBar.vue";
import YbIcon from "../../components/common/YbIcon.vue";
import {
  onBrainEvent,
  panelAction,
  runInput,
  voiceStart,
  interrupt,
  reportPanelContext,
  listPlugins,
  getCurrentPanel,
  type BrainEvent,
  type PanelFocus,
} from "../../lib/brain";
import { formatContextPrefix, type InputContext } from "../../lib/at-mention";
import { procLabel, procSkip, procResultSuffix } from "../../lib/proc";
import { sessionStore } from "../../state/store";
import type { Attention, Presentation } from "../../lib/surface/surface-policy";
import type { WebviewPayload } from "../../lib/webview-source";
import { usePanelGrow } from "../../composables/usePanelGrow";
import { usePluginOverlay } from "../../composables/usePluginOverlay";
import type { AvatarState } from "../../protocol/brain-types";

const props = withDefaults(defineProps<{
  scene?: boolean;
  presentation?: Presentation;
}>(), {
  scene: false,
  presentation: "stage",
});
/** 面板身份元数据（锚点定位用，不带裁决字段） */
type SurfaceMeta = { panel: string; title: string; plugin: string; objectTitle?: string };
/** 父级 surface 裁决事件：后端建议（suggested/attention/supported）+ 前端 explicit 推断一起走裁决器 */
interface CapabilitySurfaceEvent extends SurfaceMeta {
  explicit: boolean;
  suggested: Presentation | null;
  attention: Attention;
  supported?: Presentation[];
}
// state：同步给父级侧边栏团子（插件页活跃时团子跟着面板会话走）
// panel：新面板打开时外发（父级自动切到本页；同面板刷新/挂载补拉不发，不抢用户所在页）
const emit = defineEmits<{
  state: [AvatarState];
  panel: [surface: CapabilitySurfaceEvent];
  surface: [surface: SurfaceMeta];
  close: [];
  focus: [];
  handoff: [active: boolean];
}>();

// ---- 插件列表 ----
interface PluginPanelEntry { name: string; label: string; open: string }
interface PluginInfo { id: string; name: string; panels?: PluginPanelEntry[] }
const plugins = ref<PluginInfo[]>([]);
const pluginErr = ref("");
const viewingList = ref(true); // true=插件列表；false=面板视图（panel 事件到来自动切入）
let requestedPlugin = "";
let requestedUntil = 0;

// ---- 面板"从来源长出"动效：记录触发插件卡的位置，面板用 clip-path 从卡片矩形生长/缩回（同源缩回） ----
const { originRect, collapseAnchor, panelViewEl, captureOrigin, growIn, collapseOut } = usePanelGrow();
// ---- 对话浮层（composables/usePluginOverlay）：输入/回复留痕时间线 + 自动收起 ----
const {
  msgs,
  procIdx,
  streamingIdx,
  layerVisible,
  listeningHint,
  layerRef,
  openLayer,
  scheduleCollapse,
  pushMsg,
  scrollSoon,
  dispose: overlayDispose,
} = usePluginOverlay();

/** 场景收起：让面板先"缩回"（布局保持场景，避免瞬切），保留锚点供恢复时对称长回。 */
function collapseScene(): Promise<void> {
  if (!current.value) return Promise.resolve();
  return collapseOut(true);
}

async function loadPlugins() {
  pluginErr.value = "";
  try {
    plugins.value = await listPlugins();
  } catch (err) {
    plugins.value = [];
    pluginErr.value = String(err);
  }
}

/** 点插件 → 调它的 list 直调（约定的主面板入口）；panel 事件回来 setCurrent 自动切到面板视图。 */
async function launchPlugin(p: PluginInfo, event?: MouseEvent) {
  captureOrigin(event);
  pluginErr.value = "";
  requestedPlugin = p.id;
  requestedUntil = Date.now() + 8000;
  try {
    await panelAction(`${p.id}.list`, {}, undefined, `panel:${p.id}`);
  } catch (err) {
    requestedUntil = 0;
    pluginErr.value = "启动失败：" + String(err);
  }
}

/** 点面板子入口（素材库/热点雷达等）→ 调 manifest [[panel]] open 声明的 api 方法；panel 事件回来切视图。 */
async function openPluginPanel(p: PluginInfo, panel: PluginPanelEntry, event?: MouseEvent) {
  captureOrigin(event);
  pluginErr.value = "";
  requestedPlugin = p.id;
  requestedUntil = Date.now() + 8000;
  try {
    await panelAction(`${p.id}.${panel.open}`, {}, undefined, `panel:${p.id}`);
  } catch (err) {
    requestedUntil = 0;
    pluginErr.value = "打开失败：" + String(err);
  }
}

// ---- 当前面板：kind="panel" 事件整体替换刷新（webview 非空 → webview 面板，否则 schema 面板）----
// hints：后端随 panel 事件透传的表面建议（presentation/attention/surfaces），父级裁决用
const current = ref<{
  panel: string;
  title: string;
  schema: any;
  webview: WebviewPayload | null;
  data: Record<string, unknown>;
  input?: "inherit" | "coexist" | "handoff" | "none";
  hints?: { presentation: Presentation | null; attention: Attention; surfaces?: Presentation[] };
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
// ---- 大窗输入条 handoff(panel-input-modes spec §C):声明制,与 PanelApp 同规则——
//      input ∈ {handoff, none} 壳条让位;逃生口 = 顶部「主屏」tab,不加新元素 ----
const handoff = computed(() => {
  const m = current.value?.input;
  return m === "handoff" || m === "none";
});
watch(handoff, (active) => emit("handoff", active), { immediate: true });
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
  const wasList = viewingList.value;
  current.value = v;
  viewingList.value = false;
  focus.value = computeFocus(v);
  // 面板载荷写入 surface 域：重启后恢复工作面的数据来源（Tauri last_panel 是内存态，重启即失）
  sessionStore.surface.setPanel({
    panel: v.panel,
    title: v.title,
    schema: v.schema ?? null,
    data: v.data ?? {},
    webview: v.webview ?? null,
  });
  // panel 事件可以先于用户展开工作面到达；隐藏能力不得抢占“当前对象”。
  void reportPanelContext(props.scene ? focus.value : null).catch(() => {});
  const plugin = v.panel.split(":", 1)[0] || v.panel;
  const meta = {
    panel: v.panel,
    title: v.title,
    plugin,
    objectTitle: typeof focus.value?.item?.title === "string" ? focus.value.item.title : undefined,
  };
  emit("surface", meta);
  if (isNewPanel && !silent) {
    // 表面裁决输入：本地时间窗推断只是 explicit 的来源之一；presentation/attention 从后端透传
    const explicit = requestedPlugin === plugin && Date.now() <= requestedUntil;
    emit("panel", {
      ...meta,
      explicit,
      suggested: v.hints?.presentation ?? null,
      attention: v.hints?.attention ?? "suggest",
      supported: v.hints?.surfaces ?? undefined,
    });
  }
  requestedUntil = 0;
  if (wasList) void nextTick(() => growIn());
}

/** 返回插件列表 ≈ 浮窗的关闭：清焦点上下文（大脑不再注入旧面板），面板内容留着（再进秒开）。
 *  广播 panel-closed：对话页的「⇢ 协作」关联气泡收到收尾信号（浮窗模式由 Rust 窗隐发，大窗在这里发）。 */
async function backToList() {
  await collapseOut();
  viewingList.value = true;
  focus.value = null;
  sessionStore.surface.clearScene();
  void reportPanelContext(null).catch(() => {});
  void emitTauri("panel-closed").catch(() => {});
  emit("close");
}

/** 工作面收起时只暂停对象注入，保留面板数据与滚动状态，活动胶囊可原样恢复。 */
function suspendSurface() {
  focus.value = null;
  void reportPanelContext(null).catch(() => {});
}

function restoreSurface() {
  if (!current.value) return false;
  viewingList.value = false;
  focus.value = computeFocus(current.value);
  void reportPanelContext(focus.value).catch(() => {});
  // 恢复时若有任何锚点（用户卡片 originRect 或上次收起锚点 collapseAnchor），从同处长回
  if (originRect.value || collapseAnchor.value) void nextTick(() => growIn());
  return true;
}

/** Esc 第一层只清对象作用域，不退出工作面。 */
function clearObjectScope(): boolean {
  if (!focus.value?.item) return false;
  focus.value = { ...focus.value, item: null };
  void reportPanelContext(focus.value).catch(() => {});
  if (current.value) {
    emit("surface", {
      panel: current.value.panel,
      title: current.value.title,
      plugin: current.value.panel.split(":", 1)[0] || current.value.panel,
    });
  }
  return true;
}

defineExpose({ backToList, suspendSurface, restoreSurface, clearObjectScope, collapseScene });

function onEvent(e: BrainEvent) {
  // 会话分流：宠物场景（对话页）的对话事件不归这里；panel/panel_data 例外（面板内容必须接）
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
        hints: {
          presentation: (e.payload?.presentation as Presentation | null | undefined) ?? null,
          attention: (e.payload?.attention as Attention | undefined) ?? "suggest",
          surfaces: e.payload?.surfaces as Presentation[] | undefined,
        },
      });
      break;
    case "action_proposed":
      state.value = "work";
      // 过程行：技能短标签 + 进行中状态（use_plugin 跳过——成功有 notice，不重复）
      if (e.action?.id && !procSkip(e.action)) {
        if (streamingIdx.value !== null) streamingIdx.value = null;
        procIdx.set(e.action.id, msgs.value.length);
        msgs.value.push({ role: "proc", text: procLabel(e.action), pstate: "run" });
        scrollSoon();
      }
      break;
    case "action_result": {
      // 直调失败在此亮出（不是 error 事件，否则点了没反应）
      const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
      if (idx !== undefined) {
        // 过程行收尾：成功/失败改 pstate（失败带 error 摘要）
        const ok = e.result?.success !== false;
        msgs.value[idx].pstate = ok ? "ok" : "fail";
        msgs.value[idx].text = procLabel(e.action) + procResultSuffix(e.result);
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

async function onAction(a: { method: string; params: Record<string, unknown> }) {
  errorText.value = "";
  try {
    await panelAction(a.method, a.params, undefined, surface.value);
  } catch (err) {
    errorText.value = "面板操作失败：" + String(err);
  }
}

// 工作台条交互：提交走同一 runInput（focus 已在大脑上下文里）；mic/长按团子 = 语音
const inputBarRef = ref<{ focus: () => void } | null>(null);

function submit(text: string, contexts: InputContext[] = []) {
  errorText.value = "";
  const t = formatContextPrefix(contexts) + text;   // @ 文件/附件 chips 前缀进文本
  pushMsg("user", t); // 输入立刻有落点（浮层时间线）
  void runInput(t, surface.value).catch((err) => {
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
  inputBarRef.value?.focus(); // 经 InputBar expose 聚焦主 textarea（querySelector("input") 会误中隐藏 file-input）
}

/** 挂载补拉最近一次 panel 载荷：大窗可能在协作中途才打开（panel 事件先于本页订阅发出）。
 *  Tauri 侧 last_panel 是内存态（重启即失）：缓存缺失时回退 localStorage 面板快照，
 *  重启后据此恢复「正在和 xxx 协作中」的工作面（快照回填给 Rust，多窗一致）。 */
async function pullCache() {
  try {
    const cached = await getCurrentPanel();
    if (cached && current.value === null) {
      setCurrent({ ...cached, title: cached.title ?? cached.panel }, true);
      return;
    }
  } catch { /* Tauri 缓存缺失/不可用（含重启后内存态已清）→ 走 surface 域回退 */ }

  if (current.value === null) {
    const panel = sessionStore.surface.getPanel();
    if (panel) {
      setCurrent(
        { panel: panel.panel, title: panel.title, schema: panel.schema, webview: panel.webview, data: panel.data },
        true,
      );
    }
  }
}

// webview 面板 html（空串 → 走 schema 面板）
const webviewHtml = computed(() => current.value?.webview?.html ?? "");
// module 面板(R4):url/v 直传 WebviewPanel;空串 → 与 html 一起判空走 schema/占位
const webviewUrl = computed(() => current.value?.webview?.url ?? "");
const webviewV = computed(() => current.value?.webview?.v ?? 0);

watch(state, (s) => emit("state", s));

onMounted(async () => {
  const qaMode = import.meta.env.DEV && new URLSearchParams(window.location.search).get("qa") === "capability";
  if (qaMode) {
    plugins.value = [{ id: "zimeiti", name: "自媒体" }];
    requestedPlugin = "zimeiti";
    requestedUntil = Date.now() + 8000;
    setCurrent({
      panel: "zimeiti:board",
      title: "自媒体 · 选题看板",
      schema: {
        version: 1,
        type: "board",
        bind: { items: "$data.rows", column: "$item.status" },
        columns: [
          { key: "候选", label: "候选", color: "#9c8b7a" },
          { key: "写作中", label: "写作中", color: "#ff8a5c" },
          { key: "待发布", label: "待发布", color: "#5b8def" },
          { key: "已发布", label: "已发布", color: "#58b368" },
        ],
        card: { title: "$item.title", subtitle: "$item.angle", actions: [] },
        quick_add: { method: "zimeiti.add", params: { title: "$text" }, column: "候选", placeholder: "快速记一条选题…" },
      },
      webview: null,
      data: {
        rows: [
          { id: 1, title: "应用消失之后，任务如何拥有屏幕", angle: "从页面导航转向能力表面", status: "候选" },
          { id: 2, title: "AI OS 的关键不是万能输入框", angle: "状态、权限与可逆性才是底座", status: "候选" },
          { id: 3, title: "插件不是目的地，而是临时长出的手", angle: "用译宝的真实交互做开场", status: "写作中" },
          { id: 4, title: "为什么 Agent 不该自动抢焦点", angle: "从桌面心流讨论主动权", status: "待发布" },
          { id: 5, title: "从应用接力到对象接力", angle: "邮件、日历和提醒的协作模型", status: "已发布" },
        ],
      },
    });
    emit("state", state.value);
    return;
  }
  // 事件订阅失败（非 Tauri / 竞态）不阻塞缓存恢复——pullCache 是重启后恢复工作面的关键路径
  try {
    unlisten = await onBrainEvent(onEvent);
  } catch { /* 静默：仍尝试拉取面板缓存/快照 */ }
  void loadPlugins();
  await pullCache();
  emit("state", state.value); // 父级侧边栏团子拿初始态
});
onUnmounted(() => {
  unlisten?.();
  overlayDispose();
});
</script>

<template>
  <div class="plugins-page" :class="{ 'in-scene': props.scene, 'is-focus': props.presentation === 'focus', 'on-desk': props.scene && !viewingList }">
    <!-- 页头：列表态是页标题；插件 tab 里的面板是层级导航。桌上工位把页头让给 HomeDeskWork。 -->
    <header v-if="!(props.scene && !viewingList)" class="page-head" :class="{ 'in-panel': !viewingList }" data-tauri-drag-region>
      <template v-if="viewingList">
        <div class="head-text" data-tauri-drag-region>
          <h1 class="pg-title" data-tauri-drag-region>插件</h1>
          <span class="pg-sub" data-tauri-drag-region>点开任意插件进入它的工作面板</span>
        </div>
      </template>
      <template v-else>
        <button class="back" :title="props.scene ? '收起工作面' : '返回插件列表'" @click="props.scene ? emit('close') : backToList()">
          <YbIcon name="x" :size="13" />
        </button>
        <span class="crumb">{{ props.scene ? "当前任务" : "插件" }}</span>
        <span class="crumb-sep">›</span>
        <span class="pg-title panel-name">{{ current?.title ?? "面板" }}</span>
        <span v-if="props.scene" class="scene-spacer" />
        <span v-if="props.scene" class="scene-source"><i :class="{ live: busy }" />{{ busy ? "译宝正在协作" : "已连接" }}</span>
        <button v-if="props.scene" class="scene-action" :title="props.presentation === 'focus' ? '退出专注' : '进入专注'" @click="emit('focus')">
          <YbIcon name="expand" :size="13" />
          <span>{{ props.presentation === "focus" ? "退出专注" : "专注" }}</span>
        </button>
      </template>
    </header>

    <!-- 插件列表：Launchpad 式网格（图标按 id 哈希配色），顶部搜索过滤 -->
    <PluginGrid
      v-if="viewingList"
      :plugins="plugins"
      :err="pluginErr"
      @launch="launchPlugin"
      @open-panel="openPluginPanel"
    />

    <!-- 面板视图：确认统一进主屏收件箱；这里只保留错误细条 / 面板内容 / 工作台条。
         外层宿主承载"从来源长出"动效：clip-path 从插件卡矩形展开到全面板，收起时同源缩回 -->
    <template v-else>
      <div ref="panelViewEl" class="panel-grow">
        <div v-if="errorText" class="error-bar"><YbIcon name="alert" :size="14" />{{ errorText }}</div>

        <div class="content">
          <WebviewPanel
            v-if="current && (webviewHtml || webviewUrl)"
            :key="current.panel"
            :panel="current.panel"
            :html="webviewHtml"
            :url="webviewUrl"
            :v="webviewV"
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

        <!-- 工作台条：插件 tab 里保留。桌上工位用译宝桌沿 / 工人自带 Composer，这里不再养第二套助手。 -->
        <div v-if="!props.scene" class="bench" :class="{ 'is-handoff': handoff }">
          <transition name="pop">
            <div v-if="layerVisible && (msgs.length || listeningHint)" ref="layerRef" class="thread">
              <button class="thread-x" title="收起" @click="layerVisible = false">×</button>
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
            <InputBar ref="inputBarRef" class="bench-input" :busy="busy" :listening="state === 'listen'" @submit="submit" @mic="onMic" @interrupt="onInterrupt" />
          </div>
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
  background: var(--yb-content-bg);
}
.plugins-page.on-desk {
  background: transparent;
}
/* 页头：列表态留红绿灯安全区 + 页标题；面板态压缩成一行层级导航。整条兼作拖动区 */
.page-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 0 var(--yb-space-5) var(--yb-space-4);
  user-select: none;
}
.page-head.in-panel {
  padding: 0 var(--yb-space-4) var(--yb-space-2);
  border-bottom: 1px solid var(--yb-border-base);
}
.in-scene .page-head.in-panel {
  height: 62px;
  box-sizing: border-box;
  padding: 0 14px;
  background: rgba(255, 255, 255, 0.58);
}
.head-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.pg-title {
  margin: 0;
  font-size: 26px;
  font-weight: var(--yb-fw-bold);
  letter-spacing: -0.01em;
  line-height: var(--yb-lh-tight);
  color: var(--yb-text-strong);
}
.pg-sub {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
/* 面板态：面板名降回常规字号（不再是页标题，是层级末端） */
.panel-name {
  font-size: var(--yb-fs-xl);
}
.crumb {
  font-size: var(--yb-fs-xl);
  color: var(--yb-text-dim);
  cursor: default;
}
.crumb-sep {
  color: var(--yb-text-faint);
}
.scene-spacer { flex: 1; min-width: 8px; }
.scene-source {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  white-space: nowrap;
}
.scene-source i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--yb-success);
}
.scene-source i.live {
  background: var(--yb-accent);
  box-shadow: 0 0 0 4px rgba(var(--yb-c-sky-rgb), 0.1);
}
.scene-action {
  height: 27px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 0 8px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
}
.scene-action:hover { border-color: rgba(var(--yb-c-sky-rgb), 0.28); color: var(--yb-accent); }
/* 返回：macOS 用左上角圆形关闭按钮语义（这里是「离开面板回列表」） */
.back {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.back:hover {
  background: var(--yb-btn-neutral-hover);
  color: var(--yb-text);
}

/* ---- 插件列表：macOS 图标网格 ---- */
/* ---- 面板视图（与浮窗同款） ---- */
.error-bar {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  margin: var(--yb-space-2) var(--yb-space-4) 0;
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
/* 面板视图宿主：承载 clip-path 生长动效；内容三块纵向排布 */
.panel-grow {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  will-change: clip-path, opacity, transform;
}
.content {
  flex: 1;
  min-height: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
}
.on-desk .content :deep(.webview) {
  flex: 1;
  min-height: 0;
  border-radius: 0;
}
.in-scene .content {
  background:
    radial-gradient(90% 50% at 50% 0%, rgba(var(--yb-c-sky-rgb), 0.035), transparent 68%),
    var(--yb-content-bg);
}

/* ---- 工作台条 ---- */
.bench {
  position: relative;
  flex-shrink: 0;
  padding: var(--yb-space-3) var(--yb-space-4);
  border-top: 1px solid var(--yb-border-base);
  background: var(--yb-content-bg);
}
.in-scene .bench {
  padding: 10px 14px 12px;
  background: rgba(255, 255, 255, 0.76);
  backdrop-filter: blur(18px);
}
.is-focus .bench { padding-left: 24px; padding-right: 24px; }
/* handoff 让位:bench-bar 已移除,容器垂直 padding 与分隔线一并让位——空 bench 高 0,面板内容到底不留缝隙。
   水平 padding 保留:高度为 0 时不可见,也无需区分 in-scene/is-focus 变体(规则顺序在后,覆盖它们) */
.bench.is-handoff { padding-top: 0; padding-bottom: 0; border-top: none; }

@media (max-width: 720px) {
  .scene-source { display: none; }
  .scene-action span { display: none; }
  .panel-name { max-width: 48vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
}
.thread {
  position: absolute;
  left: var(--yb-space-4);
  right: var(--yb-space-4);
  bottom: calc(100% - var(--yb-space-1));
  max-height: 260px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--yb-space-3) var(--yb-space-4);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-card-border);
  box-shadow: var(--yb-shadow-3);
  scrollbar-width: thin;
}
.thread-x {
  position: absolute;
  top: 6px;
  right: 8px;
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
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
