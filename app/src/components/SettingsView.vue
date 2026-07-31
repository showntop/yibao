<script setup lang="ts">
// 设置页（home 大窗唯一内容）：模型/语音（保存后重启大脑生效）+ 通用/权限/数据（即时生效）。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import { openUrl } from "@tauri-apps/plugin-opener";
import YbIcon from "./YbIcon.vue";
import {
  getSetupConfig,
  saveSetupConfig,
  restartBrain,
  clearBrainData,
  openDataDir,
  checkPermissions,
  promptPermission,
  onBrainStatus,
  onBrainPermissions,
  getMemListOnce,
  memDelete,
  memEdit,
  getSettingsOnce,
  setSettings,
  getPerceptionOnce,
  deletePerception,
  clearPerception,
  type BrainPermissions,
  type ClearKind,
  type MemItem,
  type PerceptionItem,
  type SettingsValues,
} from "../lib/brain";

// ---- 分类导航（macOS 系统设置语言）----
// 原先 11 个分组平铺一列要滚很久，且「感知日志」「记忆管理」这种数据浏览器
// 混在开关中间。按语义收成 4 类，每类内仍是分组卡片。
type Cat = "general" | "proactive" | "privacy" | "data";
const cat = ref<Cat>("general");
const CATS: { id: Cat; label: string; icon: "gear" | "sparkle" | "lock" | "doc" }[] = [
  { id: "general", label: "通用", icon: "gear" },
  { id: "proactive", label: "主动协助", icon: "sparkle" },
  { id: "privacy", label: "隐私与权限", icon: "lock" },
  { id: "data", label: "数据与记忆", icon: "doc" },
];

// ---- 模型 / 语音（写 .env，重启大脑生效）----
const key = ref(""); // 留空 = 不改动已有 key
const hasKey = ref(false);
const model = ref("glm-4.6");
const baseUrl = ref("");
const voice = ref("zh-CN-XiaoxiaoNeural");
const voiceEnabled = ref(true);
const saving = ref(false);
const saveErr = ref("");
const saveMsg = ref("");

// edge-tts 常用中文音色（与首启向导一致）
const VOICES: [string, string][] = [
  ["zh-CN-XiaoxiaoNeural", "晓晓（女声·活泼）"],
  ["zh-CN-XiaoyiNeural", "晓伊（女声·温柔）"],
  ["zh-CN-YunxiNeural", "云希（男声·清亮）"],
  ["zh-CN-YunjianNeural", "云健（男声·沉稳）"],
];

async function save() {
  saving.value = true;
  saveErr.value = "";
  saveMsg.value = "";
  try {
    await saveSetupConfig({
      key: key.value,
      model: model.value,
      baseUrl: baseUrl.value,
      voice: voice.value,
      voiceEnabled: voiceEnabled.value,
    });
    key.value = ""; // 已落盘，清掉输入框里的明文
    hasKey.value = true;
    await restartBrain();
    saveMsg.value = "已保存，正在重启大脑…"; // 大脑上线后由 brain-status 收尾
  } catch (e) {
    saveErr.value = String(e);
  } finally {
    saving.value = false;
  }
}

// ---- 通用：开机启动（即时生效，不落 .env 不重启）----
const autoStart = ref(false);
const autoStartErr = ref("");

async function toggleAutostart() {
  autoStartErr.value = "";
  const next = !autoStart.value;
  try {
    if (next) await enable();
    else await disable();
    autoStart.value = next;
  } catch (e) {
    autoStartErr.value = String(e);
  }
}

// ---- 权限（复用引导横幅的检测/授权链路，视觉收敛为设置行）----
// home 大窗独立挂载，收不到宠物窗的 perms prop：自行订阅 brain-permissions 广播 + 挂载时主动拉一次
const perms = ref<BrainPermissions | null>(null);
const SETTINGS_URLS: Record<"ax" | "screen" | "input", string> = {
  ax: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  screen: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
  input: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
};

function grant(which: "ax" | "screen" | "input") {
  // 双管齐下：系统授权弹窗（仅首次有效）+ 打开对应设置面板
  void promptPermission(which).catch(() => {});
  void openUrl(SETTINGS_URLS[which]).catch(() => {});
}

function recheck() {
  void checkPermissions().catch(() => {});
}

// ---- 数据：清空（行内二次确认）----
const confirming = ref<ClearKind | null>(null);
const clearing = ref<ClearKind | null>(null);
const clearMsg = ref("");
const clearErr = ref(false); // 清空结果成/败：驱动 s-msg 色与图标，不再靠 ⚠️ 前缀判错

async function doClear(kind: ClearKind) {
  confirming.value = null;
  clearing.value = kind;
  clearMsg.value = "";
  clearErr.value = false;
  try {
    await clearBrainData(kind);
    clearMsg.value = kind === "memory" ? "✓ 长期记忆已清空" : "✓ 对话历史已清空";
  } catch (e) {
    clearMsg.value = String(e);
    clearErr.value = true;
  } finally {
    clearing.value = null;
  }
}

// ---- 自主权（数据目录 settings.json，即时生效免重启）----
const proactiveVoice = ref(true);
const proactiveLevel = ref<"quiet" | "bubble" | "full">("full");
// TTS 引擎（settings.json；切换下次启动生效）
const ttsProvider = ref<"edge" | "cosyvoice" | "cosyvoice_cloud">("edge");
const ttsErr = ref("");
// 主动协助（settings.json；即时生效）
const watchEnabled = ref(false);
const watchScreenEnabled = ref(false);
const watchIdleWarn = ref(45);
const watchQuietHours = ref("23:00-07:00");
const watchObserveApps = ref("");
const watchLookGap = ref(300);
const watchMaxHour = ref(6);
const watchMaxDay = ref(50);
const watchStatus = ref<SettingsValues["watch.status"] | null>(null);
const watchErr = ref("");
const autonErr = ref("");

async function toggleProactiveVoice() {
  autonErr.value = "";
  const next = !proactiveVoice.value;
  proactiveVoice.value = next; // 乐观更新，失败回滚
  const r = await setSettings({ proactive_voice: next });
  if (r === null) {
    proactiveVoice.value = !next;
    autonErr.value = "设置未生效（大脑不在线？）";
  }
}

async function setProactiveLevel(lv: "quiet" | "bubble" | "full") {
  if (lv === proactiveLevel.value) return;
  autonErr.value = "";
  const prev = proactiveLevel.value;
  proactiveLevel.value = lv; // 乐观更新，失败回滚
  const r = await setSettings({ "proactive.level": lv });
  if (r === null) {
    proactiveLevel.value = prev;
    autonErr.value = "设置未生效（大脑不在线？）";
  }
}

async function setTtsProvider(p: "edge" | "cosyvoice" | "cosyvoice_cloud") {
  if (p === ttsProvider.value) return;
  ttsErr.value = "";
  const prev = ttsProvider.value;
  ttsProvider.value = p; // 乐观更新，失败回滚
  const r = await setSettings({ "tts.provider": p });
  if (r === null) {
    ttsProvider.value = prev;
    ttsErr.value = "设置未生效（大脑不在线？）";
  }
}

async function _setWatch(patch: Record<string, unknown>, onFail: () => void) {
  watchErr.value = "";
  const r = await setSettings(patch);
  if (r === null) {
    onFail();
    watchErr.value = "设置未生效（大脑不在线？）";
    return;
  }
  syncWatchSettings(r);
}

function syncWatchSettings(s: SettingsValues) {
  watchEnabled.value = s["watch.enabled"] === true;
  watchScreenEnabled.value = s["watch.screen_enabled"] === true;
  if (typeof s["watch.idle_warn_minutes"] === "number") watchIdleWarn.value = s["watch.idle_warn_minutes"];
  if (typeof s["watch.quiet_hours"] === "string") watchQuietHours.value = s["watch.quiet_hours"];
  if (Array.isArray(s["watch.observe_apps"])) watchObserveApps.value = s["watch.observe_apps"].join("\n");
  if (typeof s["watch.look_min_gap"] === "number") watchLookGap.value = s["watch.look_min_gap"];
  if (typeof s["watch.look_max_per_hour"] === "number") watchMaxHour.value = s["watch.look_max_per_hour"];
  if (typeof s["watch.look_max_per_day"] === "number") watchMaxDay.value = s["watch.look_max_per_day"];
  const status = s["watch.status"];
  if (status && typeof status === "object") watchStatus.value = status;
  syncPerceptionSettings(s);
}

const watchStatusText = computed(() => {
  if (!watchStatus.value?.running) return "已停止";
  const active = [
    watchEnabled.value && `健康提醒${watchStatus.value.health_available ? "运行中" : "不可用"}`,
    watchScreenEnabled.value && `屏幕建议${watchStatus.value.screen_available ? "运行中" : "不可用"}`,
  ].filter(Boolean);
  return active.join(" · ");
});
async function toggleWatch() {
  const next = !watchEnabled.value;
  watchEnabled.value = next;
  await _setWatch({ "watch.enabled": next }, () => { watchEnabled.value = !next; });
}
async function toggleWatchScreen() {
  const next = !watchScreenEnabled.value;
  watchScreenEnabled.value = next;
  await _setWatch({ "watch.screen_enabled": next }, () => { watchScreenEnabled.value = !next; });
}
async function setWatchIdleWarn(n: number) {
  if (!Number.isFinite(n) || n < 5) return;
  const prev = watchIdleWarn.value;
  watchIdleWarn.value = n;
  await _setWatch({ "watch.idle_warn_minutes": n }, () => { watchIdleWarn.value = prev; });
}
async function setWatchQuietHours(v: string) {
  const normalized = v.trim();
  if (normalized && !/^(?:[01]?\d|2[0-3]):[0-5]\d-(?:[01]?\d|2[0-3]):[0-5]\d$/.test(normalized)) {
    watchErr.value = "静默时段格式应为 HH:MM-HH:MM，例如 23:00-07:00";
    return;
  }
  const prev = watchQuietHours.value;
  watchQuietHours.value = normalized;
  await _setWatch({ "watch.quiet_hours": normalized }, () => { watchQuietHours.value = prev; });
}
async function saveWatchScreenOptions() {
  const apps = watchObserveApps.value.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
  if (!apps.every((item) => /^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/.test(item))) {
    watchErr.value = "请填写 bundle id，例如 com.microsoft.VSCode；每行一个";
    return;
  }
  await _setWatch({
    "watch.observe_apps": apps,
    "watch.look_min_gap": Math.max(30, watchLookGap.value),
    "watch.look_max_per_hour": Math.max(1, watchMaxHour.value),
    "watch.look_max_per_day": Math.max(1, watchMaxDay.value),
  }, () => {});
}

// ---- 感知（默认关闭；settings 即时生效；日志内容由 sidecar 临时解密给 UI）----
const perceptionMaster = ref(false);
const perceptionApp = ref(false);
const perceptionActivity = ref(false);
const perceptionModelAccess = ref(false);
const perceptionItems = ref<PerceptionItem[]>([]);
const perceptionAvailable = ref(true);
const perceptionLoaded = ref(false);
const perceptionLoading = ref(false);
const perceptionHasMore = ref(false);
const perceptionErr = ref("");
const perceptionConfirming = ref<number | "all" | null>(null);
const perceptionDeleting = ref<number | "all" | null>(null);

function syncPerceptionSettings(s: SettingsValues) {
  perceptionMaster.value = s["perception.master"] === true;
  perceptionApp.value = s["perception.app"] === true;
  perceptionActivity.value = s["perception.activity"] === true;
  perceptionModelAccess.value = s["perception.model_access"] === true;
}

async function setPerceptionSetting(
  key:
    | "perception.master"
    | "perception.app"
    | "perception.activity"
    | "perception.model_access",
  next: boolean,
) {
  perceptionErr.value = "";
  const old = {
    "perception.master": perceptionMaster.value,
    "perception.app": perceptionApp.value,
    "perception.activity": perceptionActivity.value,
    "perception.model_access": perceptionModelAccess.value,
  };
  if (key === "perception.master") perceptionMaster.value = next;
  if (key === "perception.app") perceptionApp.value = next;
  if (key === "perception.activity") perceptionActivity.value = next;
  if (key === "perception.model_access") perceptionModelAccess.value = next;
  const r = await setSettings({ [key]: next });
  if (r === null) {
    perceptionMaster.value = old["perception.master"];
    perceptionApp.value = old["perception.app"];
    perceptionActivity.value = old["perception.activity"];
    perceptionModelAccess.value = old["perception.model_access"];
    perceptionErr.value = "设置未生效（大脑不在线？）";
    return;
  }
  syncPerceptionSettings(r);
  if (key === "perception.master" && next && !perceptionMaster.value) {
    perceptionErr.value = "感知存储不可用，已保持关闭";
  }
}

async function loadPerception(more = false) {
  perceptionLoading.value = true;
  perceptionErr.value = "";
  const beforeId = more && perceptionItems.value.length
    ? perceptionItems.value[perceptionItems.value.length - 1].id
    : undefined;
  const r = await getPerceptionOnce(50, beforeId);
  perceptionAvailable.value = r.available;
  if (r.error) perceptionErr.value = r.error;
  if (more) {
    const known = new Set(perceptionItems.value.map((x) => x.id));
    perceptionItems.value.push(...r.items.filter((x) => !known.has(x.id)));
  } else {
    perceptionItems.value = r.items;
  }
  perceptionHasMore.value = r.items.length === 50;
  perceptionLoaded.value = true;
  perceptionLoading.value = false;
}

function perceptionText(item: PerceptionItem): string {
  if (item.source === "app") {
    const app = String(item.payload.app || "未知应用");
    const title = String(item.payload.title || "");
    return title ? `${app} — ${title}` : app;
  }
  if (item.source === "activity") {
    const seconds = Number(item.payload.idle_seconds || 0);
    return item.kind === "idle" ? `进入空闲 · ${Math.max(1, Math.round(seconds / 60))} 分钟` : "恢复活跃";
  }
  return String(item.payload.text || item.kind);
}

function perceptionSource(item: PerceptionItem): string {
  return item.source === "app" ? "应用" : item.source === "activity" ? "活动" : item.source;
}

function relativeTime(ts: number): string {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

async function doPerceptionDelete(id: number) {
  perceptionConfirming.value = null;
  perceptionDeleting.value = id;
  const r = await deletePerception(id);
  perceptionDeleting.value = null;
  if (r.ok) perceptionItems.value = perceptionItems.value.filter((x) => x.id !== id);
  else perceptionErr.value = r.error || "删除失败";
}

async function doPerceptionClear() {
  perceptionConfirming.value = null;
  perceptionDeleting.value = "all";
  const r = await clearPerception();
  perceptionDeleting.value = null;
  if (!r.error) {
    perceptionItems.value = [];
    perceptionHasMore.value = false;
  } else {
    perceptionErr.value = r.error;
  }
}

// ---- 记忆管理（「它记得我什么」必须可见、可改、可删）----
const memItems = ref<MemItem[]>([]);
const memReady = ref(true);
const memFailed = ref(false);
const memLoaded = ref(false);
const memConfirming = ref<string | null>(null);
const memDeleting = ref<string | null>(null);
const memErr = ref("");
// 行内编辑
const memEditing = ref<string | null>(null);
const memDraft = ref("");
const memSaving = ref<string | null>(null);
// 命名空间筛选（null = 全部）与全文展开
const memFilter = ref<string | null>(null);
const memExpanded = ref(new Set<string>());

const memNamespaces = computed(() => {
  const map = new Map<string, { ns: string; label: string; count: number }>();
  for (const m of memItems.value) {
    const cur = map.get(m.ns);
    if (cur) cur.count += 1;
    else map.set(m.ns, { ns: m.ns, label: m.label, count: 1 });
  }
  return [...map.values()];
});

const memFiltered = computed(() =>
  memFilter.value === null ? memItems.value : memItems.value.filter((m) => m.ns === memFilter.value),
);

function fmtMemTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function loadMem() {
  memErr.value = "";
  const r = await getMemListOnce();
  memItems.value = r.items;
  memReady.value = r.ready;
  memFailed.value = r.failed;
  memLoaded.value = true;
}

async function doMemDelete(id: string) {
  memConfirming.value = null;
  memDeleting.value = id;
  memErr.value = "";
  const r = await memDelete(id);
  memDeleting.value = null;
  if (r.ok) memItems.value = memItems.value.filter((m) => m.id !== id);
  else memErr.value = r.error || "删除失败";
}

function startMemEdit(m: MemItem) {
  memConfirming.value = null;
  memEditing.value = m.id;
  memDraft.value = m.text;
  memErr.value = "";
}

function cancelMemEdit() {
  memEditing.value = null;
  memDraft.value = "";
}

async function doMemEdit(id: string) {
  const text = memDraft.value.trim();
  if (!text) return;
  memSaving.value = id;
  memErr.value = "";
  const r = await memEdit(id, text);
  memSaving.value = null;
  if (r.ok) {
    const it = memItems.value.find((m) => m.id === id);
    if (it) it.text = text;
    cancelMemEdit();
  } else {
    memErr.value = r.error || "保存失败";
  }
}

function toggleMemExpand(id: string) {
  const s = new Set(memExpanded.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  memExpanded.value = s;
}

// ---- 关于 ----
const version = ref("…");

let unlistenStatus: (() => void) | null = null;
let unlistenPerms: (() => void) | null = null;

onMounted(async () => {
  unlistenPerms = await onBrainPermissions((p) => {
    perms.value = p;
  });
  // 主动拉一次：大脑在 hello 时广播过权限，本窗可能后于 hello 挂载（大脑不在线则静默失败，等上线广播）
  void checkPermissions().catch(() => {});
  try {
    const cfg = await getSetupConfig();
    hasKey.value = cfg.has_key;
    model.value = cfg.model;
    baseUrl.value = cfg.base_url;
    voice.value = cfg.voice;
    voiceEnabled.value = cfg.voice_enabled;
  } catch { /* 用默认值 */ }
  try {
    autoStart.value = await isEnabled();
  } catch { /* dev 环境可能不可用 */ }
  try {
    version.value = await getVersion();
  } catch { /* 保留占位 */ }
  void loadMem(); // 记忆管理列表（异步，不阻塞设置页首屏）
  void getSettingsOnce().then((s) => { // 自主权旋钮当前值（大脑不在线则保持默认）
    if (s) {
      if (typeof s.proactive_voice === "boolean") proactiveVoice.value = s.proactive_voice;
      const lv = s["proactive.level"];
      if (lv === "quiet" || lv === "bubble" || lv === "full") proactiveLevel.value = lv;
      const tp = s["tts.provider"];
      if (tp === "edge" || tp === "cosyvoice" || tp === "cosyvoice_cloud") ttsProvider.value = tp;
      syncWatchSettings(s);
    }
  });
  void loadPerception();
  // 保存触发的重启：大脑上线事件收尾行内提示（掉线过程 UI 复用对话页既有事件）
  unlistenStatus = await onBrainStatus((m) => {
    if (m.status === "up" && saveMsg.value === "已保存，正在重启大脑…") {
      saveMsg.value = "✓ 大脑已重启，设置已生效";
    }
  });
});

onUnmounted(() => {
  unlistenStatus?.();
  unlistenPerms?.();
});
</script>

<template>
  <!-- 设置页：macOS 系统设置（Ventura+）语言——左侧分类目录 + 右侧分组卡片。
       原 11 个分组平铺一列滚不到底，现按语义收成 4 类；「感知日志」「记忆管理」
       这两个本质是数据浏览器的长列表，各自独占分类内的卡，不再挤在开关中间。 -->
  <div class="settings">
    <!-- 分类目录：与 Home 侧栏区分（这是二级导航，用文字列表不用图标底） -->
    <nav class="cat-nav">
      <div class="cat-safe" data-tauri-drag-region></div>
      <h1 class="cat-title" data-tauri-drag-region>设置</h1>
      <button
        v-for="c in CATS"
        :key="c.id"
        class="cat-item"
        :class="{ on: cat === c.id }"
        @click="cat = c.id"
      >
        <YbIcon class="cat-ic" :name="c.icon" :size="14" />
        <span>{{ c.label }}</span>
      </button>
    </nav>

    <div class="s-scroll">
      <!-- ============ 通用：模型 / 语音 / 启动 ============ -->
      <template v-if="cat === 'general'">
        <section class="s-group">
          <div class="s-group-title">模型</div>
          <label class="s-field">
            <span class="s-label">API Key</span>
            <input v-model="key" type="password" :placeholder="hasKey ? '已保存，输入以更换' : '未配置'" />
          </label>
          <label class="s-field">
            <span class="s-label">模型</span>
            <input v-model="model" placeholder="glm-4.6" />
          </label>
          <label class="s-field">
            <span class="s-label">Base URL</span>
            <input v-model="baseUrl" placeholder="留空 = 智谱官方端点" />
          </label>
        </section>

        <section class="s-group">
          <div class="s-group-title">语音</div>
          <label class="s-field">
            <span class="s-label">音色</span>
            <select v-model="voice">
              <option v-for="[v, label] in VOICES" :key="v" :value="v">{{ label }}</option>
            </select>
          </label>
          <label class="s-field">
            <span class="s-label">合成引擎<span class="s-row-why">切换后重启大脑生效</span></span>
            <select
              :value="ttsProvider"
              @change="setTtsProvider(($event.target as HTMLSelectElement).value as 'edge' | 'cosyvoice' | 'cosyvoice_cloud')"
            >
              <option value="edge">edge-tts（云端·快）</option>
              <option value="cosyvoice_cloud">CosyVoice 云（阿里·高质量）</option>
              <option value="cosyvoice">CosyVoice 本地（离线·可克隆）</option>
            </select>
          </label>
          <div v-if="ttsErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ ttsErr }}</div>
          <div class="s-row">
            <span class="s-row-label">语音播报与聆听</span>
            <button class="switch" :class="{ on: voiceEnabled }" role="switch" :aria-checked="voiceEnabled" title="语音总开关" @click="voiceEnabled = !voiceEnabled"><i /></button>
          </div>
        </section>

        <!-- 保存（模型+语音）：写 .env → 重启大脑生效。sticky 贴底，长表单里始终可达 -->
        <div class="s-actions">
          <span v-if="saveErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ saveErr }}</span>
          <span v-else-if="saveMsg" class="s-msg ok">{{ saveMsg }}</span>
          <button class="s-primary" :disabled="saving" @click="save">{{ saving ? "保存中…" : "保存并重启大脑" }}</button>
        </div>

        <section class="s-group">
          <div class="s-group-title">启动与快捷键</div>
          <div class="s-row">
            <span class="s-row-label">开机自动启动</span>
            <button class="switch" :class="{ on: autoStart }" role="switch" :aria-checked="autoStart" title="开机自动启动" @click="toggleAutostart"><i /></button>
          </div>
          <div v-if="autoStartErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ autoStartErr }}</div>
          <div class="s-row">
            <span class="s-row-label">全局快捷键</span>
            <span class="s-row-value">⌘⇧Y 显示 / 隐藏译宝</span>
          </div>
        </section>
      </template>

      <!-- ============ 主动协助：健康节律 / 屏幕建议 / 通知方式 ============ -->
      <template v-else-if="cat === 'proactive'">
        <section class="s-group">
          <div class="s-group-title">健康节律</div>
          <div class="s-note watch-status">{{ watchStatusText }} · 设置即时生效</div>
          <div class="s-row">
            <span class="s-row-label">
              健康节律
              <span class="s-row-why">仅读取活动 / 空闲状态；连续活跃达到阈值后提醒休息</span>
            </span>
            <button class="switch" :class="{ on: watchEnabled }" role="switch" :aria-checked="watchEnabled" title="健康节律" @click="toggleWatch"><i /></button>
          </div>
          <div v-if="watchEnabled">
            <label class="s-field">
              <span class="s-label">久坐提醒（连续活跃分钟）</span>
              <input type="number" min="5" step="5" :value="watchIdleWarn" @change="setWatchIdleWarn(+($event.target as HTMLInputElement).value)" />
            </label>
            <label class="s-field">
              <span class="s-label">静默时段<span class="s-row-why">HH:MM-HH:MM，跨午夜；留空=关</span></span>
              <input type="text" :value="watchQuietHours" placeholder="23:00-07:00" @change="setWatchQuietHours(($event.target as HTMLInputElement).value)" />
            </label>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">屏幕建议</div>
          <div class="s-row">
            <span class="s-row-label">
              屏幕建议
              <span class="s-row-why">只在允许的应用中截图判断是否值得提醒；截图前后都会核验当前 bundle id</span>
            </span>
            <button class="switch" :class="{ on: watchScreenEnabled }" role="switch" :aria-checked="watchScreenEnabled" title="屏幕建议" @click="toggleWatchScreen"><i /></button>
          </div>
          <div v-if="watchScreenEnabled" class="watch-disclosure">
            <div class="s-note">截图会发送给当前视觉模型服务，但只允许下列 bundle id；无法实时确认前台应用时不会截图或上传。</div>
            <label class="s-field">
              <span class="s-label">允许观察的 bundle id<span class="s-row-why">每行一个，例如 com.microsoft.VSCode</span></span>
              <textarea v-model="watchObserveApps" rows="3" placeholder="com.microsoft.VSCode" @blur="saveWatchScreenOptions" />
            </label>
            <details class="watch-advanced">
              <summary>频率与预算</summary>
              <label class="s-field"><span class="s-label">最小间隔（秒）</span><input v-model.number="watchLookGap" type="number" min="30" @change="saveWatchScreenOptions" /></label>
              <label class="s-field"><span class="s-label">每小时最多观察</span><input v-model.number="watchMaxHour" type="number" min="1" @change="saveWatchScreenOptions" /></label>
              <label class="s-field"><span class="s-label">每天最多观察</span><input v-model.number="watchMaxDay" type="number" min="1" @change="saveWatchScreenOptions" /></label>
            </details>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">通知方式</div>
          <div class="s-row">
            <span class="s-row-label">
              主动找我
              <span class="s-row-why">安静：提醒与播报只记入动态，不打扰；气泡：桌宠冒泡，不亮窗不出声；完整：亮窗 + 气泡</span>
            </span>
            <span class="seg" role="group" aria-label="主动找我频率">
              <button class="seg-btn" :class="{ on: proactiveLevel === 'quiet' }" :aria-pressed="proactiveLevel === 'quiet'" @click="setProactiveLevel('quiet')">安静</button>
              <button class="seg-btn" :class="{ on: proactiveLevel === 'bubble' }" :aria-pressed="proactiveLevel === 'bubble'" @click="setProactiveLevel('bubble')">气泡</button>
              <button class="seg-btn" :class="{ on: proactiveLevel === 'full' }" :aria-pressed="proactiveLevel === 'full'" @click="setProactiveLevel('full')">完整</button>
            </span>
          </div>
          <div class="s-row">
            <span class="s-row-label">
              主动开口播报
              <span class="s-row-why">{{ proactiveLevel === "full" ? "提醒触发时开口说话；关闭则只亮窗/气泡" : "仅「完整」档生效" }}</span>
            </span>
            <button
              class="switch"
              :class="{ on: proactiveVoice }"
              role="switch"
              :aria-checked="proactiveVoice"
              :disabled="proactiveLevel !== 'full'"
              title="主动开口播报"
              @click="toggleProactiveVoice"
            ><i /></button>
          </div>
          <div v-if="autonErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ autonErr }}</div>
          <div v-if="watchErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ watchErr }}</div>
        </section>
      </template>

      <!-- ============ 隐私：感知开关 / 感知日志 / 系统权限 ============ -->
      <template v-else-if="cat === 'privacy'">
        <section class="s-group">
          <div class="s-group-title">感知</div>
          <div class="s-note">全部默认关闭。观察内容加密存放在本机；只有开启下方模型读取开关并询问最近活动时，所选时间段才会发送给当前模型服务。</div>
          <div class="s-row">
            <span class="s-row-label">启用感知<span class="s-row-why">总开关，关闭后立即停止采样</span></span>
            <button class="switch" :class="{ on: perceptionMaster }" role="switch" :aria-checked="perceptionMaster" title="启用感知" @click="setPerceptionSetting('perception.master', !perceptionMaster)"><i /></button>
          </div>
          <div class="s-row">
            <span class="s-row-label">应用与窗口<span class="s-row-why">只在切换时记录应用名和窗口标题</span></span>
            <button class="switch" :class="{ on: perceptionApp }" role="switch" :aria-checked="perceptionApp" :disabled="!perceptionMaster" title="应用与窗口" @click="setPerceptionSetting('perception.app', !perceptionApp)"><i /></button>
          </div>
          <div class="s-row">
            <span class="s-row-label">活动与空闲<span class="s-row-why">只记录状态切换，不读取输入内容</span></span>
            <button class="switch" :class="{ on: perceptionActivity }" role="switch" :aria-checked="perceptionActivity" :disabled="!perceptionMaster" title="活动与空闲" @click="setPerceptionSetting('perception.activity', !perceptionActivity)"><i /></button>
          </div>
          <div class="s-row">
            <span class="s-row-label">允许模型读取感知记录<span class="s-row-why">询问最近活动时，将所选时间段的应用名、窗口标题和活动状态发送给当前模型；不发送截图或按键内容</span></span>
            <button class="switch" :class="{ on: perceptionModelAccess }" role="switch" :aria-checked="perceptionModelAccess" title="允许模型读取感知记录" @click="setPerceptionSetting('perception.model_access', !perceptionModelAccess)"><i /></button>
          </div>
          <div class="s-note">{{ perceptionMaster ? "运行中" : "已暂停" }} · {{ perceptionItems.length }} 条已加载观察</div>
          <div v-if="perceptionErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ perceptionErr }}</div>
        </section>

        <!-- 感知日志：让用户看到、逐条删、全部清空 -->
        <section class="s-group">
          <div class="s-group-title">感知日志<template v-if="perceptionItems.length"> · {{ perceptionItems.length }}</template></div>
          <div v-if="perceptionLoaded && !perceptionAvailable" class="s-note">感知存储不可用；总开关会保持关闭。</div>
          <div v-else-if="perceptionLoaded && !perceptionItems.length" class="s-note">
            {{ perceptionMaster ? "还没有观察——切换一次应用或等待进入空闲。" : "感知未开启；已有记录仍可在开启前审阅和删除。" }}
          </div>
          <!-- 长列表：限高自滚，避免把下方权限卡推到视野外 -->
          <div v-if="perceptionItems.length" class="log-scroll">
            <div v-for="item in perceptionItems" :key="item.id" class="p-row">
              <span class="m-ns">{{ perceptionSource(item) }}</span>
              <span class="p-main">
                <span class="p-text">{{ perceptionText(item) }}</span>
                <span class="p-meta">{{ relativeTime(item.ts) }} · {{ item.sensitivity }}</span>
              </span>
              <span class="s-row-btns">
                <template v-if="perceptionConfirming === item.id">
                  <button class="s-mini danger" :disabled="perceptionDeleting === item.id" @click="doPerceptionDelete(item.id)">确认</button>
                  <button class="s-mini" @click="perceptionConfirming = null">取消</button>
                </template>
                <button v-else class="s-mini" :disabled="perceptionDeleting === item.id" @click="perceptionConfirming = item.id">删除</button>
              </span>
            </div>
          </div>
          <div class="p-actions">
            <button class="s-mini" :disabled="perceptionLoading" @click="loadPerception(false)">{{ perceptionLoading ? "读取中…" : "刷新" }}</button>
            <button v-if="perceptionHasMore" class="s-mini" :disabled="perceptionLoading" @click="loadPerception(true)">加载更早</button>
            <span class="p-spacer" />
            <template v-if="perceptionConfirming === 'all'">
              <button class="s-mini danger" :disabled="perceptionDeleting === 'all'" @click="doPerceptionClear">确认清空</button>
              <button class="s-mini" @click="perceptionConfirming = null">取消</button>
            </template>
            <button v-else class="s-mini" :disabled="!perceptionItems.length || perceptionDeleting === 'all'" @click="perceptionConfirming = 'all'">清空…</button>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">系统权限</div>
          <div class="s-row">
            <span class="s-row-label">
              <i class="perm-dot" :class="perms ? (perms.ax ? 'on' : 'off') : 'unknown'" />
              辅助功能
              <span class="s-row-why">读取控件、模拟键鼠</span>
            </span>
            <button v-if="perms && !perms.ax" class="s-mini accent" @click="grant('ax')">去授权</button>
          </div>
          <div class="s-row">
            <span class="s-row-label">
              <i class="perm-dot" :class="perms ? (perms.screen ? 'on' : 'off') : 'unknown'" />
              屏幕录制
              <span class="s-row-why">截图感知屏幕内容</span>
            </span>
            <button v-if="perms && !perms.screen" class="s-mini accent" @click="grant('screen')">去授权</button>
          </div>
          <div class="s-row">
            <span class="s-row-label">
              <i class="perm-dot" :class="perms ? (perms.input ? 'on' : 'off') : 'unknown'" />
              输入监控
              <span class="s-row-why">用户键鼠优先，AI 自动让出控制</span>
            </span>
            <button v-if="perms && !perms.input" class="s-mini accent" @click="grant('input')">去授权</button>
          </div>
          <div class="s-row">
            <span class="s-row-why">{{ perms ? "授权后点重新检测；屏幕录制需重启译宝生效" : "大脑连接后自动检测" }}</span>
            <button class="s-mini" @click="recheck">重新检测</button>
          </div>
        </section>
      </template>

      <!-- ============ 数据：记忆管理 / 清空 / 数据目录 / 关于 ============ -->
      <template v-else>
        <!-- 记忆管理（「它记得我什么」：按命名空间列出，可单条删除；彻底清空走下方数据区） -->
        <section class="s-group">
          <div class="s-group-title">记忆管理<template v-if="memLoaded && memItems.length"> · {{ memItems.length }}</template></div>
          <div v-if="memFailed" class="s-note">长期记忆不可用（本次运行记不住事）——检查模型配置或重启译宝。</div>
          <div v-else-if="!memReady" class="s-note">
            记忆接入中… <button class="s-mini" @click="loadMem">刷新</button>
          </div>
          <template v-else>
            <div v-if="memLoaded && !memItems.length" class="s-note">还没有记住什么——跟译宝聊聊你的偏好和习惯。</div>
            <div v-if="memNamespaces.length > 1" class="m-chips">
              <button class="m-chip" :class="{ on: memFilter === null }" @click="memFilter = null">
                全部 · {{ memItems.length }}
              </button>
              <button
                v-for="n in memNamespaces"
                :key="n.ns"
                class="m-chip"
                :class="{ on: memFilter === n.ns }"
                @click="memFilter = n.ns"
              >{{ n.label }} · {{ n.count }}</button>
            </div>
            <!-- 长列表：限高自滚（与感知日志同策略） -->
            <div v-if="memFiltered.length" class="log-scroll">
              <div v-for="m in memFiltered" :key="m.id" class="m-row">
                <span class="m-ns">{{ m.label }}</span>
                <template v-if="memEditing === m.id">
                  <textarea v-model="memDraft" class="m-edit" rows="2" :disabled="memSaving === m.id" />
                  <span class="s-row-btns">
                    <button class="s-mini" :disabled="memSaving === m.id || !memDraft.trim()" @click="doMemEdit(m.id)">
                      {{ memSaving === m.id ? "保存中…" : "保存" }}
                    </button>
                    <button class="s-mini" :disabled="memSaving === m.id" @click="cancelMemEdit">取消</button>
                  </span>
                </template>
                <template v-else>
                  <span
                    class="m-text"
                    :class="{ open: memExpanded.has(m.id) }"
                    :title="memExpanded.has(m.id) ? '点击收起' : '点击展开全文'"
                    @click="toggleMemExpand(m.id)"
                  >{{ m.text }}</span>
                  <span v-if="m.created_at" class="m-time">{{ fmtMemTime(m.created_at) }}</span>
                  <span class="s-row-btns">
                    <button class="s-mini" @click="startMemEdit(m)">编辑</button>
                    <template v-if="memConfirming === m.id">
                      <button class="s-mini danger" :disabled="memDeleting === m.id" @click="doMemDelete(m.id)">
                        {{ memDeleting === m.id ? "删除中…" : "确认" }}
                      </button>
                      <button class="s-mini" :disabled="memDeleting === m.id" @click="memConfirming = null">取消</button>
                    </template>
                    <button v-else class="s-mini" @click="memConfirming = m.id">删除</button>
                  </span>
                </template>
              </div>
            </div>
          </template>
          <div v-if="memErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ memErr }}</div>
        </section>

        <section class="s-group">
          <div class="s-group-title">清空与文件</div>
          <div class="s-row">
            <span class="s-row-label">长期记忆<span class="s-row-why">记住的偏好与事实</span></span>
            <span class="s-row-btns">
              <template v-if="confirming === 'memory'">
                <button class="s-mini danger" :disabled="clearing === 'memory'" @click="doClear('memory')">
                  {{ clearing === "memory" ? "清空中…" : "确认清空" }}
                </button>
                <button class="s-mini" :disabled="clearing === 'memory'" @click="confirming = null">取消</button>
              </template>
              <button v-else class="s-mini" @click="confirming = 'memory'">清空…</button>
            </span>
          </div>
          <div class="s-row">
            <span class="s-row-label">对话历史<span class="s-row-why">跨会话的聊天记录</span></span>
            <span class="s-row-btns">
              <template v-if="confirming === 'history'">
                <button class="s-mini danger" :disabled="clearing === 'history'" @click="doClear('history')">
                  {{ clearing === "history" ? "清空中…" : "确认清空" }}
                </button>
                <button class="s-mini" :disabled="clearing === 'history'" @click="confirming = null">取消</button>
              </template>
              <button v-else class="s-mini" @click="confirming = 'history'">清空…</button>
            </span>
          </div>
          <div v-if="clearMsg" class="s-msg" :class="clearErr ? 'err' : 'ok'"><YbIcon v-if="clearErr" name="alert" :size="13" />{{ clearMsg }}</div>
          <div class="s-note">清空会先停大脑再拉起，过程中译宝短暂离线几秒。</div>
          <div class="s-row">
            <span class="s-row-label">数据目录<span class="s-row-why">配置 / 记忆 / 历史文件</span></span>
            <button class="s-mini" @click="openDataDir">在 Finder 打开</button>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">关于</div>
          <div class="s-row">
            <span class="s-row-label">译宝</span>
            <span class="s-row-value">v{{ version }}</span>
          </div>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 设置页：macOS 系统设置（Ventura+）语言——左侧分类目录 + 右侧分组卡片。
   页面底用浅灰（--yb-card-page-bg）反衬白卡，这是系统设置的标志性层次。 */
.settings {
  flex: 1;
  min-height: 0;
  display: flex;
  background: var(--yb-card-page-bg);
}

/* ---- 分类目录（二级导航）---- */
.cat-nav {
  width: 168px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 0 var(--yb-space-2) var(--yb-space-3);
  border-right: 1px solid var(--yb-border-base);
  background: var(--yb-sidebar-bg);
}
/* 大窗侧栏只覆盖左边 200px，这里仍在标题栏下方，需自己留安全区 */
.cat-safe {
  height: var(--yb-titlebar-h);
  flex-shrink: 0;
}
.cat-title {
  margin: 0 0 var(--yb-space-3);
  padding: 0 var(--yb-space-2);
  font-size: var(--yb-fs-xl);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-strong);
  user-select: none;
}
.cat-item {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 6px var(--yb-space-2);
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.cat-ic {
  flex-shrink: 0;
  color: var(--yb-text-dim);
}
.cat-item:hover {
  background: var(--yb-sidebar-sel);
}
.cat-item.on {
  background: var(--yb-sidebar-sel-active);
  color: var(--yb-text-on-accent);
}
.cat-item.on .cat-ic {
  color: var(--yb-text-on-accent);
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.s-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-4);
  padding: var(--yb-titlebar-h) var(--yb-space-5) var(--yb-space-5);
  scrollbar-width: thin;
  animation: fade-in 0.2s var(--yb-ease-out) both;
}
.s-scroll::-webkit-scrollbar {
  width: 7px;
}
.s-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
/* 分组卡：系统设置语言——实白 + hairline + 小圆角。
   左对齐而非居中：内容区已被分类目录挤窄，再居中会整体偏右。 */
.s-group {
  box-sizing: border-box;
  width: 100%;
  max-width: 620px;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding: var(--yb-space-4);
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
}
/* 分组标题：卡内首行，与内容用 hairline 分隔（系统设置的 section header） */
.s-group-title {
  margin: calc(var(--yb-space-1) * -1) 0 0;
  padding-bottom: var(--yb-space-2);
  border-bottom: 1px solid var(--yb-card-row-line);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
}
/* 字段（模型/语音）：对齐首启向导的输入样式 */
.s-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.s-label {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
input,
select,
textarea {
  padding: 6px 10px;
  border-radius: var(--yb-radius-xs);
  border: 1px solid var(--yb-border-strong);
  background: var(--yb-card-bg);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  outline: none;
  transition: border-color var(--yb-dur-fast) var(--yb-ease-out), box-shadow var(--yb-dur-fast) var(--yb-ease-out);
}
/* macOS focus ring：accent 描边 + 外发光环（--yb-focus-ring 是完整 box-shadow 值） */
input:focus,
select:focus,
textarea:focus {
  border-color: var(--yb-accent);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.22);
}
.sub-title {
  margin-top: var(--yb-space-2);
  padding-top: var(--yb-space-2);
  border-top: 1px solid var(--yb-card-row-line);
  border-bottom: none;
}
.watch-status {
  color: var(--yb-accent-deep);
}
.watch-disclosure {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-page-bg);
}
.watch-disclosure textarea {
  resize: vertical;
}
.watch-advanced summary {
  cursor: pointer;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  margin-bottom: var(--yb-space-2);
}
/* 长列表（感知日志 / 记忆管理）：限高自滚，不把后面的卡推出视野 */
.log-scroll {
  max-height: 280px;
  overflow-y: auto;
  margin: 0 calc(var(--yb-space-2) * -1);
  padding: 0 var(--yb-space-2);
  scrollbar-width: thin;
}
.log-scroll::-webkit-scrollbar {
  width: 6px;
}
.log-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
/* 设置行：左标签右控件（系统设置的核心行式） */
.s-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-3);
  min-height: 26px;
}
.s-row-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: var(--yb-fs-lg);
  color: var(--yb-text);
}
.s-row-why {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.s-row-value {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.s-row-btns {
  display: inline-flex;
  gap: 6px;
}
.s-note {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: 1.4;
}
/* 记忆行：命名空间徽章 + 文本（两行截断）+ 删除 */
.m-row {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  padding: 7px 0;
  border-top: 1px solid var(--yb-card-row-line);
}
.m-ns {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  line-height: var(--yb-lh-base);
}
.m-text {
  flex: 1;
  min-width: 0;
  font-size: var(--yb-fs-md);
  color: var(--yb-text);
  line-height: var(--yb-lh-ui);
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  cursor: pointer;
}
.m-text.open {
  display: block;
  -webkit-line-clamp: unset;
  overflow: visible;
}
.m-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  opacity: 0.55;
  white-space: nowrap;
}
/* 记忆筛选 chips + 行内编辑框 */
.m-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 0 8px;
}
.m-chip {
  border: 1px solid var(--yb-border-strong);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  border-radius: var(--yb-radius-pill);
  padding: 2px 10px;
  font-size: var(--yb-fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.m-chip.on {
  background: var(--yb-accent-soft);
  border-color: var(--yb-accent);
  color: var(--yb-accent-deep);
}
.m-edit {
  flex: 1;
  min-width: 0;
  resize: vertical;
  font-family: inherit;
  font-size: var(--yb-fs-md);
  line-height: var(--yb-lh-ui);
  padding: 6px 8px;
  border: 1px solid var(--yb-accent);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-bg);
  color: var(--yb-text);
}
/* 权限状态点：绿=已授权 红=缺 灰=未检测到 */
.perm-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: var(--yb-state-idle);
}
.perm-dot.on {
  background: var(--yb-state-success);
}
.perm-dot.off {
  background: var(--yb-state-error);
}
/* iOS 风开关：天青 accent 着色 */
.switch {
  position: relative;
  width: 36px;
  height: 22px;
  flex-shrink: 0;
  border: none;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral-hover);
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
  padding: 0;
}
.switch i {
  position: absolute;
  left: 2px;
  top: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: var(--yb-shadow-1);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.switch.on {
  background: var(--yb-accent);
}
.switch.on i {
  transform: translateX(14px);
}
.switch:disabled {
  opacity: 0.45;
  cursor: default;
}
/* 三档 segmented（自主权旋钮）：macOS Segmented Control——凹槽 + 白滑块 */
.seg {
  display: inline-flex;
  flex-shrink: 0;
  gap: 2px;
  padding: 2px;
  border-radius: var(--yb-radius-xs);
  background: var(--yb-segment-track);
}
.seg-btn {
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  padding: 3px 12px;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.seg-btn:hover {
  color: var(--yb-text);
}
.seg-btn.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-text);
  font-weight: var(--yb-fw-medium);
  box-shadow: var(--yb-shadow-1);
}
/* 感知日志：来源徽章 + 一行正文/元信息 + 原地删除。 */
.p-row {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  padding: 7px 0;
  border-top: 1px solid var(--yb-card-row-line);
}
.p-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.p-text {
  overflow: hidden;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  line-height: 1.45;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.p-meta {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.p-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}
.p-spacer {
  flex: 1;
}
/* 行内小按钮 */
.s-mini {
  padding: 3px 11px;
  border: 1px solid var(--yb-border-strong);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  font-weight: var(--yb-fw-medium);
  white-space: nowrap;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.s-mini:hover:not(:disabled) {
  background: var(--yb-btn-neutral);
  color: var(--yb-text);
}
.s-mini.accent {
  border-color: var(--yb-accent);
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.s-mini.accent:hover:not(:disabled) {
  background: var(--yb-accent-deep);
  color: var(--yb-text-on-accent);
}
.s-mini.danger {
  border-color: var(--yb-danger);
  background: var(--yb-danger);
  color: #fff;
}
.s-mini.danger:hover:not(:disabled) {
  background: var(--yb-danger);
  color: #fff;
  filter: brightness(0.94);
}
.s-mini:disabled {
  opacity: 0.5;
  cursor: default;
}
/* 保存行：消息在左、主按钮在右；与分组卡同宽左对齐 */
.s-actions {
  box-sizing: border-box;
  width: 100%;
  max-width: 620px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--yb-space-3);
  padding: 0 2px;
}
.s-msg {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  font-size: var(--yb-fs-md);
  line-height: var(--yb-lh-ui);
}
.s-msg.ok {
  color: var(--yb-intent-ok);
}
.s-msg.err {
  color: var(--yb-danger);
}
.s-primary {
  flex-shrink: 0;
  padding: 6px 18px;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  font-weight: var(--yb-fw-medium);
  cursor: pointer;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.s-primary:hover:not(:disabled) {
  background: var(--yb-accent-deep);
}
.s-primary:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>
