<script setup lang="ts">
// 设置页「通用」分类（自包含）：外观 / 主屏零件 / 模型 / 语音 / 启动与快捷键 / 搜索 / 浏览器扩展 / 手机伴生端。
// 大脑只在启动时读 .env，所以模型/语音保存链路 = save_setup_config → restart_brain。
import { computed, onMounted, onUnmounted, ref } from "vue";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import QRCode from "qrcode";
import YbIcon from "../YbIcon.vue";
import {
  getSetupConfig,
  saveSetupConfig,
  restartBrain,
  getSettingsOnce,
  setSettings,
  getHttpPairInfoOnce,
  onBrainStatus,
  type SettingsValues,
  type HttpPairInfo,
} from "../../lib/brain";
import { buildPairUrl } from "../../lib/pair";
import { applyFinish, FINISHES, readFinish, type FinishId } from "../../lib/finish";
import { applyChrome, isHomeChromeId, useHomeChrome } from "../../lib/home-chrome";
import { HOME_PRESET_LIST } from "../../lib/home-assembly";
import {
  HOME_WIDGETS,
  WIDGET_MATERIALS,
  WIDGET_SIZES,
  useHomeWidgets,
} from "../../lib/home-widgets";

// ---- 外观 ----
const finish = ref<FinishId>(readFinish());
function onFinish(id: FinishId) {
  finish.value = id;
  applyFinish(id);
}
const { id: chromeId } = useHomeChrome();
function onChrome(id: string) {
  if (isHomeChromeId(id)) applyChrome(id);
}
const homeWidgets = useHomeWidgets();

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

// ---- 启动与快捷键（即时生效，不落 .env 不重启）----
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

// ---- TTS 引擎（settings.json；切换下次启动生效）----
const ttsProvider = ref<"edge" | "cosyvoice" | "cosyvoice_cloud">("edge");
const ttsErr = ref("");

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

// ---- 搜索（settings.json；即时生效）----
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
  void getSettingsOnce().then((s) => {
    if (s) {
      const tp = s["tts.provider"];
      if (tp === "edge" || tp === "cosyvoice" || tp === "cosyvoice_cloud") ttsProvider.value = tp;
      if (typeof s["http.token"] === "string") bridgeToken.value = s["http.token"];
      if (typeof s["http.mobile_token"] === "string") mobileToken.value = s["http.mobile_token"];
      if (typeof s["http.bind"] === "string") lanOpen.value = s["http.bind"] === "0.0.0.0";
      syncSearchSettings(s);
    }
  });
  void refreshPair(); // 配对二维码（大脑不在线则显示提示行）
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
  <section class="s-group">
    <div class="s-group-title">外观</div>
    <div class="s-note">材质、深浅、预设分开：主题管颜色，材质管圆角釉面和阴影。三栏、整桌、会客是我们排好的格子；画布才自己拖零件。点名字就是换桌。</div>
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
    <div class="s-row">
      <span class="s-row-label">预设</span>
      <div class="finish-seg" role="radiogroup" aria-label="主屏预设">
        <button
          v-for="c in HOME_PRESET_LIST"
          :key="c.id"
          type="button"
          class="finish-opt"
          role="radio"
          :aria-checked="chromeId === c.id"
          :class="{ on: chromeId === c.id }"
          :title="c.hint"
          @click="onChrome(c.id)"
        >{{ c.label }}</button>
      </div>
    </div>
  </section>

  <section class="s-group">
    <div class="s-group-title">主屏零件</div>
    <div class="s-note">桌上的瓷片可隐藏、改大小、换瓷或玻璃。结构预设不拖；画布才拖、磁吸。落点由当前预设决定。</div>
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
      <button
        type="button"
        class="s-mini-btn"
        title="清掉这份预设里拖过的位置；结构预设几乎无框可清"
        @click="homeWidgets.resetLayout(chromeId)"
      >恢复默认</button>
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
    <div v-if="mobileErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ mobileErr }}</div>
    <div v-if="mobileMsg" class="s-msg ok">{{ mobileMsg }}</div>
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
