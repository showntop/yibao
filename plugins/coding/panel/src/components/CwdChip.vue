<script setup lang="ts">
// cwd chip + 浮层(R4 阶段二 T6,对齐 chat.html:2620-2665):
//   chip 只显示目录名(basename),空态「选择项目目录」;点击弹浮层手输(Enter/确定 提交,
//   Esc/点外部取消)或 📁 选目录。浮层绝对定位于 .ctx-row 锚(组件以片段根渲染,
//   pop 与 chip 同层并列,定位模式与原一致)。
// 提交语义(commitCwd:空/同值忽略、running 拒、setCwd+newChat+list 刷新)全在 App——
// 本组件只上抛意图;打开时回填 cwd 并 focus+select(对齐 openCwdPop)。
// input 非受控(无 v-model):提交时读 live DOM 值,与原 `$("cwd-input").value` 一致——
// WebKit 组字时序边下 DOM 值总是最新的,model 可能 lag compositionend。
import { computed, nextTick, ref, watch } from "vue";

const props = defineProps<{ cwd: string; open: boolean }>();
const emit = defineEmits<{
  toggle: [];                 // chip 点击(开/关浮层)
  commit: [value: string];    // Enter/确定/📁 选中路径 → App commitCwd
  cancel: [];                 // Esc/点外部 → 关浮层不提交
  browse: [];                 // 📁 → native:pick_folder(App 调桥,结果走 commit)
}>();

const base = computed(() => (props.cwd || "").split("/").filter(Boolean).pop() || "");
const inputEl = ref<HTMLInputElement | null>(null);

// 打开:回填当前 cwd 并 focus+select(对齐 openCwdPop);关闭由 App 改 open,这里不管
watch(() => props.open, async (o) => {
  if (!o) return;
  await nextTick();
  if (inputEl.value) inputEl.value.value = props.cwd;
  inputEl.value?.focus();
  inputEl.value?.select();
});

function commitInput() { emit("commit", inputEl.value ? inputEl.value.value : ""); }

function onInputKeydown(e: KeyboardEvent) {
  // 组字中的 Enter 只是上屏(原此处仅 isComposing 单守卫,无 50ms 窗——输入框非发送路径)
  if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); commitInput(); }
  else if (e.key === "Escape") { e.stopPropagation(); emit("cancel"); } // 防误触全局 esc 停止
}
</script>

<template>
  <button
    id="cwd-chip"
    type="button"
    class="pill cwd-chip"
    :class="{ empty: !base }"
    :title="cwd ? '项目目录：' + cwd + '（点击修改）' : '项目目录（点击选择）'"
    @click.stop="emit('toggle')"
  >📁 <span id="cwd-chip-label">{{ base || "选择项目目录" }}</span></button>
  <!-- cwd 浮层:手输路径 + 📁 选目录;Enter/确定 提交,Esc/点外部取消(document click 在 App) -->
  <div v-if="open" id="cwd-pop" class="cwd-pop" @click.stop>
    <div class="cwd-pop-row">
      <input
        id="cwd-input"
        ref="inputEl"
        type="text"
        spellcheck="false"
        placeholder="项目路径（如 /Users/.../myapp）"
        @keydown="onInputKeydown"
      >
      <button id="cwd-browse" type="button" title="选择项目文件夹" @click="emit('browse')">📁</button>
      <button id="cwd-ok" type="button" @click="commitInput">确定</button>
    </div>
  </div>
</template>
