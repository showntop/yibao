<script setup lang="ts">
// 设置页「主动协助」分类（自包含）：自主权旋钮（主动找我/开口播报）/ 健康节律 / 屏幕建议 / 主动行为统计。
import { computed, onMounted, ref } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { getFeedStatsOnce, getSettingsOnce, setSettings, type SettingsValues, type TrustStats } from "../../lib/brain";

// ---- 自主权（数据目录 settings.json，即时生效免重启）----
const proactiveVoice = ref(true);
const proactiveLevel = ref<"quiet" | "bubble" | "full">("full");
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

// ---- 健康节律 / 屏幕建议（settings.json；即时生效）----
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

// 主动行为统计（v1.1 信任仪表读模型）：只读展示，大脑不在线显示零值
const trustStats = ref<TrustStats | null>(null);
const trustSummary = computed(() => {
  const s = trustStats.value;
  if (!s) return "统计加载中…";
  return `近 ${s.days} 天共 ${s.total} 条 · 已读率 ${Math.round(s.read_rate * 100)}% · 忽略率 ${Math.round(s.ignored_rate * 100)}%`;
});

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

onMounted(async () => {
  void getSettingsOnce().then((s) => {
    if (s) {
      if (typeof s.proactive_voice === "boolean") proactiveVoice.value = s.proactive_voice;
      const lv = s["proactive.level"];
      if (lv === "quiet" || lv === "bubble" || lv === "full") proactiveLevel.value = lv;
      syncWatchSettings(s);
    }
  });
  void getFeedStatsOnce().then((s) => { trustStats.value = s; });
});
</script>

<template>
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
