<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from "vue";
import { invoke } from "@tauri-apps/api/core";
import YbIcon from "./YbIcon.vue";
import { sessionStore } from "../state/store";
import { panelAction, onBrainEvent, type BrainEvent } from "../lib/brain";
import { parseAtTrigger, stripAtTrigger, type InputContext } from "../lib/at-mention";

// busy = 生成/播报中（可打断）；listening = 录音中（麦克风切声波态，点击=取消录音）
// draft = 外部预填草稿（主屏 Feed 点击带上下文来）；变化即填入并聚焦
const props = withDefaults(
  defineProps<{
    busy?: boolean;
    listening?: boolean;
    draft?: string;
    placeholder?: string;
  }>(),
  { placeholder: "对译宝说点什么…（shift+回车换行）" },
);
const emit = defineEmits<{
  (e: "submit", text: string, contexts: InputContext[]): void;
  (e: "mic"): void;
  (e: "interrupt"): void;
}>();
const text = ref("");
const inputRef = ref<HTMLTextAreaElement | null>(null);
/** textarea 自适应高度：内容增长时行增高，清空/发送后回落 */
function autoGrow() {
  const el = inputRef.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, 140)}px`; // 上限 140px 防过高
}
const fileRef = ref<HTMLInputElement | null>(null);
const addOpen = ref(false);
const pendingContexts = ref<InputContext[]>([]);
const canSend = computed(() => text.value.trim().length > 0);

// 草稿暂存：写入 SessionStore.conversation（按活动会话），300ms trailing debounce 避免高频写
let draftTimer: ReturnType<typeof setTimeout> | null = null;
function persistDraft(v: string) {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    const id = sessionStore.conversation.getActiveConversationId();
    if (id) sessionStore.conversation.setDraft(id, v);
  }, 300);
}
let unmounted = false;
onMounted(() => {
  const id = sessionStore.conversation.getActiveConversationId();
  if (id) {
    const saved = sessionStore.conversation.getUIState(id).draft;
    if (saved) text.value = saved;
  }
  // @ 文件搜索的回包通道（pa_<rid> 关联）；注册慢于卸载则立即退订，防监听泄漏
  void onBrainEvent(onBrainEv).then((un) => { if (unmounted) un(); else unlistenBrain = un; });
});
onBeforeUnmount(() => {
  unmounted = true;
  unlistenBrain?.();
  for (const p of pendingCalls.values()) clearTimeout(p.timer);
  pendingCalls.clear();
});
// takeDraft 清屏也会触发本 watch——那次由 takeDraft 写穿落库,这里跳过一次,避免重排 debounce 二次写
let skipDraftPersist = false;
watch(text, (v) => {
  if (skipDraftPersist) { skipDraftPersist = false; return; }
  persistDraft(v);
});

// ---- @ 文件引用（chips 化）：输入 @ 触发文件搜索浮层，选中成 file chip 进 pendingContexts。
//      搜索通道 = panelAction + onBrainEvent 关联 pa_<rid>（WebviewPanel 既有模式）；
//      搜索根 = sticky 上次 @ 目录（localStorage）→ 缺省最近 coding 会话 cwd（quiet 别名
//      coding.sessions——coding.list 本体带 panel 事件，会把插件页顶成 coding 面板）→ 空态提示 ----
const AT_ROOT_KEY = "yibao.atRoot";
const atOpen = ref(false);
const atItems = ref<{ rel: string }[]>([]);
const atIdx = ref(0);
const atRoot = ref("");
let atRootResolved = false; // 负缓存：无 coding 会话时避免逐键重查（组件重挂/选中写 sticky 后重置）
let atStart = -1;
let atCaret = 0;
let atSeq = 0; // 防乱序：逐键查询的慢响应到达时已过期则丢弃

let ridBase = Math.floor(Math.random() * 1e9);
const pendingCalls = new Map<number, { resolve: (data: unknown) => void; timer: ReturnType<typeof setTimeout> }>();
let unlistenBrain: (() => void) | null = null;

function callSkill(method: string, params: Record<string, unknown>): Promise<unknown> {
  return new Promise((resolve) => {
    const rid = (ridBase = (ridBase + 1) % 2 ** 31);
    const timer = setTimeout(() => { pendingCalls.delete(rid); resolve(null); }, 8000);
    pendingCalls.set(rid, { resolve, timer });
    panelAction(method, params, rid).catch(() => {
      const p = pendingCalls.get(rid);
      if (p) { clearTimeout(p.timer); pendingCalls.delete(rid); }
      resolve(null);
    });
  });
}

function onBrainEv(e: BrainEvent) {
  const aid = e.action?.id ?? "";
  if (!aid) return;
  if (e.kind !== "action_result" && e.kind !== "error") return;
  for (const [rid, p] of [...pendingCalls]) {
    if (aid === `pa_${rid}`) {
      pendingCalls.delete(rid);
      clearTimeout(p.timer);
      // error（白名单外/DENY/skill 异常）同样立即结算为 null——不挂满 8s 超时再误导「无匹配」
      p.resolve(e.kind === "action_result" && e.result?.success ? (e.result.data ?? null) : null);
    }
  }
}

async function ensureAtRoot(): Promise<string> {
  if (atRootResolved) return atRoot.value;
  const sticky = localStorage.getItem(AT_ROOT_KEY) || "";
  if (sticky) { atRoot.value = sticky; atRootResolved = true; return sticky; }
  const data = (await callSkill("coding.sessions", {})) as { sessions?: { cwd?: string }[] } | null;
  atRoot.value = data?.sessions?.[0]?.cwd ?? "";
  atRootResolved = true;
  return atRoot.value;
}

async function atQuery(q: string) {
  const seq = ++atSeq;
  const cwd = await ensureAtRoot();
  if (seq !== atSeq) return;
  if (!cwd) { atItems.value = []; atIdx.value = 0; atOpen.value = true; return; } // 空态提示
  const data = (await callSkill("coding.files", { cwd, q })) as { files?: { rel: string }[] } | null;
  if (seq !== atSeq) return;
  atItems.value = (data?.files ?? []).slice(0, 12);
  atIdx.value = 0;
  atOpen.value = true;
}

function closeAt() {
  atOpen.value = false;
  atSeq++; // 作废在途响应
}

function pickAt(f: { rel: string }) {
  text.value = stripAtTrigger(text.value, atCaret, atStart); // 移除触发片段，文件成 chip
  // 去重：同文件重复引用无意义（与 coding 面板 pushRef 同约定）
  if (!pendingContexts.value.some((c) => c.kind === "file" && c.path === f.rel)) {
    pendingContexts.value.push({ kind: "file", label: f.rel.split("/").pop() || f.rel, path: f.rel });
  }
  if (atRoot.value) localStorage.setItem(AT_ROOT_KEY, atRoot.value); // sticky 记忆搜索根
  closeAt();
  nextTick(() => { autoGrow(); inputRef.value?.focus(); });
}

function onTextInput() {
  autoGrow();
  const el = inputRef.value;
  const caret = el?.selectionStart ?? text.value.length;
  const t = parseAtTrigger(text.value, caret);
  if (!t) { if (atOpen.value) closeAt(); return; }
  atStart = t.start;
  atCaret = caret;
  void atQuery(t.query);
}

function onAtKeydown(e: KeyboardEvent) {
  if (!atOpen.value) return;
  if (e.key === "Escape") { closeAt(); e.stopPropagation(); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!atItems.value.length) return;
    atIdx.value = (atIdx.value + (e.key === "ArrowDown" ? 1 : -1) + atItems.value.length) % atItems.value.length;
  }
}

function kindLabel(c: InputContext) {
  return c.kind === "attachment" ? "附件" : c.kind === "file" ? "文件" : "引用";
}

watch(
  () => props.draft,
  (v) => {
    if (v) {
      text.value = v;
      persistDraft(v);
      inputRef.value?.focus();
    }
  },
);

function send() {
  const t = text.value.trim();
  if (t) {
    closeAt();   // @ 浮层随发送收敛（Enter 已被浮层拦截，这里管发送钮路径）
    // AI 正在生成/播报（stopping）时发送 = 先打断再发新消息（不必手动"停止"）
    if (stopping.value) emit("interrupt");
    emit("submit", t, pendingContexts.value.slice());
    text.value = "";
    pendingContexts.value = [];
    persistDraft("");
    nextTick(() => autoGrow()); // 清空后高度回落（nextTick 等 v-model 生效）
  }
}

function openAdd(kind: InputContext["kind"]) {
  addOpen.value = false;
  if (kind === "attachment") {
    fileRef.value?.click();
    return;
  }
  if (kind === "file") {
    // 项目文件：往输入框补一个 @ 触发内联浮层（与手打 @ 同一路径；程序赋值不发 input 事件，手动触发解析）
    const cur = text.value;
    text.value = cur && !/\s$/.test(cur) ? `${cur} @` : `${cur}@`;
    nextTick(() => {
      const el = inputRef.value;
      el?.focus();
      el?.setSelectionRange(text.value.length, text.value.length);
      onTextInput();
    });
    return;
  }
  if (!pendingContexts.value.some((item) => item.kind === "reference")) {
    pendingContexts.value.push({ kind: "reference", label: "当前会话" });
  }
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  void attachFile(file);
}

/** File → dataURL（FileReader 内部流式，大文件不用 btoa 手动分块） */
function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ""));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

/** 附件落盘（save_attachment → data_dir/attachments/）：成功 → chip 带真实路径（AI 可按路径读文件）；
 *  失败退回 label-only（旧行为），不阻断输入。图片附带内存预览 dataURL（chip 缩略图）。 */
async function attachFile(file: File, label?: string) {
  try {
    const dataUrl = await fileToDataUrl(file);
    const ext = file.name.includes(".")
      ? file.name.split(".").pop()!
      : (file.type.split("/")[1] || "png");
    const path = await invoke<string>("save_attachment", {
      data: dataUrl.slice(dataUrl.indexOf(",") + 1),
      ext,
    });
    pendingContexts.value.push({
      kind: "attachment",
      label: label ?? file.name,
      path,
      ...(file.type.startsWith("image/") ? { preview: dataUrl } : {}),
    });
  } catch {
    pendingContexts.value.push({ kind: "attachment", label: label ?? file.name });
  }
}

/** 粘贴截图：剪贴板图片 → 落盘成 chip（阻止进文本）；非图片内容走默认文本粘贴 */
function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (!file) continue;
      e.preventDefault();
      const ts = new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      void attachFile(file, `截图 ${ts}`);
    }
  }
}

function removeContext(index: number) {
  pendingContexts.value.splice(index, 1);
}

function onMic() {
  // 聆听中再点麦克风 = 取消录音；否则发起语音输入
  if (props.listening) emit("interrupt");
  else emit("mic");
}

/** 右端主按钮：生成/播报中=打断、其余=发送（无内容置灰）；聆听时取消录音归麦克风，主按钮仍是发送。 */
const stopping = computed(() => props.busy && !props.listening);

function onMain() {
  // 停止图标只打断。生成中要改口：回车/点发送走 send()（内部先 interrupt 再发）。
  if (stopping.value) emit("interrupt");
  else send();
}

// IME 组字守卫（WebKit bug 165004：compositionend 先于确认 Enter 的 keydown 派发，
// 该 keydown 的 isComposing 已为 false，单靠 e.isComposing 会穿透误发——
// 记 compositionend 时间戳，50ms 窗口内的 Enter 一并拦截。参考 plugins/coding/panel/src/components/Composer.vue）
const imeComposing = ref(false);
let lastCompEnd = 0;

function onCompStart() {
  imeComposing.value = true;
}

function onCompEnd() {
  imeComposing.value = false;
  lastCompEnd = Date.now();
}

function onEnter(e: KeyboardEvent) {
  if (e.isComposing || imeComposing.value || Date.now() - lastCompEnd < 50) return;
  // @ 浮层打开时 Enter = 选中候选（无候选则关浮层），不发送
  if (atOpen.value) {
    if (atItems.value.length) pickAt(atItems.value[atIdx.value]);
    else closeAt();
    return;
  }
  send();
}

/** 外部注入文本（编码会话引用、快捷指令等）：追加到末尾，与已有文字间补一个空格 */
function insertText(t: string) {
  const cur = text.value;
  text.value = cur && !/\s$/.test(cur) ? `${cur} ${t}` : cur + t;
  persistDraft(text.value);
  nextTick(() => autoGrow()); // 等 v-model 生效后量高
  inputRef.value?.focus();
}

/** handoff 草稿随迁(spec §C):取走草稿=清文本+清持久化副本(写穿,不走 persistDraft 的
 *  300ms debounce——窗口期内重挂会读到未清旧稿并重新持久化);空草稿返回 ""。
 *  调用时机必须在 InputBar 随 bench-bar 卸载之前。 */
function takeDraft(): string {
  const d = text.value.trim();
  if (!d) return "";
  if (draftTimer) { clearTimeout(draftTimer); draftTimer = null; } // 掐死在途的打字 debounce
  skipDraftPersist = true; // 下方清屏触发的 watch 由这里写穿代劳,不再排 debounce
  text.value = "";
  const id = sessionStore.conversation.getActiveConversationId();
  if (id) sessionStore.conversation.setDraft(id, ""); // 随迁即清,写穿 store
  return d;
}

// 全局唤起等外部焦点请求(反射键唤起后输入就绪);insertText 供面板 insert-draft 注入文本;
// takeDraft 供 handoff 随迁取稿
defineExpose({ focus: () => inputRef.value?.focus(), insertText, takeDraft });
</script>

<template>
  <form class="bar" @submit.prevent="send">
    <!-- chips 行：附件/引用/@ 文件独立一行置于输入行上方（不与文本输入挤同一行） -->
    <div v-if="pendingContexts.length" class="context-list" aria-label="待发送的附件和引用">
      <span v-for="(context, index) in pendingContexts" :key="`${context.kind}-${context.label}-${index}`" class="context-chip">
        <img v-if="context.preview" :src="context.preview" class="chip-thumb" alt="" />
        {{ kindLabel(context) }} · {{ context.label }}
        <button type="button" aria-label="移除内容" @click="removeContext(index)">×</button>
      </span>
    </div>
    <div class="bar-row">
    <div class="add-wrap">
      <button
        type="button"
        class="add"
        :class="{ open: addOpen, active: pendingContexts.length }"
        aria-label="添加附件或引用"
        :aria-expanded="addOpen"
        title="添加附件或引用"
        @click="addOpen = !addOpen"
      >
        <YbIcon name="plus" :size="16" />
      </button>
      <div v-if="addOpen" class="add-menu" role="menu" aria-label="添加内容">
        <button type="button" role="menuitem" @click="openAdd('attachment')">
          <strong>附件</strong><small>文件或图片</small>
        </button>
        <button type="button" role="menuitem" @click="openAdd('file')">
          <strong>项目文件</strong><small>@ 搜索引用（也可直接打 @）</small>
        </button>
        <button type="button" role="menuitem" @click="openAdd('reference')">
          <strong>引用</strong><small>当前会话上下文</small>
        </button>
      </div>
      <input ref="fileRef" class="file-input" type="file" @change="onFileChange" />
    </div>
    <!-- @ 文件引用浮层：锚定输入区向上展开；↑↓ 导航 / Enter 选中 / Esc 关闭 -->
    <div class="text-wrap">
      <div v-if="atOpen" class="at-menu" role="listbox" aria-label="文件引用候选">
        <div v-if="!atItems.length" class="at-empty">
          {{ atRoot ? "无匹配" : "请先在 coding 面板选择项目目录" }}
        </div>
        <div
          v-for="(f, i) in atItems"
          :key="f.rel"
          class="at-item"
          :class="{ sel: i === atIdx }"
          role="option"
          :aria-selected="i === atIdx"
          @mousedown.prevent="pickAt(f)"
        >{{ f.rel }}</div>
      </div>
      <textarea
        ref="inputRef"
        v-model="text"
        rows="1"
        :placeholder="placeholder"
        title="Shift+回车换行"
        @keydown.enter.exact.prevent="onEnter"
        @keydown="onAtKeydown"
        @compositionstart="onCompStart"
        @compositionend="onCompEnd"
        @input="onTextInput"
        @paste="onPaste"
      ></textarea>
    </div>
    <button
      type="button"
      class="mic"
      :class="{ listening }"
      :aria-label="listening ? '聆听中，点击取消' : '语音输入'"
      :title="listening ? '聆听中，点击取消' : '语音输入'"
      @click="onMic"
    >
      <span v-if="listening" class="wave"><i /><i /><i /></span>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" class="icon">
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0" />
        <line x1="12" y1="19" x2="12" y2="22" />
      </svg>
    </button>
    <button
      type="button"
      class="main"
      :class="{ stopping }"
      :disabled="!stopping && !canSend"
      :aria-label="stopping ? '打断（停止生成与播报）' : '发送'"
      :title="stopping ? '打断' : '发送'"
      @click="onMain"
    >
      <Transition name="swap" mode="out-in">
        <svg v-if="stopping" key="stop" viewBox="0 0 24 24" fill="currentColor" class="icon">
          <rect x="6" y="6" width="12" height="12" rx="2.5" />
        </svg>
        <svg v-else key="send" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
          stroke-linecap="round" stroke-linejoin="round" class="icon">
          <line x1="12" y1="19" x2="12" y2="5" />
          <polyline points="5 12 12 5 19 12" />
        </svg>
      </Transition>
    </button>
    </div>
  </form>
</template>

<style scoped>
.bar {
  position: relative;
  display: flex;
  flex-direction: column;                 /* chips 行在上、输入行在下（无 chips 时视觉与单行一致） */
  align-items: stretch;
  box-sizing: border-box;
  min-height: 46px;
  padding: 5px 5px 5px 7px;
  border-radius: 24px;                        /* 更高的对话胶囊 */
  background: var(--yb-glass);                /* 毛玻璃（统一浮层质感） */
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-surface-border);
  /* 双层阴影：下层弥散浮起，上层锐边描立体（视觉稿感） */
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.bar-row {
  display: flex;
  gap: 5px;
  align-items: center;
}
.bar:focus-within {
  border-color: var(--yb-accent);
  box-shadow:
    0 1px 2px rgba(0, 0, 0, 0.04),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
  /* focus ring：原用 box-shadow 0 0 0 3px spread，但 spread 在某些 WebView
   * 上从 padding-box 渲染，画在 border 内侧与 1px border 叠出"内圈"。
   * outline 明确从 border-box 外侧绘制且跟随 border-radius，无 inset 风险。 */
  outline: 2px solid var(--yb-accent-soft);
  outline-offset: 1px;
}
/* 输入区容器：@ 引用浮层的定位锚（向上展开） */
.text-wrap {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}
.at-menu {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 0;
  right: 0;
  max-height: 220px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow: var(--yb-shadow-soft);
  z-index: 25;
}
.at-item {
  padding: 6px 8px;
  border-radius: var(--yb-radius-sm);
  font-size: 12px;
  color: var(--yb-text);
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.at-item.sel {
  background: var(--yb-row-hover);
}
.at-empty {
  padding: 7px 9px;
  font-size: 11px;
  color: var(--yb-text-faint);
}
textarea {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  /* 去 native control 描边：macOS WKWebView 即便 border:none 仍会留 -webkit-appearance
   * 默认的「凹槽」内边框，与外层 .bar 描边叠出双圈。appearance:none 一并清掉。
   * 显式 box-shadow:none 再防一层 UA inset 残留。 */
  -webkit-appearance: none;
  appearance: none;
  box-shadow: none;
  font-size: 14px;                            /* 13.5 → 14 更清晰 */
  outline: none;
  color: var(--yb-text);
  resize: none;                               /* 去右下角拖拽柄 */
  overflow-x: hidden;
  overflow-y: auto;                           /* 超上限滚动 */
  line-height: 1.45;
  padding: 7px 0;                             /* 单行时垂直居中接近原 input */
  font-family: inherit;
}
textarea::placeholder {
  color: var(--yb-text-dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* 滚动条细化（多行输入时可见） */
textarea::-webkit-scrollbar {
  width: 4px;
}
textarea::-webkit-scrollbar-thumb {
  background: var(--yb-text-faint);
  border-radius: 2px;
}
.add-wrap {
  position: relative;
  flex: none;
}
.add,
.mic,
.main {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  border-radius: 50%;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.add {
  border: 1px solid transparent;
  background: transparent;
  color: var(--yb-accent);
}
.add:hover,
.add.open,
.add.active {
  background: var(--yb-surface-2);
  color: var(--yb-accent);
}
.add.active {
  box-shadow: inset 0 0 0 1px var(--yb-accent-soft);
}
.add-menu {
  position: absolute;
  left: -2px;
  bottom: calc(100% + 9px);
  width: 170px;
  padding: 5px;
  display: grid;
  gap: 2px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow: var(--yb-shadow-soft);
  z-index: 20;
}
.add-menu button {
  display: grid;
  gap: 1px;
  padding: 7px 9px;
  border: none;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text);
  text-align: left;
  cursor: pointer;
}
.add-menu button:hover {
  background: var(--yb-row-hover);
}
.add-menu strong {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
}
.add-menu small {
  color: var(--yb-text-faint);
  font-size: 10px;
}
.file-input {
  display: none;
}
.context-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;                    /* 多 chip 换行，独立一行不再与文本输入抢宽度 */
  gap: 4px;
  padding: 2px 4px 6px;
}
.context-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 170px;
  padding: 4px 7px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.context-chip button {
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  cursor: pointer;
  line-height: 1;
}
.chip-thumb {
  width: 18px;
  height: 18px;
  object-fit: cover;
  border-radius: 4px;
  flex: none;
}
.mic,
.main {
  border: none;
}
.icon {
  width: 14px;                                /* 与 30 按钮协调 */
  height: 14px;
}
.mic {
  background: transparent;
  border: none;
  color: var(--yb-text-dim);
}
.mic:hover {
  background: var(--yb-well);
  color: var(--yb-text);
}
/* 聆听中：红底 + 脉动光环 + 声波动画（明确的「正在听」状态） */
.mic.listening {
  background: var(--yb-danger);
  border-color: transparent;
  color: var(--yb-text-on-accent);
  animation: mic-pulse 1.6s ease-out infinite;
}
@keyframes mic-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--yb-c-red-rgb), 0.35);
  }
  70% {
    box-shadow: 0 0 0 8px rgba(var(--yb-c-red-rgb), 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(var(--yb-c-red-rgb), 0);
  }
}
.wave {
  display: flex;
  align-items: center;
  gap: 2.5px;
  height: 14px;
}
.wave i {
  width: 2.5px;
  height: 5px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-surface-1);
  animation: wave 1s ease-in-out infinite;
}
.wave i:nth-child(2) {
  animation-delay: 0.15s;
}
.wave i:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes wave {
  0%,
  100% {
    height: 5px;
  }
  50% {
    height: 13px;
  }
}
/* 主按钮：常态=发送（主色实底），打断态=失败色浅底；图标交叉淡入淡出切换。
 * 阴影收到最小（之前晕 6px/35% 让按钮视觉上"大且溢出"容器） */
.main {
  border: none;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  box-shadow: 0 1px 2px rgba(var(--yb-c-sky-rgb), 0.25);
}
.main:hover:not(:disabled) {
  background: var(--yb-accent-deep);
}
.main:active:not(:disabled) {
  transform: scale(0.97);
}
.main:disabled {
  opacity: 0.4;
  cursor: default;
  box-shadow: none;
}
.main.stopping {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  box-shadow: none;
  opacity: 1;
}
.swap-enter-active,
.swap-leave-active {
  transition: opacity var(--yb-dur-fast) var(--yb-ease-out), transform var(--yb-dur-fast) var(--yb-ease-out);
}
.swap-enter-from,
.swap-leave-to {
  opacity: 0;
  transform: scale(0.7);
}
.swap-enter-active,
.swap-leave-active {
  display: grid;
  place-items: center;
}
</style>
