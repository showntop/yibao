<script setup lang="ts">
// coding:studio(R4 阶段二 T4):接会话 store + 消息流渲染骨架。
// onInit → store.handleData(attach 接管/流式事件归约全在 store);takeover 标志 → body.takeover
// (隐藏输入区,T8 完整接线)。头部(cwd/引擎/模式/接续)与 Composer 在 T5/T6 接入——此处仅留
// 明确标记的占位:头部 = 标题 + 成本聚合 + 新对话(store 已支持);footer = 状态行 + Composer 占位。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { emitPanelEvent, hasBridge, invoke, onInit } from "./lib/bridge";
import type { PanelData } from "./lib/types";
import { doneStatusText, fmtCost, fmtTok } from "./lib/format";
import { createSessionStore } from "./stores/session";
import MessageList from "./components/MessageList.vue";
import ErrBar from "./components/ErrBar.vue";
import RunPill from "./components/RunPill.vue";

const takeover = ref(false);

const store = createSessionStore({
  invoke: (m, p) => invoke(m, p),
  // takeover-state 上报:仅 takeover 态真正发(store 语义;对齐 chat.html reportState)
  report: (st, hasSession) => {
    if (takeover.value) emitPanelEvent("takeover-state", { state: st, session: hasSession });
  },
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (t) => clearTimeout(t),
  userEchoFallbackMs: 1500,
});
const state = store.state;

// takeover 队列泄放的发送上下文:cwd 由 T6 头部控件落定后更新,先给缺省(对齐 chat.html 初值)
store.setQueueContext({ cwd: "", mode: "acceptEdits", agent: "claude-code" });

// init 回调第二参是完整载荷:takeover 标志随每条 panel_data 重提(旧桥只传 data → undefined 视为 false)
onInit((data, msg) => {
  takeover.value = !!(msg && msg.takeover);
  store.handleData(data as PanelData);
});
// body.takeover 驱动 CSS 隐藏输入区(T8 接 takeover-input/-stop 宿主消息)
watch(takeover, (on) => { document.body.classList.toggle("takeover", on); }, { immediate: true });

// ---- 顶栏:会话成本聚合(C4,对齐 renderCost):tok 与成本都未见 → 空;codex 无 cost → 只 token 段 ----
const costText = computed(() => {
  const u = state.usage;
  if (!u.tok && !u.hasCost) return "";
  return fmtTok(u.tok) + " tok" + (u.hasCost ? " · " + fmtCost(u.cost) : "");
});
const newChatDisabled = computed(() => state.sending || state.streaming || !state.currentSession);

// ---- footer 状态行:提交中/运行中(spinner)/完成行(doneStatusText,isFinite 守御)/错误 ----
const status = computed(() => {
  if (state.sending) return { text: "提交中…", spin: true, err: false };
  if (state.streaming) return { text: (state.runPrefix || "会话") + " 运行中…", spin: true, err: false };
  if (state.error) return { text: state.error, spin: false, err: true };
  if (state.ended === "done") return { text: doneStatusText(state.lastUsage), spin: false, err: false };
  if (state.ended === "stopped") return { text: "已中断", spin: false, err: false };
  return { text: "", spin: false, err: false };
});

// ---- RunPill 布局(C3):bottom = footer 高 + 10 + (errbar 可见时)errbar 高,现算现贴 ----
const footerEl = ref<HTMLElement | null>(null);
const errbarRef = ref<{ root: HTMLElement | null } | null>(null);
const pillBottom = ref(110);
const pillVisible = computed(() => state.sending || state.streaming);

function relayout() {
  void nextTick(() => {
    let b = (footerEl.value ? footerEl.value.offsetHeight : 0) + 10;
    const e = errbarRef.value?.root;
    if (e) b += e.offsetHeight;
    pillBottom.value = b;
  });
}

// errbar 出现/消失(内容变化)→ 重算;详情开合由 ErrBar 的 layout 事件上来
watch(() => state.error, relayout);
watch(pillVisible, relayout); // pill 现身/消失各算一次(对齐 showRunPill/hideRunPill 里的 layoutRunPill)

let ro: ResizeObserver | null = null;
onMounted(() => {
  relayout();
  // footer 换行/字号变化导致高度变 → pill 重新贴合(对齐 window resize 监听;RO 覆盖内容撑变)
  if (typeof ResizeObserver !== "undefined" && footerEl.value) {
    ro = new ResizeObserver(relayout);
    ro.observe(footerEl.value);
  }
  window.addEventListener("resize", relayout);
});
onBeforeUnmount(() => {
  if (ro) ro.disconnect();
  window.removeEventListener("resize", relayout);
});
</script>

<template>
  <!-- 桥缺失时可见,提示这是设计预览 -->
  <div v-if="!hasBridge" id="bridge-warn">设计预览：未检测到译宝桥（window.yibao），起停/流式回显不可用。</div>

  <!-- 头部占位(T6 接入:cwd chip / 引擎 chip / 权限模式 pill / 接续 popover)。
       此处仅 标题 + 成本聚合 + 新对话(store 已支持,先行可用) -->
  <header>
    <span class="title">编码对话</span>
    <span class="spacer"></span>
    <span id="cost" title="本会话累计 token 与成本（done 事件累加；新对话/恢复历史后清零重计）">{{ costText }}</span>
    <button id="new-chat" type="button" title="清空当前对话，开新会话（下次发送走 coding.start）" :disabled="newChatDisabled" @click="store.newChat()">新对话</button>
  </header>

  <MessageList :items="state.items" :pad-for-pill="pillVisible" />

  <ErrBar v-if="state.error" ref="errbarRef" :text="state.error" @layout="relayout" />
  <RunPill
    :bottom="pillBottom"
    :sending="state.sending"
    :streaming="state.streaming"
    :prefix="state.runPrefix"
    :tok="state.usage.tok"
    :on-stop="store.stop"
  />

  <!-- Composer 占位(T5 接入:输入框 / @ chips / 文件补全 / 快捷键行);takeover 态由宿主 InputBar
       驱动,占位整段经 body.takeover 隐藏。状态行保留(发送/运行/完成/错误反馈) -->
  <footer ref="footerEl">
    <div class="composer-ph">输入区占位 —— Composer 于 Task 5 接入（takeover 模式经宿主输入条发送）</div>
    <div class="keys-row">
      <span id="status" :class="{ err: status.err }"><span v-if="status.spin" class="spin"></span>{{ status.text }}</span>
    </div>
  </footer>
</template>
