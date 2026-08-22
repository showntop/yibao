<script setup lang="ts">
// 插件启动器视图（宠物窗双击团子进入）：列出插件，点击直达它的主面板。
// 纯展示：插件清单/错误由父级维护（父级的 allPlugins 同时是输入意图匹配的数据源）；
// 启动动作（panelAction + explicit 标记）是父级 surface 裁决域的事，经 launch 事件上抛。
import YbIcon from "../YbIcon.vue";
import { iconStyle, initial } from "../../lib/icons";

defineProps<{
  plugins: { id: string; name: string }[];
  err: string;
}>();

const emit = defineEmits<{
  launch: [plugin: { id: string; name: string }];
}>();
</script>

<template>
  <div class="bubbles">
    <div class="pl-head">
      <span class="pl-title">插件</span>
      <span class="pl-subtitle">选择一个能力继续</span>
    </div>
    <div v-if="err" class="pl-err"><YbIcon name="alert" :size="14" />{{ err }}</div>
    <div class="pl-grid">
      <button v-for="p in plugins" :key="p.id" class="pl-card" @click="emit('launch', p)">
        <span class="pl-card-ico" :style="iconStyle(p.id)">{{ initial(p.name) }}</span>
        <span class="pl-card-name">{{ p.name }}</span>
        <span class="pl-card-id">{{ p.id }}</span>
      </button>
    </div>
    <div v-if="!plugins.length && !err" class="pl-empty">没有发现插件</div>
  </div>
</template>

<style scoped>
/* ---- 插件启动器 ---- */
.pl-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px;
}
.pl-title {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
}
.pl-subtitle {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-sm);
}
.pl-err {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
/* 插件 grid（Launchpad 式）：2 列网格卡，上大 icon + 下名字/id，hover 上浮 */
.pl-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  padding: 2px;
}
.pl-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 14px 8px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  cursor: pointer;
  font-family: inherit;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.pl-card:hover {
  border-color: var(--yb-accent);
  background: var(--yb-surface-solid);
  transform: translateY(-2px);
  box-shadow: var(--yb-shadow-2);
}
.pl-card:active {
  transform: scale(0.97);
}
.pl-card-ico {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: var(--yb-radius-md);
  font-size: 18px;
  font-weight: var(--yb-fw-bold);
  font-family: var(--yb-font);
}
.pl-card-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
  line-height: 1.3;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pl-card-id {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  line-height: 1.2;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pl-empty {
  flex: 1;
  display: grid;
  place-items: center;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
}
.bubbles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  overflow-y: auto;
  padding: 4px 2px 0;
  scrollbar-width: thin;
}
</style>
