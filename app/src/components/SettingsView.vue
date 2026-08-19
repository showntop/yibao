<script setup lang="ts">
// 设置页（home 大窗唯一内容）：模型/语音（保存后重启大脑生效）+ 通用/权限/数据（即时生效）。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { getVersion } from "@tauri-apps/api/app";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import { openUrl } from "@tauri-apps/plugin-opener";
import QRCode from "qrcode";
import YbIcon from "./YbIcon.vue";
import {
  getSetupConfig,
  saveSetupConfig,
  restartBrain,
  checkPermissions,
  promptPermission,
  onBrainStatus,
  onBrainPermissions,
  getSettingsOnce,
  setSettings,
  getFeedStatsOnce,
  getHttpPairInfoOnce,
  distillNow,
  type BrainPermissions,
  type SettingsValues,
  type HttpPairInfo,
} from "../lib/brain";
import type { TrustStats } from "../lib/brain";
import { buildPairUrl } from "../lib/pair";
import { applyFinish, FINISHES, readFinish, type FinishId } from "../lib/finish";
import {
  HOME_WIDGETS,
  WIDGET_MATERIALS,
  WIDGET_SIZES,
  useHomeWidgets,
} from "../lib/home-widgets";

// ---- 分类导航（macOS 系统设置语言）----
// 原先 11 个分组平铺一列要滚很久，且「感知日志」「记忆管理」这种数据浏览器
// 混在开关中间。按语义收成 4 类，每类内仍是分组卡片。
// 「关于」原塞在「通用」末位，与可配置项混在一起；版本/版权是只读元信息，
// 单独成类更明确——「通用」只剩真正可改的设置。
type Cat = "general" | "proactive" | "privacy" | "about";
const cat = ref<Cat>("general");
const finish = ref<FinishId>(readFinish());
function onFinish(id: FinishId) {
  finish.value = id;
  applyFinish(id);
}
const homeWidgets = useHomeWidgets();
const CATS: { id: Cat; label: string; icon: "gear" | "sparkle" | "lock" | "info" }[] = [
  { id: "general", label: "通用", icon: "gear" },
  { id: "proactive", label: "主动协助", icon: "sparkle" },
  { id: "privacy", label: "隐私与权限", icon: "lock" },
  { id: "about", label: "关于", icon: "info" },
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

// ---- 浏览器扩展（http 桥共享 token；sidecar 启动时生成，复制到扩展选项页）----
const bridgeToken = ref(""); // 空 = 大脑未连接或尚未写入 http.token
const showToken = ref(false);
const tokenMsg = ref("");
const maskedToken = computed(() => (bridgeToken.value ? "•".repeat(Math.min(bridgeToken.value.length, 12)) : "（大脑未连接）"));

async function copyToken() {
  try {
    await navigator.clipboard.writeText(bridgeToken.value);
    tokenMsg.value = "已复制";
  } catch {
    showToken.value = true; // clipboard 被拒（无手势/权限）→ 显示全文手选复制
    tokenMsg.value = "复制失败，已为你显示全文";
  }
  setTimeout(() => (tokenMsg.value = ""), 2000);
}

// ---- 手机伴生端（移动 token 热重置 / 局域网开关 / 配对二维码）----
const mobileToken = ref(""); // 空 = 大脑未连接或尚未写入 http.mobile_token
const showMobileToken = ref(false);
const mobileMsg = ref("");
const mobileErr = ref("");
const maskedMobileToken = computed(() =>
  mobileToken.value ? "•".repeat(Math.min(mobileToken.value.length, 12)) : "（大脑未连接）",
);
// 局域网访问开关：http.bind === "0.0.0.0" 视为开（写入后需重启大脑生效）
const lanOpen = ref(false);
const lanInfo = ref<HttpPairInfo | null>(null); // null = 尚未拿到（超时=大脑不在线）
const pairQr = ref(""); // data URL；空串不显示码（无内网 IP / 生成失败）

async function copyMobileToken() {
  try {
    await navigator.clipboard.writeText(mobileToken.value);
    mobileMsg.value = "已复制";
  } catch {
    showMobileToken.value = true;
    mobileMsg.value = "复制失败，已为你显示全文";
  }
  setTimeout(() => (mobileMsg.value = ""), 2000);
}

/** 重置移动 token：写新随机 32 位 hex，大脑热生效（旧手机立刻失联需重新配对）。 */
async function resetMobileToken() {
  mobileErr.value = "";
  const r = await setSettings({ "http.mobile_token": crypto.randomUUID().replace(/-/g, "") });
  if (r === null) {
    mobileErr.value = "设置未生效（大脑不在线？）";
    return;
  }
  if (typeof r["http.mobile_token"] === "string") mobileToken.value = r["http.mobile_token"];
  mobileMsg.value = "已重置并即时生效，旧手机需重新配对";
  setTimeout(() => (mobileMsg.value = ""), 3000);
  void buildQr(); // token 变了二维码要重画
}

/** 局域网开关：写 http.bind；改绑定地址要重启大脑才生效（提示里说明）。 */
async function toggleLan() {
  mobileErr.value = "";
  const next = !lanOpen.value;
  lanOpen.value = next; // 乐观更新，失败回滚
  const r = await setSettings({ "http.bind": next ? "0.0.0.0" : "127.0.0.1" });
  if (r === null) {
    lanOpen.value = !next;
    mobileErr.value = "设置未生效（大脑不在线？）";
    return;
  }
  mobileMsg.value = "已保存，重启大脑后生效";
  setTimeout(() => (mobileMsg.value = ""), 3000);
}

/** 拉配对信息并重画二维码；大脑不在线/无内网 IP 时空串隐藏码。 */
async function refreshPair() {
  lanInfo.value = await getHttpPairInfoOnce();
  await buildQr();
}

async function buildQr() {
  const url = lanInfo.value ? buildPairUrl(lanInfo.value.lan_ip, lanInfo.value.port, mobileToken.value) : "";
  if (!url) {
    pairQr.value = "";
    return;
  }
  try {
    pairQr.value = await QRCode.toDataURL(url);
  } catch {
    pairQr.value = "";
  }
}

// 每次切回「通用」分类刷新配对信息（IP/端口可能在设置页停留期间变化）
watch(cat, (c) => {
  if (c === "general") void refreshPair();
});

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

// ---- 自主权（数据目录 settings.json，即时生效免重启）----
const proactiveVoice = ref(true);
const proactiveLevel = ref<"quiet" | "bubble" | "full">("full");
// TTS 引擎（settings.json；切换下次启动生效）
const ttsProvider = ref<"edge" | "cosyvoice" | "cosyvoice_cloud">("edge");
const ttsErr = ref("");
// 联网搜索通道（settings.json；即时生效）
type SearchProvider = "browser" | "ddg" | "searxng" | "brave" | "tavily" | "serper";
const SEARCH_PROVIDERS: { id: SearchProvider; label: string }[] = [
  { id: "browser", label: "浏览器打开（免配置）" },
  { id: "ddg", label: "DuckDuckGo（免费免 key）" },
  { id: "searxng", label: "SearXNG（自建实例）" },
  { id: "brave", label: "Brave Search（API key）" },
  { id: "tavily", label: "Tavily（API key）" },
  { id: "serper", label: "Serper（API key）" },
];
type KeyProvider = "brave" | "tavily" | "serper";
const KEY_PROVIDERS: KeyProvider[] = ["brave", "tavily", "serper"];

/** 6 值联合收窄到「需要 key 的 3 个」；不是则 null。模板与逻辑共用，避免依赖模板收窄。 */
function asKeyProvider(p: SearchProvider): KeyProvider | null {
  return (KEY_PROVIDERS as readonly SearchProvider[]).includes(p) ? (p as KeyProvider) : null;
}

function searchKeyOf(p: SearchProvider): string {
  const k = asKeyProvider(p);
  return k ? searchKeys.value[k] : "";
}

function updateSearchKey(p: SearchProvider, v: string): void {
  const k = asKeyProvider(p);
  if (k) void setSearchKey(k, v);
}

const searchProvider = ref<SearchProvider>("browser");
const searchSearxngUrl = ref("");
const searchKeys = ref<{ brave: string; tavily: string; serper: string }>({ brave: "", tavily: "", serper: "" });
const searchErr = ref("");

async function setSearchProvider(p: SearchProvider) {
  if (p === searchProvider.value) return;
  searchErr.value = "";
  const prev = searchProvider.value;
  searchProvider.value = p; // 乐观更新，失败回滚
  const r = await setSettings({ "search.provider": p });
  if (r === null) {
    searchProvider.value = prev;
    searchErr.value = "设置未生效（大脑不在线？）";
  }
}

async function setSearchSearxngUrl(v: string) {
  const normalized = v.trim();
  searchErr.value = "";
  const prev = searchSearxngUrl.value;
  searchSearxngUrl.value = normalized; // 乐观更新，失败回滚
  const r = await setSettings({ "search.searxng_url": normalized });
  if (r === null) {
    searchSearxngUrl.value = prev;
    searchErr.value = "设置未生效（大脑不在线？）";
  }
}

async function setSearchKey(p: "brave" | "tavily" | "serper", v: string) {
  searchErr.value = "";
  const prev = { ...searchKeys.value };
  searchKeys.value = { ...searchKeys.value, [p]: v }; // 乐观更新，失败回滚
  const r = await setSettings({ "search.keys": searchKeys.value });
  if (r === null) {
    searchKeys.value = prev;
    searchErr.value = "设置未生效（大脑不在线？）";
  }
}

function syncSearchSettings(s: SettingsValues) {
  const p = s["search.provider"];
  if (p === "browser" || p === "ddg" || p === "searxng" || p === "brave" || p === "tavily" || p === "serper") {
    searchProvider.value = p;
  }
  if (typeof s["search.searxng_url"] === "string") searchSearxngUrl.value = s["search.searxng_url"];
  const keys = s["search.keys"];
  if (keys && typeof keys === "object") {
    for (const kp of ["brave", "tavily", "serper"] as const) {
      const kv = (keys as Record<string, unknown>)[kp];
      if (typeof kv === "string") searchKeys.value = { ...searchKeys.value, [kp]: kv };
    }
  }
}
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

// 主动行为统计（v1.1 信任仪表读模型）：只读展示，大脑不在线显示零值
const trustStats = ref<TrustStats | null>(null);
const trustSummary = computed(() => {
  const s = trustStats.value;
  if (!s) return "统计加载中…";
  return `近 ${s.days} 天共 ${s.total} 条 · 已读率 ${Math.round(s.read_rate * 100)}% · 忽略率 ${Math.round(s.ignored_rate * 100)}%`;
});

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

// ---- 感知日志 / 记忆管理 / 清空：已移出到「数据」页（DataView.vue）----

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
  void getSettingsOnce().then((s) => { // 自主权旋钮当前值（大脑不在线则保持默认）
    if (s) {
      if (typeof s.proactive_voice === "boolean") proactiveVoice.value = s.proactive_voice;
      const lv = s["proactive.level"];
      if (lv === "quiet" || lv === "bubble" || lv === "full") proactiveLevel.value = lv;
      const tp = s["tts.provider"];
      if (tp === "edge" || tp === "cosyvoice" || tp === "cosyvoice_cloud") ttsProvider.value = tp;
      if (typeof s["http.token"] === "string") bridgeToken.value = s["http.token"];
      if (typeof s["http.mobile_token"] === "string") mobileToken.value = s["http.mobile_token"];
      if (typeof s["http.bind"] === "string") lanOpen.value = s["http.bind"] === "0.0.0.0";
      syncSearchSettings(s);
      syncWatchSettings(s);
    }
  });
  void refreshPair(); // 配对二维码（大脑不在线则显示提示行）
  void getFeedStatsOnce().then((s) => { trustStats.value = s; });
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
          <div class="s-group-title">外观</div>
          <div class="s-note">材质与深浅主题分开：主题管颜色，材质管圆角、釉面和阴影。组件只读令牌，之后加皮肤只扩这一项。</div>
          <div class="s-row">
            <span class="s-row-label">材质</span>
            <div class="finish-seg" role="radiogroup" aria-label="材质">
              <button
                v-for="f in FINISHES"
                :key="f.id"
                type="button"
                class="finish-opt"
                role="radio"
                :aria-checked="finish === f.id"
                :class="{ on: finish === f.id }"
                :title="f.hint"
                @click="onFinish(f.id)"
              >{{ f.label }}</button>
            </div>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">主屏零件</div>
          <div class="s-note">左右栏是桌面上的瓷片，不是钉死的侧栏。可隐藏、改大小、换瓷或玻璃；桌面上按住零件右上角拖动可排序。</div>
          <div v-for="w in HOME_WIDGETS" :key="w.id" class="s-row widget-row">
            <label class="s-row-label">
              <input
                type="checkbox"
                :checked="homeWidgets.spec(w.id).visible"
                @change="homeWidgets.hide(w.id)"
              />
              {{ w.label }}
            </label>
            <div class="widget-ctrls">
              <div class="finish-seg" role="radiogroup" :aria-label="`${w.label}大小`">
                <button
                  v-for="s in WIDGET_SIZES"
                  :key="s.id"
                  type="button"
                  class="finish-opt"
                  :class="{ on: homeWidgets.spec(w.id).size === s.id }"
                  @click="homeWidgets.setSize(w.id, s.id)"
                >{{ s.label }}</button>
              </div>
              <div class="finish-seg" role="radiogroup" :aria-label="`${w.label}材质`">
                <button
                  v-for="m in WIDGET_MATERIALS"
                  :key="m.id"
                  type="button"
                  class="finish-opt"
                  :class="{ on: homeWidgets.spec(w.id).material === m.id }"
                  @click="homeWidgets.setMaterial(w.id, m.id)"
                >{{ m.label }}</button>
              </div>
            </div>
          </div>
          <div class="s-row">
            <span class="s-row-label">布局</span>
            <button type="button" class="s-mini-btn" @click="homeWidgets.reset()">恢复默认</button>
          </div>
        </section>

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

        <!-- 操作带：只针对「模型」+「语音」两组（写 .env → 重启大脑）。
             sticky 贴 s-scroll 底部：滚到下面的"启动/搜索/扩展"卡时也始终可达；
             渐变遮罩防止下方卡从透明区透出，盖在 s-group 之上但本身不是 s-group（不带卡边）。 -->
        <div class="s-ops-band">
          <div class="s-ops-text">
            <YbIcon class="s-ops-ic" name="info" :size="14" />
            <span>以上「模型」与「语音」的改动需保存后重启大脑才生效</span>
          </div>
          <div class="s-ops-cta">
            <span v-if="saveErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ saveErr }}</span>
            <span v-else-if="saveMsg" class="s-msg ok">{{ saveMsg }}</span>
            <button class="s-primary" :disabled="saving" @click="save">{{ saving ? "保存中…" : "保存并重启大脑" }}</button>
          </div>
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
          <div class="s-row">
            <span class="s-row-label"></span>
            <span class="s-row-value">⌘⇧U 划词唤起（选中文字 → 动作条）</span>
          </div>
          <div class="s-row">
            <span class="s-row-label"></span>
            <span class="s-row-value">⌘⇧I 截图即问（框选区域 → 提问）</span>
          </div>
        </section>

        <section class="s-group">
          <div class="s-group-title">搜索</div>
          <div class="s-note">联网搜索通道，设置即时生效。除「浏览器打开」外，搜索都会返回结构化结果（标题/链接/摘要）供译宝阅读；点开结果想读正文时用 extract_url。</div>
          <label class="s-field">
            <span class="s-label">搜索服务</span>
            <select
              :value="searchProvider"
              @change="setSearchProvider(($event.target as HTMLSelectElement).value as SearchProvider)"
            >
              <option v-for="sp in SEARCH_PROVIDERS" :key="sp.id" :value="sp.id">{{ sp.label }}</option>
            </select>
          </label>
          <label v-if="searchProvider === 'searxng'" class="s-field">
            <span class="s-label">SearXNG 实例地址<span class="s-row-why">例如 http://127.0.0.1:8888</span></span>
            <input
              type="text"
              :value="searchSearxngUrl"
              placeholder="http://127.0.0.1:8888"
              @change="setSearchSearxngUrl(($event.target as HTMLInputElement).value)"
            />
          </label>
          <template v-if="asKeyProvider(searchProvider)">
            <div class="s-note">API key 此处填写优先于 .env（YIBAO_SEARCH_{{ searchProvider.toUpperCase() }}_KEY）；留空 = 用 .env 配置。</div>
            <label class="s-field">
              <span class="s-label">{{ searchProvider === "brave" ? "Brave" : searchProvider === "tavily" ? "Tavily" : "Serper" }} API Key</span>
              <input
                type="password"
                :value="searchKeyOf(searchProvider)"
                :placeholder="searchKeyOf(searchProvider) ? '已配置（输入以更换）' : '未配置'"
                @change="updateSearchKey(searchProvider, ($event.target as HTMLInputElement).value)"
              />
            </label>
          </template>
          <div v-if="searchErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ searchErr }}</div>
        </section>

        <section class="s-group">
          <div class="s-group-title">浏览器扩展</div>
          <div class="s-row">
            <span class="s-row-label">连接 token</span>
            <span class="s-row-value">
              <code class="bridge-token">{{ showToken ? bridgeToken : maskedToken }}</code>
              <button class="s-mini-btn" @click="showToken = !showToken">{{ showToken ? "隐藏" : "显示" }}</button>
              <button class="s-mini-btn" :disabled="!bridgeToken" @click="copyToken">复制</button>
            </span>
          </div>
          <div v-if="tokenMsg" class="s-msg ok">{{ tokenMsg }}</div>
          <div class="s-row">
            <span class="s-row-label">端口</span>
            <span class="s-row-value">19527（YIBAO_HTTP_PORT 可覆盖，重启大脑生效）</span>
          </div>
          <div class="s-note">安装：chrome://extensions → 开发者模式 → 加载已解压 → 选仓库 extension/ 目录；扩展选项页粘贴 token。右键或工具栏按钮即可「存素材 / 存为选题」。</div>
        </section>

        <section class="s-group">
          <div class="s-group-title">手机伴生端</div>
          <div class="s-row">
            <span class="s-row-label">
              连接 token
              <span class="s-row-why">手机端访问凭据，与浏览器扩展 token 相互独立</span>
            </span>
            <span class="s-row-value">
              <code class="bridge-token">{{ showMobileToken ? mobileToken : maskedMobileToken }}</code>
              <button class="s-mini-btn" @click="showMobileToken = !showMobileToken">{{ showMobileToken ? "隐藏" : "显示" }}</button>
              <button class="s-mini-btn" :disabled="!mobileToken" @click="copyMobileToken">复制</button>
              <button class="s-mini-btn" :disabled="!mobileToken" @click="resetMobileToken">重置</button>
            </span>
          </div>
          <div v-if="mobileMsg" class="s-msg ok">{{ mobileMsg }}</div>
          <div v-if="mobileErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ mobileErr }}</div>
          <div class="s-row">
            <span class="s-row-label">
              局域网访问
              <span class="s-row-why">允许同一 WiFi 下的手机连接；关闭仅本机可访问</span>
            </span>
            <button class="switch" :class="{ on: lanOpen }" role="switch" :aria-checked="lanOpen" title="局域网访问" @click="toggleLan"><i /></button>
          </div>
          <div class="s-row">
            <span class="s-row-label">
              配对二维码
              <span class="s-row-why">手机与电脑同一 WiFi，扫码直达配对页（预填地址与 token）</span>
            </span>
            <span class="s-row-value">
              <img v-if="pairQr" class="pair-qr" :src="pairQr" alt="手机伴生端配对二维码" />
              <span v-else class="pair-qr-tip">{{ lanInfo === null ? "配对信息获取中…" : "未检测到局域网 IP（网络离线或仅本机回环），暂无法生成二维码" }}</span>
            </span>
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
        <!-- 主动行为统计（v1.1 信任仪表读模型）：只读，数字全部来自 feed 表聚合 -->
        <section class="s-group">
          <div class="s-group-title">主动行为统计</div>
          <div class="s-note">{{ trustSummary }}</div>
          <div class="s-row">
            <span class="s-row-label">任务收尾播报</span>
            <span class="s-row-value">{{ trustStats?.by_kind.task ?? 0 }}</span>
          </div>
          <div class="s-row">
            <span class="s-row-label">提醒触发</span>
            <span class="s-row-value">{{ trustStats?.by_kind.reminder ?? 0 }}</span>
          </div>
          <div class="s-row">
            <span class="s-row-label">其它主动事件</span>
            <span class="s-row-value">{{ trustStats?.by_kind.event ?? 0 }}</span>
          </div>
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
            <span class="s-row-label">允许模型读取感知记录<span class="s-row-why">询问最近活动时，将所选时间段的应用名、窗口标题和活动状态发送给当前模型；开启屏幕内容后，还包括界面结构文本与截图概括；不发送截图原图或按键内容</span></span>
            <button class="switch" :class="{ on: perceptionModelAccess }" role="switch" :aria-checked="perceptionModelAccess" title="允许模型读取感知记录" @click="setPerceptionSetting('perception.model_access', !perceptionModelAccess)"><i /></button>
          </div>
          <div class="s-row">
            <span class="s-row-label">屏幕内容<span class="s-row-why">读取界面结构文本；无法读取时截图概括</span></span>
            <button class="switch" :class="{ on: perceptionScreen }" role="switch" :aria-checked="perceptionScreen" :disabled="!perceptionMaster" title="屏幕内容" @click="onScreenToggle"><i /></button>
          </div>
          <!-- 开启屏幕观察的行内两段确认：说明外发边界后，确认才写入 -->
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
            <span class="s-row-why">{{ perms ? "授权后点重新检测；屏幕录制需重启译宝生效" : "大脑连接后自动检测" }}</span>
            <button class="s-mini" @click="recheck">重新检测</button>
          </div>
        </section>
      </template>

      <!-- ============ 关于：版本号 / 仓库存放点（只读元信息）============ -->
      <template v-else-if="cat === 'about'">
        <section class="s-group">
          <div class="s-group-title">译宝</div>
          <div class="s-row">
            <span class="s-row-label">版本</span>
            <span class="s-row-value">v{{ version }}</span>
          </div>
          <div class="s-row">
            <span class="s-row-label">数据目录</span>
            <span class="s-row-value">~/Library/Application Support/com.yibao.desktop/</span>
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
  padding: 0 var(--yb-space-5) var(--yb-space-5);
  scrollbar-width: thin;
  animation: fade-in 0.2s var(--yb-ease-out) both;
}
/* 大屏：分组卡 2 列网格，避免右侧大面积空白；窄屏单列左对齐 */
@media (min-width: 1100px) {
  .s-scroll {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    align-content: start;
    column-gap: var(--yb-space-4);
  }
}
.s-scroll::-webkit-scrollbar {
  width: 7px;
}
.s-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}
/* 分组卡：实白 + hairline + 小圆角。大屏 grid 双列自动填充，无右侧孤立空白。 */
.s-group {
  box-sizing: border-box;
  width: 100%;
  max-width: 760px;
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
  box-shadow: var(--yb-focus-ring);
}
/* 下拉框：去掉系统原生箭头，换成与主题一致的描边箭头（currentColor 跟随文字色，深浅主题都协调） */
select {
  appearance: none;
  -webkit-appearance: none;
  padding-right: 30px;
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath d='M2.5 4.5 6 8l3.5-3.5' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  cursor: pointer;
  transition: border-color var(--yb-dur-fast) var(--yb-ease-out), box-shadow var(--yb-dur-fast) var(--yb-ease-out), background-color var(--yb-dur-fast) var(--yb-ease-out);
}
select:hover {
  border-color: var(--yb-border-strong);
  background-color: var(--yb-surface-2);
}
select option {
  background: var(--yb-card-bg);
  color: var(--yb-text);
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
.finish-seg {
  display: inline-flex;
  padding: 2px;
  gap: 2px;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-segment-track);
  box-shadow: var(--yb-press);
}
.finish-opt {
  height: 24px;
  padding: 0 10px;
  border: 0;
  border-radius: calc(var(--yb-radius-sm) - 2px);
  background: transparent;
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-sm);
  cursor: pointer;
}
.finish-opt.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-text-strong);
  box-shadow: var(--yb-glaze-hi), var(--yb-shadow-1);
}
.widget-row {
  align-items: flex-start;
  padding: 6px 0;
  border-top: 1px solid var(--yb-card-row-line);
}
.widget-row:first-of-type { border-top: 0; }
.widget-ctrls {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.widget-row .s-row-label input {
  margin: 0 4px 0 0;
  accent-color: var(--yb-accent);
}
.s-mini-btn {
  margin-left: 6px;
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-xs);
  padding: 2px 8px;
  cursor: pointer;
}
.s-mini-btn:hover { background: var(--yb-surface-2); }
.bridge-token { font-family: var(--yb-mono); font-size: var(--yb-fs-sm); }
/* 手机伴生端配对二维码：固定小尺寸，提示行与码等宽对齐 */
.pair-qr { width: 120px; height: 120px; border-radius: 6px; background: #fff; padding: 4px; }
.pair-qr-tip { font-size: var(--yb-fs-sm); opacity: 0.75; max-width: 220px; }
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
  background: var(--yb-segment-thumb);
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
  color: var(--yb-text-on-accent);
}
.s-mini.danger:hover:not(:disabled) {
  background: var(--yb-danger);
  color: var(--yb-text-on-accent);
  filter: brightness(0.94);
}
.s-mini:disabled {
  opacity: 0.5;
  cursor: default;
}
/* 操作带：粘到 s-scroll 底部，长表单（启动/搜索/扩展）也能始终看到保存按钮。
   不是 s-group 也不带卡边——它是「针对上面卡组的横切操作」，
   用 surface 底 + hairline 描边 + 微阴影与卡区分，又跟 card page bg 协调。 */
.s-ops-band {
  box-sizing: border-box;
  width: 100%;
  max-width: 760px;
  position: sticky;
  bottom: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-3);
  padding: var(--yb-space-3) var(--yb-space-4);
  margin-top: calc(-1 * var(--yb-space-4));
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  /* 渐变遮罩：从 card-bg 渐变到透明，让带子「贴」在内容之上而非孤立浮空 */
  backdrop-filter: var(--yb-blur);
}
.s-ops-text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
  line-height: var(--yb-lh-ui);
  min-width: 0;
}
.s-ops-ic {
  color: var(--yb-text-faint);
  flex-shrink: 0;
}
.s-ops-cta {
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-3);
  flex-shrink: 0;
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
