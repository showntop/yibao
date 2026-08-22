<script setup lang="ts">
// 设置页「隐私与权限」分类（自包含）：感知开关（含行内两段确认）/ 系统权限。
import { onMounted, onUnmounted, ref } from "vue";
import { openUrl } from "@tauri-apps/plugin-opener";
import YbIcon from "../../components/common/YbIcon.vue";
import {
  checkPermissions,
  promptPermission,
  revealAppInFinder,
  onBrainPermissions,
  getSettingsOnce,
  setSettings,
  distillNow,
  type BrainPermissions,
  type SettingsValues,
} from "../../lib/brain";

// ---- 系统权限（复用引导横幅的检测/授权链路，视觉收敛为设置行）----
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

function revealInFinder() {
  // 兜底：在 Finder 亮出授权目标文件，可直接拖进系统设置授权列表
  void revealAppInFinder().catch(() => {});
}

// ---- 感知（默认关闭；settings 即时生效；日志内容由 sidecar 临时解密给 UI）----
const perceptionMaster = ref(false);
const perceptionApp = ref(false);
const perceptionActivity = ref(false);
const perceptionModelAccess = ref(false);
const perceptionScreen = ref(false);
// 感知开关行内错误提示（如感知存储不可用；与日志页的 perceptionErr 无关）
const perceptionErr = ref("");
// screen 开关行内两段确认：开启涉及持续观察与截图外发，须先看清说明再确认
const screenConfirming = ref(false);
// distill 开关同样两段确认：每日提炼会把昨日感知内容外发给当前模型
const perceptionDistill = ref(false);
const distillConfirming = ref(false);
const distillRunning = ref(false);
const distillResult = ref("");
// recap 开关同样两段确认：晨间反刍把昨日提炼的洞察与建议主动端给用户
const perceptionRecap = ref(false);
const recapConfirming = ref(false);

function syncPerceptionSettings(s: SettingsValues) {
  perceptionMaster.value = s["perception.master"] === true;
  perceptionApp.value = s["perception.app"] === true;
  perceptionActivity.value = s["perception.activity"] === true;
  perceptionModelAccess.value = s["perception.model_access"] === true;
  perceptionScreen.value = s["perception.screen"] === true;
  perceptionDistill.value = s["perception.distill"] === true;
  perceptionRecap.value = s["perception.recap"] === true;
}

async function setPerceptionSetting(
  key:
    | "perception.master"
    | "perception.app"
    | "perception.activity"
    | "perception.model_access"
    | "perception.screen"
    | "perception.distill"
    | "perception.recap",
  next: boolean,
) {
  perceptionErr.value = "";
  if (key === "perception.master" && !next) {
    screenConfirming.value = false;
    distillConfirming.value = false;
    recapConfirming.value = false;
  }
  const old = {
    "perception.master": perceptionMaster.value,
    "perception.app": perceptionApp.value,
    "perception.activity": perceptionActivity.value,
    "perception.model_access": perceptionModelAccess.value,
    "perception.screen": perceptionScreen.value,
    "perception.distill": perceptionDistill.value,
    "perception.recap": perceptionRecap.value,
  };
  if (key === "perception.master") perceptionMaster.value = next;
  if (key === "perception.app") perceptionApp.value = next;
  if (key === "perception.activity") perceptionActivity.value = next;
  if (key === "perception.model_access") perceptionModelAccess.value = next;
  if (key === "perception.screen") perceptionScreen.value = next;
  if (key === "perception.distill") perceptionDistill.value = next;
  if (key === "perception.recap") perceptionRecap.value = next;
  const r = await setSettings({ [key]: next });
  if (r === null) {
    perceptionMaster.value = old["perception.master"];
    perceptionApp.value = old["perception.app"];
    perceptionActivity.value = old["perception.activity"];
    perceptionModelAccess.value = old["perception.model_access"];
    perceptionScreen.value = old["perception.screen"];
    perceptionDistill.value = old["perception.distill"];
    perceptionRecap.value = old["perception.recap"];
    perceptionErr.value = "设置未生效（大脑不在线？）";
    return;
  }
  syncPerceptionSettings(r);
  if (key === "perception.master" && next && !perceptionMaster.value) {
    perceptionErr.value = "感知存储不可用，已保持关闭";
  }
}

// screen 开关：关闭直接生效；开启先弹行内说明，确认才写入（照删除的行内两段确认模式）
function onScreenToggle() {
  if (perceptionScreen.value) {
    void setPerceptionSetting("perception.screen", false);
  } else {
    screenConfirming.value = true;
  }
}

async function confirmScreenEnable() {
  screenConfirming.value = false;
  await setPerceptionSetting("perception.screen", true);
}

// distill 开关：关闭直接生效；开启先弹行内说明，确认才写入（照屏幕内容的行内两段确认模式）
function onDistillToggle() {
  if (perceptionDistill.value) {
    // 关闭 distill 会连带让 recap 失去依赖：一并撤回 recap 的行内确认，避免悬空
    recapConfirming.value = false;
    void setPerceptionSetting("perception.distill", false);
  } else {
    distillConfirming.value = true;
  }
}

async function confirmDistillEnable() {
  distillConfirming.value = false;
  await setPerceptionSetting("perception.distill", true);
}

// recap 开关：依赖 distill；关闭直接生效，开启先弹行内说明，确认才写入
function onRecapToggle() {
  if (!perceptionDistill.value) return; // 依赖未满足：按钮已 disabled，兜底防误触
  if (perceptionRecap.value) {
    void setPerceptionSetting("perception.recap", false);
  } else {
    recapConfirming.value = true;
  }
}

async function confirmRecapEnable() {
  recapConfirming.value = false;
  await setPerceptionSetting("perception.recap", true);
}

// 「立即提炼昨日」：最长 90s（LLM 60s 超时 + 余量），结果一次性展示
async function onDistillNow() {
  distillRunning.value = true;
  distillResult.value = "";
  const r = await distillNow();
  distillRunning.value = false;
  if (!r.ok) {
    distillResult.value = r.reason === "timeout" ? "提炼超时，请稍后再试" : "提炼未开启或大脑不在线";
    return;
  }
  const st = r.result?.status;
  if (st === "ok") {
    distillResult.value = `已提炼 ${r.result?.day ?? "昨日"}：洞察 ${r.result?.insights ?? 0} 条、模式 ${r.result?.patterns ?? 0} 条、事件 ${r.result?.events ?? 0} 条`;
  } else if (st === "no_data") {
    distillResult.value = "昨日没有感知观察，未提炼";
  } else if (st === "already_running") {
    distillResult.value = "提炼正在进行中";
  } else {
    distillResult.value = "提炼失败，请稍后再试";
  }
}

let unlistenPerms: (() => void) | null = null;
onMounted(async () => {
  unlistenPerms = await onBrainPermissions((p) => {
    perms.value = p;
  });
  // 主动拉一次：大脑在 hello 时广播过权限，本窗可能后于 hello 挂载（大脑不在线则静默失败，等上线广播）
  void checkPermissions().catch(() => {});
  void getSettingsOnce().then((s) => {
    if (s) syncPerceptionSettings(s);
  });
  // 大脑上线时权限状态会重新广播（onBrainPermissions 覆盖 perms），无需额外处理
});
onUnmounted(() => {
  unlistenPerms?.();
});
</script>

<template>
  <section class="s-group">
    <div class="s-group-title">感知</div>
    <div class="s-note">全部默认关闭。观察内容加密存放在本机；只有开启下方模型读取开关并询问最近活动时，所选时间段才会发送给当前模型服务。</div>
    <div class="s-row">
      <span class="s-row-label">启用感知<span class="s-row-why">总开关，关闭后立即停止采样</span></span>
      <button class="switch" :class="{ on: perceptionMaster }" role="switch" :aria-checked="perceptionMaster" title="启用感知" @click="setPerceptionSetting('perception.master', !perceptionMaster)"><i /></button>
    </div>
    <div v-if="perceptionMaster" class="s-row">
      <span class="s-row-label">应用使用<span class="s-row-why">记录应用使用时间与切换</span></span>
      <button class="switch" :class="{ on: perceptionApp }" role="switch" :aria-checked="perceptionApp" title="应用使用" @click="setPerceptionSetting('perception.app', !perceptionApp)"><i /></button>
    </div>
    <div v-if="perceptionMaster" class="s-row">
      <span class="s-row-label">活动状态<span class="s-row-why">记录忙碌/空闲，用于健康节律</span></span>
      <button class="switch" :class="{ on: perceptionActivity }" role="switch" :aria-checked="perceptionActivity" title="活动状态" @click="setPerceptionSetting('perception.activity', !perceptionActivity)"><i /></button>
    </div>
    <div v-if="perceptionMaster" class="s-row">
      <span class="s-row-label">模型读取<span class="s-row-why">询问「最近在做什么」时可查看最近感知内容</span></span>
      <button class="switch" :class="{ on: perceptionModelAccess }" role="switch" :aria-checked="perceptionModelAccess" title="模型读取" @click="setPerceptionSetting('perception.model_access', !perceptionModelAccess)"><i /></button>
    </div>
    <div v-if="perceptionMaster" class="s-row">
      <span class="s-row-label">
        屏幕内容
        <span class="s-row-why">截图感知屏幕内容；无法读取结构时发送给视觉模型概括</span>
      </span>
      <button class="switch" :class="{ on: perceptionScreen }" role="switch" :aria-checked="perceptionScreen" title="屏幕内容" @click="onScreenToggle"><i /></button>
    </div>
    <!-- 开启屏幕内容的行内两段确认：说明外发边界后，确认才写入 -->
    <div v-if="screenConfirming" class="s-row">
      <span class="s-row-label"><span class="s-row-why">屏幕内容将被持续观察；界面结构文本只存本机，无法读取结构时的截图会发送给智谱 GLM 做概括</span></span>
      <span class="s-row-btns">
        <button class="s-mini danger" @click="confirmScreenEnable">确认开启</button>
        <button class="s-mini" @click="screenConfirming = false">取消</button>
      </span>
    </div>
    <div class="s-row">
      <span class="s-row-label">每日提炼<span class="s-row-why">每日凌晨将昨日感知内容发送给当前模型做提炼，产出模式记忆与效率洞察</span></span>
      <button class="switch" :class="{ on: perceptionDistill }" role="switch" :aria-checked="perceptionDistill" :disabled="!perceptionMaster" title="每日提炼" @click="onDistillToggle"><i /></button>
    </div>
    <!-- 开启每日提炼的行内两段确认：说明外发边界后，确认才写入 -->
    <div v-if="distillConfirming" class="s-row">
      <span class="s-row-label"><span class="s-row-why">确认后，每日 04:17 自动将昨日全天感知内容（应用名、窗口标题、活动状态、界面结构文本与截图概括）发送给当前模型做提炼；不发送截图原图或按键内容</span></span>
      <span class="s-row-btns">
        <button class="s-mini danger" @click="confirmDistillEnable">确认开启</button>
        <button class="s-mini" @click="distillConfirming = false">取消</button>
      </span>
    </div>
    <div v-if="perceptionDistill" class="s-row">
      <span class="s-row-label"><span class="s-row-why">{{ perceptionScreen ? "提炼含应用、活动与屏幕内容" : "未开启屏幕内容，提炼只含应用与活动数据" }}</span></span>
      <span class="s-row-btns">
        <button class="s-mini" :disabled="distillRunning" @click="onDistillNow">{{ distillRunning ? "提炼中…" : "立即提炼昨日" }}</button>
      </span>
    </div>
    <div v-if="distillResult" class="s-note">{{ distillResult }}</div>
    <div class="s-row">
      <span class="s-row-label">晨间反刍<span class="s-row-why">{{ perceptionDistill ? "每天首次打开主窗时，主动端出昨日提炼的洞察与建议" : "需先开启每日提炼" }}</span></span>
      <button class="switch" :class="{ on: perceptionRecap }" role="switch" :aria-checked="perceptionRecap" :disabled="!perceptionDistill" title="晨间反刍" @click="onRecapToggle"><i /></button>
    </div>
    <!-- 开启晨间反刍的行内两段确认：说明触发时机与打扰度边界后，确认才写入 -->
    <div v-if="recapConfirming" class="s-row">
      <span class="s-row-label"><span class="s-row-why">确认后，每天首次打开主窗时，译宝会主动把昨日的效率洞察与建议端给你（受打扰度旋钮管，可随时关）</span></span>
      <span class="s-row-btns">
        <button class="s-mini danger" @click="confirmRecapEnable">确认开启</button>
        <button class="s-mini" @click="recapConfirming = false">取消</button>
      </span>
    </div>
    <div class="s-note">{{ perceptionMaster ? "运行中" : "已暂停" }}</div>
    <div v-if="perceptionErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ perceptionErr }}</div>
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
      <span class="s-row-why">{{ perms ? "授权后自动检测；屏幕录制需重启译宝生效。列表里找不到译宝？可点「+」或把文件拖进列表" : "大脑连接后自动检测" }}</span>
      <button class="s-mini" @click="revealInFinder">在 Finder 中显示</button>
      <button class="s-mini" @click="recheck">重新检测</button>
    </div>
  </section>
</template>
