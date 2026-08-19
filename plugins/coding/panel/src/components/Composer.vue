<script setup lang="ts">
// Composer(R4 阶段二 T5):输入框 + @ chips + 文件补全 + 粘贴截图 + IME 双守卫。
// 行为逐条对齐 chat.html::2685-2821——
//   :2718-2742 粘贴截图(clipboardData.items 找 image/ → FileReader dataURL →
//     native:save_attachment {data=base64 逗号后, ext} → 绝对路径入 chips + 状态行反馈)
//   :2744-2812 @ 补全(input 触发正则 → coding.files {cwd,q} → 菜单 ≤12 条首条选中;
//     capture keydown:Esc 关菜单 stopPropagation / ↑↓ 循环 / Enter 插入带 IME 守卫;
//     插入按触发时缓存位 start..caret 切删,文件成 chip 不留在文本)
//   :2816-2821 发送键(↵ 发送 / ⇧↵ 换行;IME 双守卫 isComposing + compositionend 50ms 窗)
// 与原两处结构差异:
//   ① 原 capture 菜单一处理器 + bubble 发送一处理器经 defaultPrevented 串联;本组件合并为
//     单一 keydown(菜单消费的键提前 return),同元素同目标行为等价。
//   ② textarea 非受控(无 v-model/:value):Vue 在 IME 组字期回写 value 会炸组合(vModelText
//     有 el.composing 守卫,裸绑定没有);atInsert/clear/doSend 直读写 DOM,与原一致。
// refs 由本组件收集,send 事件上抛;发送成功/入队由 App 调 clear() 消费(失败保留 prompt+refs
// 可重试,对齐原 send() 的清理时机)。busy 仅反映 store 态(中断钮现身),发送不锁——busy 期
// send 照常上抛,由 App onSend 过同组校验后 store.queueInput 排队(T4:原 takeover-input
// 排队路径并入 onSend)。多工位:每工位一个本组件实例,聚焦者的 footer 经 CSS 停靠页底。
import { reactive, ref, watch } from "vue";
import { hasBridge, invoke } from "../lib/bridge";
import { emsg } from "../lib/format";
import { basename, matchAtQuery, pushRef } from "../lib/refs";
import AtRefsChips from "./AtRefsChips.vue";

const props = defineProps<{
  busy: boolean;
  cwd: string;
  // 中断受理信号(对齐 RunPill 的 onStop):受理 true / 拒理或失败 false——
  // sending 窗 App 拒理(会话 id 未回填是死点击),false 时中断钮立即重新解锁
  onStop: () => Promise<boolean>;
}>();
const emit = defineEmits<{
  send: [text: string, refs: string[]];
  status: [text: string, err: boolean]; // 瞬时状态行(截图反馈),由 App keys-row 状态位展示
}>();

const taEl = ref<HTMLTextAreaElement | null>(null);
const refs = ref<string[]>([]);

// ---- @ 补全(对齐 atState):caret = 触发时光标位,atInsert 切删用它而不用 live 光标
//      (菜单开着挪光标再 Enter 会切错) ----
const at = reactive({ open: false, items: [] as string[], idx: 0, query: "", start: -1, caret: 0 });
// compositionend 时间戳 50ms 窗(WebKit bug 165004:compositionend 先于确认 Enter 的 keydown,
// 其 isComposing=false,单靠 e.isComposing 会穿透)——发送与 @ 菜单两处 Enter 共用
let lastCompEnd = 0;
const imeActive = (e: KeyboardEvent) => e.isComposing || Date.now() - lastCompEnd < 50;

function closeAt() { at.open = false; }

function queryFiles() {
  if (!hasBridge) return;
  const cwd = props.cwd.trim();
  if (!cwd) return;
  invoke<{ files?: Array<{ rel: string }> }>("coding.files", { cwd, q: at.query })
    .then((r) => {
      at.items = ((r && r.files) || []).map((f) => String(f.rel)).slice(0, 12); // 最多 12 条
      at.idx = 0; // 首条选中
      at.open = true; // 空结果也开:菜单显「无匹配」
    })
    .catch(() => closeAt());
}

function onInput() {
  const ta = taEl.value;
  if (!ta) return;
  const pos = ta.selectionStart ?? ta.value.length;
  const m = matchAtQuery(ta.value.slice(0, pos));
  if (m) {
    at.start = m.start; at.caret = pos; at.query = m.query;
    queryFiles();
  } else if (at.open) closeAt();
}

function atInsert(rel: string) {
  const ta = taEl.value;
  // 删掉触发菜单的 @query 片段(start..caret 用触发时缓存位),文件以 chip 上 chips 行
  if (ta) ta.value = ta.value.slice(0, at.start) + ta.value.slice(at.caret);
  refs.value = pushRef(refs.value, rel); // 去重:同文件重复引用无意义
  closeAt();
  if (ta) ta.focus();
}

function onKeydown(e: KeyboardEvent) {
  // 菜单打开时优先消费导航键(对齐原 capture 处理器;消费的键 return,不再落发送路径)
  if (at.open) {
    if (e.key === "Escape") { closeAt(); e.stopPropagation(); return; } // 防误触全局 esc 停止
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!at.items.length) return; // 「无匹配」态:光标不挪
      at.idx = (at.idx + (e.key === "ArrowDown" ? 1 : -1) + at.items.length) % at.items.length;
      return;
    }
    if (e.key === "Enter" && at.items.length && !imeActive(e)) {
      e.preventDefault(); e.stopPropagation();
      atInsert(at.items[at.idx]!);
      return;
    }
  }
  // 发送:↵ 发送 / ⇧↵ 换行;IME 组字中的 Enter 只是上屏,绝不触发发送(双守卫)
  if (e.key !== "Enter" || e.shiftKey || imeActive(e)) return;
  e.preventDefault();
  doSend();
}

function doSend() {
  // busy 不拦(T4):busy 期入队/空闲直发由 App onSend 判(校验拒发/排队提示都在那一处)
  const ta = taEl.value;
  emit("send", ta ? ta.value : "", [...refs.value]);
}

// ---- 粘贴截图:剪贴板图片落盘成 @ chip(CC 可按路径直接读图);非图片走默认文本粘贴 ----
function attachImage(file: File) {
  const rd = new FileReader();
  rd.onload = () => {
    const url = String(rd.result || "");
    const ext = (file.type && file.type.split("/")[1]) || "png";
    invoke<unknown>("native:save_attachment", { data: url.slice(url.indexOf(",") + 1), ext })
      .then((p) => {
        if (p) {
          refs.value = pushRef(refs.value, String(p));
          emit("status", "已引用截图（" + basename(String(p)) + "）", false);
        }
      })
      .catch((e) => emit("status", "截图保存失败：" + emsg(e), true));
  };
  rd.readAsDataURL(file);
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items || !hasBridge) return;
  for (let i = 0; i < items.length; i++) {
    const it = items[i]!;
    if (it.type && it.type.indexOf("image/") === 0) {
      const file = it.getAsFile();
      if (!file) continue;
      e.preventDefault(); // 图片成 chip,不进文本
      attachImage(file);
    }
  }
}

// ---- 中断:busy 期现身;点击上锁,仅受理后保持——拒理(sending 窗死点击)/失败(false)
//      立即重新解锁(对齐 RunPill clickStop;修复 T5 评审:sending 窗 no-op 点击上锁后
//      锁存过整个窗口,streaming 起跑后钮已废) ----
const stopArmed = ref(false);
watch(() => props.busy, (b) => { if (!b) stopArmed.value = false; });
function onStop() {
  if (stopArmed.value || !props.busy) return;
  stopArmed.value = true;
  Promise.resolve(props.onStop())
    .then((ok) => { if (!ok) stopArmed.value = false; })
    .catch(() => { stopArmed.value = false; });
}

function removeRef(i: number) { refs.value.splice(i, 1); }

// App 用:发送成功消费 prompt+chips(对齐原 invoke.then 的清空时机);空 prompt 校验聚焦
function clear() {
  if (taEl.value) taEl.value.value = "";
  refs.value = [];
  closeAt();
}
function focus() { taEl.value?.focus(); }
/** handoff 草稿随迁（spec §C）：壳侧译宝条草稿填入——已有残稿换行追加（不覆盖用户输入），填完聚焦 */
function fillDraft(text: string) {
  const ta = taEl.value;
  if (!ta) return;
  ta.value = ta.value.trim() ? ta.value.replace(/\s+$/, "") + "\n" + text : text;
  ta.focus();
}
defineExpose({ clear, focus, fillDraft });
</script>

<template>
  <!-- 段①:输入框;.prompt-wrap 是 @ 补全菜单的定位锚(向上展开) -->
  <div class="prompt-wrap">
    <AtRefsChips :refs="refs" @remove="removeRef" />
    <textarea
      id="prompt"
      ref="taEl"
      placeholder="输入消息，@ 引用文件…"
      rows="1"
      @input="onInput"
      @keydown="onKeydown"
      @compositionend="lastCompEnd = Date.now()"
      @paste="onPaste"
    ></textarea>
    <div v-if="at.open" id="at-menu" class="at-menu">
      <div v-if="!at.items.length" class="at-empty">无匹配</div>
      <div
        v-for="(rel, i) in at.items"
        :key="rel"
        class="at-item"
        :class="{ sel: i === at.idx }"
        @mousedown.prevent="atInsert(rel)"
      >{{ rel }}</div>
    </div>
  </div>
  <!-- 段②:上下文行——T6 头部控件槽位(cwd chip + 浮层 / mode pill / 引擎 chip + picker);
       .ctx-row 是 cwd 浮层的定位锚(position:relative,同原 chat.html) -->
  <div class="ctx-row"><slot name="ctx"></slot></div>
  <!-- 段③:操作行(验收样式收敛:kbd 提示簇退役,快捷键收进按钮 title)——
       左状态位(App 经 slot 提供:store 状态行 + 本组件瞬时提示共一位),
       右操作钮(中断 ghost busy 期现身 / 发送 accent 主钮 busy 期
       不禁用——点击/↵ 入队,状态行提示「已排队…」) -->
  <div class="keys-row">
    <slot name="status"></slot>
    <button v-if="busy" id="stop" class="act ghost" type="button" title="中断当前运行(esc)" :disabled="stopArmed" @click="onStop">中断</button>
    <button id="send" class="act" type="button" title="发送(↵) · 换行(⇧↵) · @ 引用文件" @click="doSend">发送</button>
  </div>
</template>
