<script setup lang="ts">
// 设置页（home 大窗唯一内容）：模型/语音（保存后重启大脑生效）+ 通用/权限/数据（即时生效）。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import { openUrl } from "@tauri-apps/plugin-opener";
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
const SETTINGS_URLS: Record<"ax" | "screen", string> = {
  ax: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
  screen: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
};

function grant(which: "ax" | "screen") {
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

async function doClear(kind: ClearKind) {
  confirming.value = null;
  clearing.value = kind;
  clearMsg.value = "";
  try {
    await clearBrainData(kind);
    clearMsg.value = kind === "memory" ? "✓ 长期记忆已清空" : "✓ 对话历史已清空";
  } catch (e) {
    clearMsg.value = "⚠️ " + String(e);
  } finally {
    clearing.value = null;
  }
}

// ---- 自主权（数据目录 settings.json，即时生效免重启）----
const proactiveVoice = ref(true);
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
      syncPerceptionSettings(s);
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
  <div class="settings">
    <header class="page-head" data-tauri-drag-region>
      <span class="pg-title" data-tauri-drag-region>设置</span>
    </header>
    <div class="s-scroll">
      <!-- 模型 -->
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

      <!-- 语音 -->
      <section class="s-group">
        <div class="s-group-title">语音</div>
        <label class="s-field">
          <span class="s-label">音色</span>
          <select v-model="voice">
            <option v-for="[v, label] in VOICES" :key="v" :value="v">{{ label }}</option>
          </select>
        </label>
        <div class="s-row">
          <span class="s-row-label">语音播报与聆听</span>
          <button class="switch" :class="{ on: voiceEnabled }" title="语音总开关" @click="voiceEnabled = !voiceEnabled"><i /></button>
        </div>
      </section>

      <!-- 保存（模型+语音）：写 .env → 重启大脑生效 -->
      <div class="s-actions">
        <span v-if="saveErr" class="s-msg err">⚠️ {{ saveErr }}</span>
        <span v-else-if="saveMsg" class="s-msg ok">{{ saveMsg }}</span>
        <button class="s-primary" :disabled="saving" @click="save">{{ saving ? "保存中…" : "保存并重启大脑" }}</button>
      </div>

      <!-- 通用 -->
      <section class="s-group">
        <div class="s-group-title">通用</div>
        <div class="s-row">
          <span class="s-row-label">开机自动启动</span>
          <button class="switch" :class="{ on: autoStart }" title="开机自动启动" @click="toggleAutostart"><i /></button>
        </div>
        <div v-if="autoStartErr" class="s-msg err">⚠️ {{ autoStartErr }}</div>
        <div class="s-row">
          <span class="s-row-label">全局快捷键</span>
          <span class="s-row-value">⌘⇧Y 显示 / 隐藏译宝</span>
        </div>
      </section>

      <!-- 自主权（settings.json，即时生效免重启） -->
      <section class="s-group">
        <div class="s-group-title">自主权</div>
        <div class="s-row">
          <span class="s-row-label">主动开口播报<span class="s-row-why">提醒触发时开口说话；关闭则只亮窗/气泡</span></span>
          <button class="switch" :class="{ on: proactiveVoice }" title="主动开口播报" @click="toggleProactiveVoice"><i /></button>
        </div>
        <div v-if="autonErr" class="s-msg err">⚠️ {{ autonErr }}</div>
      </section>

      <!-- 感知：显式 opt-in；A/C 只收状态，不截屏、不读输入内容 -->
      <section class="s-group">
        <div class="s-group-title">感知</div>
        <div class="s-note">全部默认关闭。观察内容加密存放在本机；只有开启下方模型读取开关并询问最近活动时，所选时间段才会发送给当前模型服务。</div>
        <div class="s-row">
          <span class="s-row-label">启用感知<span class="s-row-why">总开关，关闭后立即停止采样</span></span>
          <button class="switch" :class="{ on: perceptionMaster }" title="启用感知" @click="setPerceptionSetting('perception.master', !perceptionMaster)"><i /></button>
        </div>
        <div class="s-row">
          <span class="s-row-label">应用与窗口<span class="s-row-why">只在切换时记录应用名和窗口标题</span></span>
          <button class="switch" :class="{ on: perceptionApp }" :disabled="!perceptionMaster" title="应用与窗口" @click="setPerceptionSetting('perception.app', !perceptionApp)"><i /></button>
        </div>
        <div class="s-row">
          <span class="s-row-label">活动与空闲<span class="s-row-why">只记录状态切换，不读取输入内容</span></span>
          <button class="switch" :class="{ on: perceptionActivity }" :disabled="!perceptionMaster" title="活动与空闲" @click="setPerceptionSetting('perception.activity', !perceptionActivity)"><i /></button>
        </div>
        <div class="s-row">
          <span class="s-row-label">允许模型读取感知记录<span class="s-row-why">询问最近活动时，将所选时间段的应用名、窗口标题和活动状态发送给当前模型；不发送截图或按键内容</span></span>
          <button class="switch" :class="{ on: perceptionModelAccess }" title="允许模型读取感知记录" @click="setPerceptionSetting('perception.model_access', !perceptionModelAccess)"><i /></button>
        </div>
        <div class="s-note">{{ perceptionMaster ? "运行中" : "已暂停" }} · {{ perceptionItems.length }} 条已加载观察</div>
        <div v-if="perceptionErr" class="s-msg err">⚠️ {{ perceptionErr }}</div>
      </section>

      <!-- 感知日志：让用户看到、逐条删、全部清空 -->
      <section class="s-group">
        <div class="s-group-title">感知日志<template v-if="perceptionItems.length"> · {{ perceptionItems.length }}</template></div>
        <div v-if="perceptionLoaded && !perceptionAvailable" class="s-note">感知存储不可用；总开关会保持关闭。</div>
        <div v-else-if="perceptionLoaded && !perceptionItems.length" class="s-note">
          {{ perceptionMaster ? "还没有观察——切换一次应用或等待进入空闲。" : "感知未开启；已有记录仍可在开启前审阅和删除。" }}
        </div>
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

      <!-- 权限 -->
      <section class="s-group">
        <div class="s-group-title">权限</div>
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
          <span class="s-row-why">{{ perms ? "授权后点重新检测；屏幕录制需重启译宝生效" : "大脑连接后自动检测" }}</span>
          <button class="s-mini" @click="recheck">重新检测</button>
        </div>
      </section>

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
        </template>
        <div v-if="memErr" class="s-msg err">⚠️ {{ memErr }}</div>
      </section>

      <!-- 数据 -->
      <section class="s-group">
        <div class="s-group-title">数据</div>
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
        <div v-if="clearMsg" class="s-msg" :class="clearMsg.startsWith('⚠️') ? 'err' : 'ok'">{{ clearMsg }}</div>
        <div class="s-note">清空会先停大脑再拉起，过程中译宝短暂离线几秒。</div>
        <div class="s-row">
          <span class="s-row-label">数据目录<span class="s-row-why">配置 / 记忆 / 历史文件</span></span>
          <button class="s-mini" @click="openDataDir">在 Finder 打开</button>
        </div>
      </section>

      <!-- 关于 -->
      <section class="s-group">
        <div class="s-group-title">关于</div>
        <div class="s-row">
          <span class="s-row-label">译宝</span>
          <span class="s-row-value">v{{ version }}</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
/* 设置页：撑满 home 大窗内容区，内部自滚动；分组卡居中单列（max-width 560，宽窗不拉成长行） */
.settings {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  animation: fade-in 0.22s var(--yb-ease) 0.06s both;
}
/* 页头：与对话/插件页同款（标题 + 整条兼作拖动区） */
.page-head {
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  padding: var(--yb-space-3) var(--yb-space-4) 0;
  user-select: none;
}
.pg-title {
  font-size: var(--yb-fs-xl);
  font-weight: 650;
  letter-spacing: 0.01em;
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
  gap: var(--yb-space-3);
  padding: var(--yb-space-3) var(--yb-space-4) var(--yb-space-4);
  scrollbar-width: thin;
}
.s-scroll::-webkit-scrollbar {
  width: 6px;
}
.s-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: 3px;
}
/* 分组卡：与插件行/向导同款白卡；居中单列（宽窗下内容不拉成长行） */
.s-group {
  box-sizing: border-box;
  width: 100%;
  max-width: 560px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  border: 1px solid var(--yb-surface-border);
  border-radius: 14px;
  background: var(--yb-surface-solid);
  box-shadow: var(--yb-shadow-soft);
}
.s-group-title {
  font-size: var(--yb-fs-md);
  font-weight: 600;
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
select {
  padding: 7px 10px;
  border-radius: var(--yb-radius-sm);
  border: 1px solid var(--yb-surface-border);
  background: var(--yb-bg);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  outline: none;
}
input:focus,
select:focus {
  border-color: var(--yb-accent);
}
/* 设置行：左标签右控件 */
.s-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  min-height: 24px;
}
.s-row-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
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
  padding: 6px 0;
  border-top: 1px solid var(--yb-surface-border);
}
.m-ns {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  line-height: 1.6;
}
.m-text {
  flex: 1;
  min-width: 0;
  font-size: var(--yb-fs-md);
  color: var(--yb-text);
  line-height: 1.5;
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
/* 记忆筛选 chips + 行内编辑框 */
.m-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 2px 0 8px;
}
.m-chip {
  border: 1px solid var(--yb-surface-border);
  background: transparent;
  color: var(--yb-text-dim);
  border-radius: var(--yb-radius-lg);
  padding: 2px 10px;
  font-size: var(--yb-fs-sm);
  cursor: pointer;
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
  line-height: 1.5;
  padding: 6px 8px;
  border: 1px solid var(--yb-accent);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface);
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
  border-radius: 999px;
  background: var(--yb-btn-neutral);
  cursor: pointer;
  transition: background 0.15s ease;
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
  box-shadow: 0 1px 3px rgba(40, 60, 90, 0.25);
  transition: transform 0.15s ease;
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
/* 感知日志：来源徽章 + 一行正文/元信息 + 原地删除。 */
.p-row {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  padding: 7px 0;
  border-top: 1px solid var(--yb-surface-border);
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
  padding: 4px 12px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.15s ease;
}
.s-mini:hover:not(:disabled) {
  filter: brightness(0.96);
  color: var(--yb-text);
}
.s-mini.accent {
  background: var(--yb-accent);
  color: #fff;
}
.s-mini.danger {
  background: var(--yb-danger);
  color: #fff;
}
.s-mini:disabled {
  opacity: 0.6;
  cursor: default;
}
/* 保存行：消息在左（弹性撑开）、主按钮在右；与分组卡同宽居中 */
.s-actions {
  box-sizing: border-box;
  width: 100%;
  max-width: 560px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: 0 2px;
}
.s-msg {
  font-size: var(--yb-fs-md);
  line-height: 1.4;
}
.s-msg.ok {
  color: var(--yb-state-success);
}
.s-msg.err {
  color: var(--yb-danger);
}
.s-primary {
  flex-shrink: 0;
  padding: 7px 18px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-accent);
  color: #fff;
  font-size: var(--yb-fs-md);
  font-weight: 600;
  cursor: pointer;
  transition: filter 0.15s;
}
.s-primary:hover:not(:disabled) {
  filter: brightness(0.96);
}
.s-primary:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
