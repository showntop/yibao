<script setup lang="ts">
import { ref, computed, watch, watchEffect, onMounted, nextTick } from "vue";
import { invoke } from "@tauri-apps/api/core";
import YbIcon from "./YbIcon.vue";
import { sessionStore } from "../../state/store";
import { getConversationMessages, searchFiles, pickFolder } from "../../services/brainClient";
import { listPlugins } from "../../lib/brain";
import { parseAtTrigger, stripAtTrigger, type InputContext } from "../../lib/at-mention";
import type { ConversationMeta } from "../../state/types";
import {
  parseSlashTrigger,
  stripSlashTrigger,
  filterSlashCommands,
  BUILTIN_SLASH_COMMANDS,
  pluginSlashCommands,
  type SlashCmd,
} from "../../lib/slash";
import { inputMenuOpen, addMenuOpen } from "../../lib/input-menu";

// busy = 生成/播报中（可打断）；listening = 录音中（麦克风切声波态，点击=取消录音）
// draft = 外部预填草稿（主屏 Feed 点击带上下文来）；变化即填入并聚焦
const props = withDefaults(
  defineProps<{
    busy?: boolean;
    listening?: boolean;
    draft?: string;
    placeholder?: string;
    /** 多行展开的最大高度（px）；小窗（桌宠脚下）给更小值防溢出 */
    growMax?: number;
    /** compact 模式（桌宠 QuickPanel）：加号菜单改向下展开，避开桌宠小窗向上被截 */
    compact?: boolean;
  }>(),
  { placeholder: "对译宝说点什么…（shift+回车换行）", growMax: 140, compact: false },
);
const emit = defineEmits<{
  (e: "submit", text: string, contexts: InputContext[]): void;
  (e: "mic"): void;
  (e: "interrupt"): void;
  /** /命令 local 动作（截图/新建会话/打开插件…），由父窗口分发 */
  (e: "slash-local", id: string): void;
  /** /命令 插件动作（api.toml command=true 的直调方法），由父窗口 panelAction */
  (e: "slash-plugin", p: { pluginId: string; method: string }): void;
}>();
const text = ref("");
const inputRef = ref<HTMLTextAreaElement | null>(null);
/** textarea 自适应高度：仅多行（含换行符）随内容增高；单行固定一行高度 */
function autoGrow() {
  const el = inputRef.value;
  if (!el) return;
  if (!text.value.includes("\n")) {
    el.style.height = "";
    return;
  }
  el.style.height = "auto";
  el.style.height = `${Math.min(el.scrollHeight, props.growMax)}px`; // 上限防过高（小窗更小）
}
const fileRef = ref<HTMLInputElement | null>(null);
const addOpen = ref(false);
const pendingContexts = ref<InputContext[]>([]);
const canSend = computed(() => text.value.trim().length > 0);
/** 多行判定：仅当文本含换行符（shift+回车 手打换行）才展开成两行及以上，否则保持单行 */
const isMulti = computed(() => text.value.includes("\n"));

// 草稿暂存：写入 SessionStore.conversation（按活动会话），300ms trailing debounce 避免高频写
let draftTimer: ReturnType<typeof setTimeout> | null = null;
function persistDraft(v: string) {
  if (draftTimer) clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    const id = sessionStore.conversation.getActiveConversationId();
    if (id) sessionStore.conversation.setDraft(id, v);
  }, 300);
}
onMounted(() => {
  const id = sessionStore.conversation.getActiveConversationId();
  if (id) {
    const saved = sessionStore.conversation.getUIState(id).draft;
    if (saved) text.value = saved;
  }
  // / 命令的插件动作项（list_plugins 的 commands）
  void loadPluginCommands();
});
// takeDraft 清屏也会触发本 watch——那次由 takeDraft 写穿落库,这里跳过一次,避免重排 debounce 二次写
let skipDraftPersist = false;
watch(text, (v) => {
  if (skipDraftPersist) { skipDraftPersist = false; return; }
  persistDraft(v);
});

// ---- @ 文件引用（chips 化）：输入 @ 触发文件搜索浮层，选中成 file chip 进 pendingContexts。
//      搜索 = 原生命令 search_files（Rust 侧，与 coding 插件彻底解耦）；
//      搜索根 = sticky 上次 @ 目录（localStorage）→ 无则引导 pick_folder 原生目录选择器选一次。
//      产品语义：@ = 选一个目录，在里面搜文件引用；与编码会话零关系。 ----
const AT_ROOT_KEY = "yibao.atRoot";
const atOpen = ref(false);
const atItems = ref<{ rel: string }[]>([]);
const atIdx = ref(0);
const atRoot = ref("");
let atRootResolved = false; // 已尝试解析过（含无记忆）：避免每次输入 @ 都重查/弹目录选择器
let atStart = -1;
let atCaret = 0;
let atSeq = 0; // 防乱序：逐键查询的慢响应到达时已过期则丢弃

// ---- 最近会话引用浮层（加号菜单「引用」）：从 SessionStore 列出最近会话（排除当前活跃），
//      选中成 reference chip；发送时 InputBar 拉取该会话消息展开成上下文前缀 ----
const refOpen = ref(false);
const refItems = ref<ConversationMeta[]>([]);
const refIdx = ref(0);
let refSeq = 0;

// ---- / 命令菜单（与 @ 文件引用同款浮层范式）：内置注册表 + 插件动作（list_plugins commands）----
const slashOpen = ref(false);
const slashAll = ref<SlashCmd[]>(BUILTIN_SLASH_COMMANDS); // 内置 + 插件（异步合并）
const slashItems = ref<SlashCmd[]>([]);
const slashIdx = ref(0);
let slashStart = -1;
let slashCaret = 0;
let slashSeq = 0;

/** 拉插件命令（list_plugins 的 commands → 插件动作命令）；失败静默，只留内置命令 */
async function loadPluginCommands() {
  try {
    const plugins = await listPlugins();
    slashAll.value = [...BUILTIN_SLASH_COMMANDS, ...pluginSlashCommands(plugins)];
  } catch { /* 插件命令可选 */ }
}

function closeSlash() {
  slashOpen.value = false;
  slashSeq++; // 作废在途响应
}

async function slashQuery(q: string) {
  const seq = ++slashSeq;
  slashItems.value = filterSlashCommands(slashAll.value, q);
  if (seq !== slashSeq) return;
  slashIdx.value = 0;
  // 同步计算 maxHeight：避开 watch 的 pre-flush 时序依赖，保证菜单首次出现时已就绪
  // （否则 CSS 兜底 360 让菜单按内容自然高度渲染，超出窗口底被视口裁、无滚动条、下方条目不可点）
  updateMenuMaxH();
  slashOpen.value = true;
}

/** 选中命令：template=展开模板进输入框（光标落参数位）；local/plugin=清空输入、上抛父窗口执行 */
function pickSlash(cmd: SlashCmd) {
  closeSlash();
  if (cmd.kind === "template" && cmd.template) {
    const idx = cmd.template.indexOf("{p}");
    const expanded = idx >= 0
      ? cmd.template.slice(0, idx) + cmd.template.slice(idx + 3)
      : cmd.template;
    text.value = stripSlashTrigger(text.value, slashCaret, slashStart) + expanded;
    const caretPos = text.value.length - expanded.length + (idx >= 0 ? idx : expanded.length);
    persistDraft(text.value);
    nextTick(() => {
      autoGrow();
      inputRef.value?.focus();
      inputRef.value?.setSelectionRange(caretPos, caretPos);
    });
    return;
  }
  text.value = "";
  persistDraft("");
  nextTick(() => autoGrow());
  if (cmd.kind === "local") emit("slash-local", cmd.local ?? "");
  else if (cmd.kind === "plugin") emit("slash-plugin", { pluginId: cmd.pluginId ?? "", method: cmd.pluginMethod ?? "" });
}

async function ensureAtRoot(): Promise<string> {
  if (atRootResolved) return atRoot.value;
  atRoot.value = localStorage.getItem(AT_ROOT_KEY) || "";
  atRootResolved = true;
  return atRoot.value;
}

/** @ 无搜索根时点「选择目录」：弹原生目录选择器选一次，选中写 sticky 并立即按当前 @ 片段重搜。
 *  取消 → 收起浮层（不反复弹）。 */
async function pickAtRoot() {
  const dir = await pickFolder().catch(() => null);
  if (!dir) { closeAt(); return; }
  atRoot.value = dir;
  localStorage.setItem(AT_ROOT_KEY, dir);
  const el = inputRef.value;
  const caret = el?.selectionStart ?? text.value.length;
  const atT = parseAtTrigger(text.value, caret);
  if (atT) { atStart = atT.start; atCaret = caret; void atQuery(atT.query); }
  else closeAt();
}

async function atQuery(q: string) {
  const seq = ++atSeq;
  const cwd = await ensureAtRoot();
  if (seq !== atSeq) return;
  if (!cwd) { atItems.value = []; atIdx.value = 0; updateMenuMaxH(); atOpen.value = true; return; }
  const data = await searchFiles(cwd, q).catch(() => null);
  if (seq !== atSeq) return;
  atItems.value = (data?.files ?? []).slice(0, 12);
  atIdx.value = 0;
  updateMenuMaxH();
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

// ---- 菜单浮层：绝对定位锚定在 .bar 内的 textarea 上方（bottom: calc(100% + 6px)）。
//      不再 Teleport 到 body + fixed：那种方案在桌宠窗口圆角下右缘被切、视口/容器变换下时序脆弱。
//      改为内嵌后：菜单天然贴 .bar 上方，宽 = .bar 内容宽（左右各收 8px 避 .bar 圆角视觉穿透），
//      高度由 max-height 限制 + overflow-y 滚动。HomeChat 防 skill-row 遮挡靠"input-slot 后渲染在上层"；
//      桌宠防团子遮挡靠 .wb-stack overflow visible + QuickPanel 后渲染 + max-height 卡在 stack 顶。
const menuMaxH = ref(360);
/** 菜单展开方向：优先向下（不遮挡输入上方的内容/对话；桌宠下避开团子、覆盖下方 dock 按钮），
 *  输入框贴底（大窗）时自动回退向上。 */
const menuPlacement = ref<"up" | "down">("up");

function updateMenuMaxH() {
  const ta = inputRef.value;
  if (!ta) return;
  const bar = ta.closest(".bar") as HTMLElement | null;
  const r = (bar ?? ta).getBoundingClientRect();
  const vh = window.innerHeight;
  // 优先向下展开：桌宠下输入框下方到窗口底空间通常够 5-6 条，且不遮挡上方团子/内容；
  // 下方不足（大窗输入框贴底）才向上。maxHeight = 实际可用空间，溢出则菜单内滚动。
  const spaceDown = vh - r.bottom - 6;
  const spaceUp = r.top - 8;
  const useDown = spaceDown >= 96 || spaceDown >= spaceUp;
  menuPlacement.value = useDown ? "down" : "up";
  menuMaxH.value = Math.min(360, Math.max(48, useDown ? spaceDown : spaceUp));
}

// 派生共享的"菜单打开"状态（任一菜单开着 = true）：桌宠 QuickPanel 用它暂停 hover 自动收起。
// 用 watchEffect 自动派生，避免 slashQuery/atQuery/closeSlash/closeAt/closeRef 多处手动同步。
watchEffect(() => {
  inputMenuOpen.value = slashOpen.value || atOpen.value || refOpen.value;
  addMenuOpen.value = addOpen.value;
});

// watch 保留作为兜底（防止 updateMenuMaxH 在菜单首次出现时漏跑）
watch([slashOpen, atOpen], ([s, a]) => {
  if (s || a) updateMenuMaxH();
});

function onTextInput() {
  autoGrow();
  if (addOpen.value) addOpen.value = false; // 输入文字（含 /、@ 触发）即收加号菜单
  if (refOpen.value) closeRef(); // 引用会话浮层同理
  const el = inputRef.value;
  const caret = el?.selectionStart ?? text.value.length;
  const atT = parseAtTrigger(text.value, caret);
  const slashT = parseSlashTrigger(text.value, caret);
  // @ 与 / 互斥：以「触发片段 start 更靠后」的为准（用户最后输入的那个触发符）
  if (slashT && (!atT || slashT.start >= atT.start)) {
    if (atOpen.value) closeAt();
    slashStart = slashT.start;
    slashCaret = caret;
    void slashQuery(slashT.query);
    return;
  }
  closeSlash();
  if (!atT) { if (atOpen.value) closeAt(); return; }
  atStart = atT.start;
  atCaret = caret;
  void atQuery(atT.query);
}

/** 命令/文件菜单的键盘导航（Escape/↑↓）；Enter 选中由 onEnter 统一处理（含 IME 守卫） */
function onMenuKeydown(e: KeyboardEvent) {
  if (slashOpen.value) {
    if (e.key === "Escape") { closeSlash(); e.stopPropagation(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!slashItems.value.length) return;
      slashIdx.value = (slashIdx.value + (e.key === "ArrowDown" ? 1 : -1) + slashItems.value.length) % slashItems.value.length;
    }
    return; // 菜单开着时其余键照常输入
  }
  if (atOpen.value) {
    if (e.key === "Escape") { closeAt(); e.stopPropagation(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!atItems.value.length) return;
      atIdx.value = (atIdx.value + (e.key === "ArrowDown" ? 1 : -1) + atItems.value.length) % atItems.value.length;
    }
    return;
  }
  if (refOpen.value) {
    if (e.key === "Escape") { closeRef(); e.stopPropagation(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!refItems.value.length) return;
      refIdx.value = (refIdx.value + (e.key === "ArrowDown" ? 1 : -1) + refItems.value.length) % refItems.value.length;
    }
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

/** 展开引用会话内容：拉取该会话消息拼成上下文前缀（AI 真正读到被引用会话的对话）。
 *  拉取失败/无消息 → 降级为仅标注标题，不阻断发送。 */
async function expandRefContent(c: InputContext): Promise<string> {
  const title = c.label || "最近会话";
  const id = c.refId;
  if (!id) return `【引用：${title}】`;
  const rows = await getConversationMessages(id, 30).catch(() => null);
  if (!rows?.length) return `【引用会话「${title}」（无内容）】`;
  const body = rows
    .filter((m) => m.payload?.text)
    .map((m) => `${m.role === "user" ? "用户" : "助手"}：${m.payload.text}`)
    .join("\n")
    .slice(0, 4000); // 防超长会话把上下文撑爆
  return `【引用会话「${title}」】\n${body}`;
}

async function send() {
  const t = text.value.trim();
  if (!t) return;
  closeAt();   // @ 浮层随发送收敛（Enter 已被浮层拦截，这里管发送钮路径）
  closeSlash(); // / 命令浮层同理
  closeRef();  // 引用会话浮层同理
  // AI 正在生成/播报（stopping）时发送 = 先打断再发新消息（不必手动"停止"）
  if (stopping.value) emit("interrupt");
  // 先同步清空输入与 chips（引用内容展开是异步的，防止展开期间再点发送造成重复提交）
  const contexts = pendingContexts.value.slice();
  text.value = "";
  pendingContexts.value = [];
  persistDraft("");
  nextTick(() => autoGrow()); // 清空后高度回落（nextTick 等 v-model 生效）
  // 引用会话在 InputBar 内展开成上下文前缀（不留给父组件 formatContextPrefix——
  // 那只是标题标注，AI 读不到内容）；附件/@文件照常交给父组件拼路径前缀
  let refPrefix = "";
  const rest: InputContext[] = [];
  for (const c of contexts) {
    if (c.kind === "reference") refPrefix += `\n${await expandRefContent(c)}`;
    else rest.push(c);
  }
  emit("submit", refPrefix.trimStart() + t, rest);
}

/** 加号菜单 toggle：与 @ / 命令/引用浮层互斥——打开加号前收掉其余浮层 */
function onAddToggle() {
  if (!addOpen.value) {
    closeSlash();
    closeAt();
    closeRef();
  }
  addOpen.value = !addOpen.value;
}

/** 加号菜单动作（v2 收敛）：附件=本地文件/图片；引用=最近会话（项目文件统一走 @ 手势） */
function openAdd(kind: "attachment" | "reference") {
  addOpen.value = false;
  if (kind === "attachment") {
    fileRef.value?.click();
    return;
  }
  // 引用最近会话：列出会话浮层（排除当前活跃；会话 id 发送时展开内容）
  // store 可能未 hydrate（如桌宠 QuickPanel 首开）：先兜底 restore 再列
  void sessionStore.restore().catch(() => {}).then(() => {
    const seq = ++refSeq;
    const activeId = sessionStore.conversation.getActiveConversationId();
    refItems.value = sessionStore.conversation
      .listConversations()
      .filter((c) => c.id !== activeId)
      .slice(0, 12);
    if (seq !== refSeq) return;
    refIdx.value = 0;
    updateMenuMaxH();
    refOpen.value = true;
  });
}

function closeRef() {
  refOpen.value = false;
  refSeq++; // 作废在途
}

function pickRef(c: ConversationMeta) {
  const label = c.title?.trim() || "新对话";
  if (!pendingContexts.value.some((item) => item.kind === "reference" && item.refId === c.id)) {
    pendingContexts.value.push({ kind: "reference", label, refId: c.id });
  }
  closeRef();
  nextTick(() => { autoGrow(); inputRef.value?.focus(); });
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
  // / 命令菜单打开时 Enter = 选中命令（无候选则关菜单），不发送
  if (slashOpen.value) {
    if (slashItems.value.length) pickSlash(slashItems.value[slashIdx.value]);
    else closeSlash();
    return;
  }
  // @ 浮层打开时 Enter = 选中候选（无候选则关浮层），不发送
  if (atOpen.value) {
    if (atItems.value.length) pickAt(atItems.value[atIdx.value]);
    else closeAt();
    return;
  }
  // 引用会话浮层打开时 Enter = 选中会话（无候选则关浮层），不发送
  if (refOpen.value) {
    if (refItems.value.length) pickRef(refItems.value[refIdx.value]);
    else closeRef();
    return;
  }
  void send();
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
        <span class="chip-label" :data-full="`${kindLabel(context)} · ${context.label}`">{{ kindLabel(context) }} · {{ context.label }}</span>
        <button type="button" aria-label="移除内容" @click="removeContext(index)">×</button>
      </span>
    </div>
    <div class="bar-row" :class="{ multi: isMulti }">
    <div class="add-wrap">
      <button
        type="button"
        class="add"
        :class="{ open: addOpen, active: pendingContexts.length }"
        aria-label="添加附件或引用"
        :aria-expanded="addOpen"
        title="添加附件或引用"
        @click="onAddToggle"
      >
        <YbIcon name="plus" :size="16" />
      </button>
      <div v-if="addOpen" class="add-menu" :class="{ down: props.compact }" role="menu" aria-label="添加内容">
        <button type="button" role="menuitem" @click="openAdd('attachment')">
          <strong>附件</strong><small>文件或图片</small>
        </button>
        <button type="button" role="menuitem" @click="openAdd('reference')">
          <strong>引用</strong><small>最近会话（带上它的内容）</small>
        </button>
        <div class="add-hint">文件引用：输入 <b>@</b> 搜索</div>
      </div>
      <input ref="fileRef" class="file-input" type="file" @change="onFileChange" />
    </div>
    <!-- @ 文件引用浮层：锚定 .bar 向上展开（绝对定位），↑↓ 导航 / Enter 选中 / Esc 关闭 -->
    <!-- / 命令浮层：同 @ 浮层范式，绝对定位在 .bar 上方；内置命令 + 插件动作 -->
    <!-- 引用最近会话浮层：同浮层范式，列出最近会话（排除当前活跃） -->
    <div v-if="refOpen" class="at-menu" :class="menuPlacement" :style="{ maxHeight: menuMaxH + 'px' }" role="listbox" aria-label="引用最近会话">
      <div v-if="!refItems.length" class="at-empty">暂无其他会话可引用</div>
      <div
        v-for="(c, i) in refItems"
        :key="c.id"
        class="at-item ref-item"
        :class="{ sel: i === refIdx }"
        role="option"
        :aria-selected="i === refIdx"
        @mousedown.prevent="pickRef(c)"
      >
        <YbIcon name="chat" :size="12" />
        <span class="ref-title">{{ c.title?.trim() || "新对话" }}</span>
        <span class="ref-preview">{{ c.preview }}</span>
      </div>
    </div>
    <div v-if="atOpen" class="at-menu" :class="menuPlacement" :style="{ maxHeight: menuMaxH + 'px' }" role="listbox" aria-label="文件引用候选">
      <!-- 搜索根栏：显示当前引用目录（可更换）——@ 是目录内搜索，根必须可见可改 -->
      <div v-if="atRoot" class="at-root">
        <YbIcon name="doc" :size="12" />
        <span class="at-root-path" :title="atRoot">{{ atRoot }}</span>
        <button type="button" class="at-root-switch" @mousedown.prevent="pickAtRoot">更换</button>
      </div>
      <div v-if="!atItems.length" class="at-empty">
        <template v-if="atRoot">无匹配</template>
        <template v-else>
          <span>还没有选择目录</span>
          <button type="button" class="at-pick" @mousedown.prevent="pickAtRoot">选择目录…</button>
        </template>
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
    <div v-if="slashOpen" class="at-menu" :class="menuPlacement" :style="{ maxHeight: menuMaxH + 'px' }" role="listbox" aria-label="命令候选">
      <div v-if="!slashItems.length" class="at-empty">无匹配命令</div>
      <div
        v-for="(c, i) in slashItems"
        :key="c.id"
        class="at-item slash-item"
        :class="{ sel: i === slashIdx }"
        role="option"
        :aria-selected="i === slashIdx"
        @mousedown.prevent="pickSlash(c)"
      >
        <YbIcon :name="c.icon" :size="12" />
        <span class="slash-kw">/{{ c.keyword }}</span>
        <span class="slash-label">{{ c.label }}</span>
        <span class="slash-desc">{{ c.desc }}</span>
      </div>
    </div>
    <div class="text-wrap">
      <textarea
        ref="inputRef"
        v-model="text"
        rows="1"
        :class="{ nowrap: !isMulti }"
        :placeholder="placeholder"
        title="Shift+回车换行"
        @mousedown="addOpen = false"
        @keydown.enter.exact.prevent="onEnter"
        @keydown="onMenuKeydown"
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
  border-radius: 24px;
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-glaze-hi), var(--yb-glaze-edge),
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.06),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
  transition: border-color var(--yb-dur-fast) var(--yb-ease-out), box-shadow var(--yb-dur-fast) var(--yb-ease-out);
}
.bar-row {
  display: flex;
  gap: 5px;
  row-gap: 0;                        /* 多行时 textarea 与按钮行零垂直间距，布局更紧凑 */
  align-items: center;
  flex-wrap: wrap;                   /* 多行时 textarea 占满首行后自然换行；单行不触发 */
}
/* 多行（shift+回车后）布局：textarea 用 100% basis 独占首行换到上方，
 * 按钮行留在下方，+ 号 margin-right:auto 推到左，语音/发送贴右。
 * align-items: flex-start + text-wrap 显式高度 auto：让每个 item 高度 = 内容高度，
 * 避免被所在 flex line 拉伸造成 textarea 下方/按钮行上下出现多余空白。 */
.bar-row.multi {
  align-items: flex-start;
}
.bar-row.multi .text-wrap {
  flex: 1 1 100%;
  order: 1;
  min-width: 0;
  height: auto;                     /* 锁死内容高度，防止被 line 高度撑开 */
  align-self: flex-start;
}
.bar-row.multi .add-wrap {
  order: 2;
  margin-right: auto;               /* 把后续 mic/main 推到行尾 */
  align-self: center;               /* 按钮在多行时垂直居中（line 高度 = 自身高度，居中即贴满） */
}
.bar-row.multi .mic {
  order: 3;
  align-self: center;
}
.bar-row.multi .main {
  order: 4;
  align-self: center;
}
.bar:focus-within {
  border-color: var(--yb-accent);
  /* 聚焦态保留完整的玻璃阴影（含 --yb-glaze-edge 顶部边缘高光），否则右上角会"切角不齐"；
   * focus ring 改用 outline（从 border-box 外侧绘制，跟随 border-radius），避免 spread 与 1px border 叠出内圈 */
  box-shadow: var(--yb-glaze-hi), var(--yb-glaze-edge),
    0 1px 2px rgba(var(--yb-c-slate-rgb), 0.06),
    0 6px 18px rgba(var(--yb-c-slate-rgb), 0.10);
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
  /* 绝对定位锚定 .bar：left/right 各 8px 收边（避免菜单圆角顶到 .bar 圆角）；
   * 方向由 .up/.down 控制；max-height 由内联 menuMaxH 控制（视可用空间自适应）。 */
  position: absolute;
  left: 8px;
  right: 8px;
  max-height: 360px;
  overflow-y: auto;
  padding: 5px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-glass);
  -webkit-backdrop-filter: var(--yb-blur);
  backdrop-filter: var(--yb-blur);
  box-shadow: var(--yb-shadow-soft);
  z-index: 2147483647; /* 瞬态浮层：绝不被团子/技能 chip 等任何元素遮挡 */
  /* 显式可交互：父链 .wb 是 pointer-events:none（窗口空白穿透），WKWebView 对
   * "父 none + 后代 auto" 组合可能误跳过；这里直接声明，确保菜单项可点选 */
  pointer-events: auto;
}
/* 展开方向：默认向上（输入框上方）；空间不足时向下（覆盖输入框下方区域，如桌宠 dock 按钮） */
.at-menu.up {
  bottom: calc(100% + 6px);
}
.at-menu.down {
  top: calc(100% + 6px);
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
.at-item:hover {
  background: var(--yb-row-hover);
}
.at-empty {
  padding: 7px 9px;
  font-size: 11px;
  color: var(--yb-text-faint);
}
/* 无搜索根时的「选择目录」引导按钮（@ 首次使用；用户显式选根） */
.at-empty .at-pick {
  margin-left: 6px;
  padding: 2px 9px;
  border: none;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  font-size: 11px;
  cursor: pointer;
}
.at-empty .at-pick:hover {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
/* 搜索根栏：@ 浮层顶部显示当前引用目录（可见、可更换） */
.at-root {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 8px 4px;
  border-bottom: 1px solid var(--yb-surface-border);
  margin-bottom: 3px;
  color: var(--yb-text-faint);
  font-size: 10px;
}
.at-root-path {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  direction: rtl; /* 长路径从末尾显示（文件名在右，更可读） */
  text-align: left;
  color: var(--yb-text-dim);
}
.at-root .at-root-switch {
  flex: none;
  padding: 1px 8px;
  border: none;
  border-radius: var(--yb-radius-pill);
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: 10px;
  cursor: pointer;
}
.at-root .at-root-switch:hover {
  background: var(--yb-accent-soft);
}
/* / 命令菜单项：图标 + 命令词（高亮）+ 标签 + 说明（淡化），与文件候选视觉区分；
 * 紧凑行高：桌宠小窗可用空间有限，压薄让更多命令可见 */
.slash-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  font-size: 11px;
}
.slash-item .slash-kw {
  color: var(--yb-accent-deep);
  font-weight: 600;
  flex: none;
}
.slash-item .slash-label {
  color: var(--yb-text);
  flex: none;
}
.slash-item .slash-desc {
  margin-left: auto;
  color: var(--yb-text-faint);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 55%;
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
  box-sizing: border-box;                       /* height = border-box 总高（含 padding），
                                                   autoGrow 用 scrollHeight 直接赋值才不会撑高 */
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
/* 单行模式：禁软折行，长文本横向滚动查看（保持一行不增高） */
textarea.nowrap {
  white-space: nowrap;
  overflow-x: auto;
  overflow-y: hidden;
}
/* 滚动条细化（多行输入/单行横向滚动时可见） */
textarea::-webkit-scrollbar {
  width: 4px;
  height: 4px;
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
  background: var(--yb-surface-2);           /* 常驻浅灰圆底，作为可见按钮 */
  color: var(--yb-accent);
}
.add:hover,
.add.open {
  background: var(--yb-accent-soft);         /* hover/展开淡蓝反馈 */
  color: var(--yb-accent);
}
.add.active {
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  box-shadow: inset 0 0 0 1px var(--yb-accent-soft);
}
.add-menu {
  position: absolute;
  left: -2px;
  bottom: calc(100% + 9px);
  max-height: 60vh;
  overflow-y: auto;
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
/* compact 模式（桌宠小窗）：向上空间不够，改向下展开避开 stack 顶/团子 */
.add-menu.down {
  bottom: auto;
  top: calc(100% + 9px);
}

.add-menu strong {
  font-size: 12px;
  font-weight: var(--yb-fw-medium);
}
.add-menu small {
  color: var(--yb-text-faint);
  font-size: 10px;
}
/* 加号菜单底部提示：项目文件统一走 @ 手势（菜单不重复入口） */
.add-hint {
  margin-top: 3px;
  padding: 6px 9px 3px;
  border-top: 1px solid var(--yb-surface-border);
  color: var(--yb-text-faint);
  font-size: 10px;
}
.add-hint b {
  color: var(--yb-accent-deep);
  font-weight: 600;
}
.file-input {
  display: none;
}
/* 引用最近会话项：图标 + 标题 + 预览（复用 at-item 的基础行高） */
.ref-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.ref-item .ref-title {
  flex: none;
  max-width: 45%;
  color: var(--yb-text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ref-item .ref-preview {
  color: var(--yb-text-faint);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.context-list {
  display: flex;
  align-items: center;
  flex-wrap: wrap;                    /* 多 chip 换行，独立一行不再与文本输入抢宽度 */
  gap: 4px;
  padding: 2px 4px 6px;
}
/* 长文件名（如超长 .md 附件）不能因 max-width + overflow:hidden 把末尾的 × 移除钮裁掉：
 * chip 本体只做收缩容器，文本由内部 .chip-label 单独省略号收缩，× 钮 flex:none 恒可见 */
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
}
.chip-label {
  position: relative;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* hover 悬停显示完整名（省略号看不清时）：attr(data-full) 自带 chip 前缀，贴在 chip 上方 */
.chip-label:hover::after {
  content: attr(data-full);
  position: absolute;
  left: 0;
  bottom: calc(100% + 6px);
  max-width: 360px;
  padding: 5px 9px;
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-solid);
  color: var(--yb-text);
  font-size: 11px;
  line-height: 1.4;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  box-shadow: var(--yb-shadow-2);
  z-index: 10;
  pointer-events: none;
}
.context-chip button {
  flex: none;
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
