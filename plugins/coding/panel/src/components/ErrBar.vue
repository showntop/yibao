<script setup lang="ts">
// turn 级错误条(对齐 chat.html:1162-1191 + :2669-2675):错误不上消息流——底部红底细条,
// 左摘要(人话首行,单行截断)+ 右「详情」展开/收起全文。
import { computed, ref, watch } from "vue";
import { humanFirstLine } from "../lib/format";

const props = defineProps<{ text: string }>();

const open = ref(false);
const summary = computed(() => humanFirstLine(props.text));

// 新错误到达:详情收回、toggle 复位(对齐 appendError)
watch(() => props.text, () => { open.value = false; });

function toggle() { open.value = !open.value; }
</script>

<template>
  <div id="errbar">
    <div class="err-row">
      <span id="err-summary" class="err-summary">✗ {{ summary }}</span>
      <button id="err-toggle" type="button" class="err-toggle" @click="toggle">{{ open ? "收起" : "详情" }}</button>
    </div>
    <pre id="err-detail" class="err-detail" :class="{ hidden: !open }">{{ text }}</pre>
  </div>
</template>
