<script setup lang="ts">
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
  type BrainPermissions,
  type ClearKind,
} from "../lib/brain";

// 设置页：模型/语音（保存后重启大脑生效）+ 通用/权限/数据（即时生效）。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
defineProps<{ perms: BrainPermissions | null }>();
const emit = defineEmits<{ back: [] }>();

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

// ---- 关于 ----
const version = ref("…");

let unlistenStatus: (() => void) | null = null;

onMounted(async () => {
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
  // 保存触发的重启：大脑上线事件收尾行内提示（掉线过程 UI 复用对话页既有事件）
  unlistenStatus = await onBrainStatus((m) => {
    if (m.status === "up" && saveMsg.value === "已保存，正在重启大脑…") {
      saveMsg.value = "✓ 大脑已重启，设置已生效";
    }
  });
});

onUnmounted(() => {
  unlistenStatus?.();
});
</script>

<template>
  <div class="settings">
    <div class="s-head">
      <button class="s-back" @click="emit('back')">‹ 返回</button>
      <span class="s-title">设置</span>
    </div>

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
/* 设置页：占满 chat-body（与插件启动器视图同挂法），内部自滚动 */
.settings {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  animation: fade-in 0.22s var(--yb-ease) 0.06s both;
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
/* 头部：返回在左、标题居中 */
.s-head {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2px 4px;
}
.s-title {
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.s-back {
  position: absolute;
  left: 0;
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: 13px;
  cursor: pointer;
  padding: 3px 8px;
  border-radius: 10px;
  transition: all 0.15s ease;
}
.s-back:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-surface-solid);
}
.s-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
  padding: 2px 2px 4px;
  scrollbar-width: thin;
}
.s-scroll::-webkit-scrollbar {
  width: 6px;
}
.s-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-surface-border);
  border-radius: 3px;
}
/* 分组卡：与插件行/向导同款白卡 */
.s-group {
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
/* 保存行：消息在左（弹性撑开）、主按钮在右 */
.s-actions {
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
