<script setup lang="ts">
// 工具调用卡(对齐 chat.html:1361-1534):默认折叠单行(chevron + 图标 + 工具名 + 意图摘要 + 结果计数),
// 点头行展开 input JSON pretty(首展开才渲染);tool_result ≤8 行直显、>8 行 details 收起「… +N lines」;
// is_error → 卡 .err + 单行尾 ✗(结果体照常挂卡下,红态)。
import { computed, ref } from "vue";
import type { RenderItem, ToolResultInfo } from "../stores/session";
import { resultTally, summarizeTool, toolIcon } from "../lib/tools";

type ToolItem = Extract<RenderItem, { type: "tool" }>;
const props = defineProps<{ item: ToolItem }>();

const open = ref(false);
const everOpened = ref(false); // 首展开才渲染 input JSON(对齐 populate 懒填),折叠后保留不重建
function toggle() {
  open.value = !open.value;
  if (open.value) everOpened.value = true;
}

const displayTool = computed(() => props.item.tool || "(未知工具)");
const icon = computed(() => toolIcon(props.item.tool));
const intent = computed(() => summarizeTool(props.item.tool, props.item.input));

// 单行尾计数:按 results 时序覆写——错误结果压 ✗;非错误结果按工具提炼,最后非空者赢(对齐原时序语义)
const tally = computed(() => {
  let t = "";
  for (const r of props.item.results) {
    if (r.isError) { t = "✗"; continue; }
    const rt = resultTally(props.item.tool, r.text);
    if (rt) t = rt;
  }
  return t;
});

const inputJson = computed(() => {
  try { return JSON.stringify(props.item.input, null, 2); }
  catch { return String(props.item.input); }
});

// 行数(末尾空行不计,对齐原 lines.pop 规则):≤8 直显,>8 折叠
function lineCount(text: string): number {
  const lines = String(text ?? "").split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines.length;
}
function resultClass(r: ToolResultInfo): string {
  const base = lineCount(r.text) <= 8 ? "tool-result plain" : "tool-result";
  return r.isError ? base + " err" : base;
}
</script>

<template>
  <div class="row ai">
    <div class="card tooluse" :class="{ err: item.hasError }">
      <div class="card-head" title="点击展开参数详情" @click="toggle">
        <span class="chev">{{ open ? "▾" : "▸" }}</span>
        <span class="ic">{{ icon }}</span>
        <span class="lbl">{{ displayTool }}</span>
        <span class="intent">{{ intent }}</span>
        <span class="tally">{{ tally }}</span>
      </div>
      <div class="card-body" :class="open ? 'open' : 'collapsed'">
        <template v-if="everOpened">
          <!-- .tool-input = 原内联样式(whiteSpace:pre-wrap + padding)的类化移植 -->
          <pre v-if="item.input != null" class="dl s tool-input">{{ inputJson }}</pre>
          <div v-else class="note">工具 {{ displayTool }} 未上报参数（runner normalize 仅传 tool 名）。</div>
        </template>
      </div>
      <!-- 结果块挂卡内 card-body 之后(对齐 appendToolResult → card.appendChild):折叠头行也可见 -->
      <template v-for="(r, i) in item.results" :key="i">
        <div v-if="lineCount(r.text) <= 8" :class="resultClass(r)">
          <pre>{{ r.text }}</pre>
        </div>
        <details v-else :class="resultClass(r)">
          <summary>… +{{ lineCount(r.text) }} lines</summary>
          <pre>{{ r.text }}</pre>
        </details>
      </template>
    </div>
  </div>
</template>
