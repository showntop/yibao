<script setup lang="ts">
// Codex→CC 交接卡(R4 阶段二 T7;对齐 chat.html:2175-2264 renderHandoffCard/sealHandoffCard):
// #log chat-flow 元素(session 起点,非 modal)——可编辑 textarea + [取消] + [用它开始 → Claude Code];
// brief 读取失败也开卡(errMsg 红条 + 空 textarea 可手动粘贴,不阻断接续);
// 开始 = seal(textarea readonly + 头改「已从 Codex 接续」+ 去底部按钮 + sealed 绿配色)后上抛 start,
// 封存即终态(启动失败也不回解,对齐原)。交互态(sealed/编辑文本)在本组件,数据在 store items
// (newChat/resumeSession 清 items 时卡随之消失,对齐原 #log 清空)。
import { ref } from "vue";

const props = defineProps<{
  item: { sid: string; brief: string | null; incomplete: boolean; errMsg: string | null };
  streaming: boolean; // 「用它开始」的运行中拦截(对齐原 :2221-2224)
}>();
const emit = defineEmits<{
  cancel: [];                  // 卡片移除(store 剔项由 App 做)
  start: [text: string];       // seal 后上抛编辑定稿(App 走 store.startHandoffSession)
  status: [text: string, err: boolean]; // 瞬时状态行
}>();

const text = ref(props.item.brief || "");
const sealed = ref(false);
const taEl = ref<HTMLTextAreaElement | null>(null);

function shortSid(sid: string): string {
  const s = String(sid ?? "");
  return s.length > 10 ? s.slice(0, 8) + "…" : s;
}

function onStart() {
  if (props.streaming) { emit("status", "当前会话运行中，请先中断再「用它开始」", true); return; }
  const edited = text.value.trim();
  if (!edited) { taEl.value?.focus(); emit("status", "交接内容为空，无法开始", true); return; }
  sealed.value = true;
  emit("start", edited);
}

function onCancel() {
  emit("cancel");
  emit("status", "已取消 Codex 接续", false);
}
</script>

<template>
  <div class="row ai">
    <div class="card handoff" :class="{ sealed }">
      <div class="card-head">
        <span class="ic">↩</span>
        <span class="lbl">{{ sealed ? "已从 Codex 接续" : "来自 Codex" }}</span>
        <span class="sid" :title="item.sid">{{ shortSid(item.sid) }}</span>
        <span v-if="item.incomplete && !sealed" class="warn">（基于不完整记录）</span>
        <span class="spacer"></span>
      </div>
      <div class="card-body open">
        <div v-if="item.errMsg" class="note err">✗ {{ item.errMsg }}</div>
        <textarea
          ref="taEl"
          v-model="text"
          class="brief"
          :readonly="sealed"
          :style="sealed ? { minHeight: '60px', maxHeight: '260px' } : undefined"
          placeholder="自动生成失败，可手动粘贴交接内容"
        ></textarea>
      </div>
      <div v-if="!sealed" class="card-foot">
        <button type="button" @click="onCancel">取消</button>
        <button type="button" class="primary" @click="onStart">用它开始 → Claude Code</button>
      </div>
    </div>
  </div>
</template>
