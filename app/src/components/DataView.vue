<script setup lang="ts">
// 大窗「数据」页：感知日志 + 记忆管理 + 清空与文件。
// 从设置页移出（用户要求：数据/记忆/日志不混在设置里），独立成侧栏入口。
// 样式与 SettingsView 共用同一套 s-*（复制副本，视觉语言一致；后续可抽公共 css）。
import { computed, onMounted, ref } from "vue";
import YbIcon from "./YbIcon.vue";
import {
  getPerceptionOnce,
  deletePerception,
  clearPerception,
  getMemListOnce,
  memDelete,
  memEdit,
  clearBrainData,
  openDataDir,
  getSettingsOnce,
  type ClearKind,
  type MemItem,
  type PerceptionItem,
} from "../lib/brain";

// ---- 感知日志（让用户看到、逐条删、全部清空）----
const perceptionMaster = ref(false);
const perceptionItems = ref<PerceptionItem[]>([]);
const perceptionAvailable = ref(true);
const perceptionLoaded = ref(false);
const perceptionLoading = ref(false);
const perceptionHasMore = ref(false);
const perceptionErr = ref("");
const perceptionConfirming = ref<number | "all" | null>(null);
const perceptionDeleting = ref<number | "all" | null>(null);

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
  if (item.source === "screen") {
    const text = String(item.payload.text || "");
    const cut = text.length > 60 ? `${text.slice(0, 60)}…` : text;
    return item.kind === "vision" ? `概括 · ${cut}` : cut;
  }
  return String(item.payload.text || item.kind);
}

function perceptionSource(item: PerceptionItem): string {
  return item.source === "app" ? "应用" : item.source === "activity" ? "活动" : item.source === "screen" ? "屏幕" : item.source;
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
const memEditing = ref<string | null>(null);
const memDraft = ref("");
const memSaving = ref<string | null>(null);
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

// ---- 清空与文件（行内二次确认）----
const confirming = ref<ClearKind | null>(null);
const clearing = ref<ClearKind | null>(null);
const clearMsg = ref("");
const clearErr = ref(false);

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

onMounted(() => {
  void loadPerception();
  void loadMem();
  // 感知总开关状态：只用于「感知未开启」的空态文案
  void getSettingsOnce().then((s) => {
    if (s && typeof s["perception.master"] === "boolean") perceptionMaster.value = s["perception.master"];
  });
});
</script>

<template>
  <div class="data-page">
    <div class="d-scroll">
      <!-- 感知日志：让用户看到、逐条删、全部清空 -->
      <section class="s-group">
        <div class="s-group-title">感知日志<template v-if="perceptionItems.length"> · {{ perceptionItems.length }}</template></div>
        <div v-if="perceptionLoaded && !perceptionAvailable" class="s-note">感知存储不可用；总开关会保持关闭。</div>
        <div v-else-if="perceptionLoaded && !perceptionItems.length" class="s-note">
          {{ perceptionMaster ? "还没有观察——切换一次应用或等待进入空闲。" : "感知未开启；已有记录仍可在开启前审阅和删除。" }}
        </div>
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
        <div v-if="perceptionErr" class="s-msg err"><YbIcon name="alert" :size="13" />{{ perceptionErr }}</div>
      </section>

      <!-- 记忆管理（「它记得我什么」：按命名空间列出，可单条删除/编辑；彻底清空走下方清空区） -->
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

      <!-- 清空与文件 -->
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
    </div>
  </div>
</template>

<style scoped>
/* 数据页：与设置页同一张皮（浅灰底反衬白卡），无左侧分类（Home 侧栏已是导航） */
.data-page {
  flex: 1;
  min-height: 0;
  display: flex;
  background: var(--yb-card-page-bg);
}
.d-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-4);
  padding: 0 var(--yb-space-5) var(--yb-space-5);
  scrollbar-width: thin;
}
.d-scroll::-webkit-scrollbar {
  width: 7px;
}
.d-scroll::-webkit-scrollbar-thumb {
  background: var(--yb-border-strong);
  border-radius: var(--yb-radius-pill);
}

/* ---- 以下与 SettingsView 共用同一套 s-*（复制副本，保证视觉一致）---- */
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
.s-group-title {
  margin: calc(var(--yb-space-1) * -1) 0 0;
  padding-bottom: var(--yb-space-2);
  border-bottom: 1px solid var(--yb-card-row-line);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
}
.s-note {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  line-height: 1.4;
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
.s-row-btns {
  display: inline-flex;
  gap: 6px;
}
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
input:focus,
select:focus,
textarea:focus {
  border-color: var(--yb-accent);
  box-shadow: var(--yb-focus-ring);
}
/* 长列表：限高自滚 */
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
/* 感知日志行 */
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
/* 命名空间徽章（感知来源也用它） */
.m-ns {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-sm);
  line-height: var(--yb-lh-base);
}
/* 记忆行 */
.m-row {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  padding: 7px 0;
  border-top: 1px solid var(--yb-card-row-line);
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
</style>
