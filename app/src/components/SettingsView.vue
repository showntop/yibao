<script setup lang="ts">
// 设置页（home 大窗唯一内容）：模型/语音（保存后重启大脑生效）+ 通用/权限/数据（即时生效）。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
import { onMounted, onUnmounted, ref } from "vue";
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
  getSettingsOnce,
  setSettings,
  type BrainPermissions,
  type ClearKind,
  type MemItem,
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

// ---- 记忆管理（「它记得我什么」必须可见、可删）----
const memItems = ref<MemItem[]>([]);
const memReady = ref(true);
const memFailed = ref(false);
const memLoaded = ref(false);
const memConfirming = ref<string | null>(null);
const memDeleting = ref<string | null>(null);
const memErr = ref("");

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
    if (s && typeof s.proactive_voice === "boolean") proactiveVoice.value = s.proactive_voice;
  });
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
          <div v-for="m in memItems" :key="m.id" class="m-row">
            <span class="m-ns">{{ m.label }}</span>
            <span class="m-text">{{ m.text }}</span>
            <span class="s-row-btns">
              <template v-if="memConfirming === m.id">
                <button class="s-mini danger" :disabled="memDeleting === m.id" @click="doMemDelete(m.id)">
                  {{ memDeleting === m.id ? "删除中…" : "确认" }}
                </button>
                <button class="s-mini" :disabled="memDeleting === m.id" @click="memConfirming = null">取消</button>
              </template>
              <button v-else class="s-mini" @click="memConfirming = m.id">删除</button>
            </span>
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
