<script setup lang="ts">
// 插件列表视图（HomePlugins 列表态）：Launchpad 式网格 + 搜索过滤。
// 纯展示 + 事件上抛（launch/open-panel 带 MouseEvent，父级 captureOrigin 记录动效锚点）；
// 启动动作（panelAction + requested 标记）是父级面板域的事。
import { computed, ref } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { iconStyle, initial } from "../../lib/icons";

interface PluginPanelEntry { name: string; label: string; open: string }
export interface PluginInfo { id: string; name: string; panels?: PluginPanelEntry[] }

const props = defineProps<{
  plugins: PluginInfo[];
  err: string;
}>();

const emit = defineEmits<{
  launch: [plugin: PluginInfo, event?: MouseEvent];
  "open-panel": [plugin: PluginInfo, panel: PluginPanelEntry, event?: MouseEvent];
}>();

// 搜索过滤（按名字或 id，与主屏/小窗的插件网格同一视觉语言）
const query = ref("");
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return props.plugins;
  return props.plugins.filter((p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q));
});
</script>

<template>
  <div class="plist">
    <div v-if="err" class="pl-err"><YbIcon name="alert" :size="14" />{{ err }}</div>
    <label v-if="plugins.length" class="pl-search">
      <input v-model="query" placeholder="搜插件名或 id…" />
    </label>
    <div v-if="filtered.length" class="pgrid">
      <button v-for="p in filtered" :key="p.id" class="pcard" @click="emit('launch', p, $event)">
        <span class="pcard-ic" :style="iconStyle(p.id)">{{ initial(p.name) }}</span>
        <span class="pcard-name">{{ p.name }}</span>
        <span class="pcard-id">{{ p.id }}</span>
        <!-- 面板级入口（manifest [[panel]] open 声明，如素材库/热点雷达）；stop 防触发卡片主入口 -->
        <span v-if="p.panels?.length" class="pcard-subs">
          <span v-for="panel in p.panels" :key="panel.name" class="pcard-sub" @click.stop="emit('open-panel', p, panel, $event)">{{ panel.label }}</span>
        </span>
      </button>
    </div>
    <div v-else-if="plugins.length" class="pl-empty">
      <YbIcon name="plug" :size="26" :stroke="1.4" />
      <p>没找到「{{ query }}」<br /><span>换个关键词试试</span></p>
    </div>
    <div v-else-if="!err" class="pl-empty">
      <YbIcon name="plug" :size="26" :stroke="1.4" />
      <p>还没装插件<br /><span>插件放在 plugins/ 目录，重启大脑后出现在这里</span></p>
    </div>
  </div>
</template>

<style scoped>
.plist {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--yb-space-5) var(--yb-space-4);
  scrollbar-width: thin;
}
.pl-err {
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  margin-bottom: var(--yb-space-3);
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
/* 搜索框 */
.pl-search {
  display: block;
  width: 100%;
  max-width: 260px;
  margin-bottom: var(--yb-space-4);
}
.pl-search input {
  width: 100%;
  padding: 7px 12px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  box-shadow: var(--yb-shadow-1);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  outline: none;
  transition: border-color var(--yb-dur-fast) var(--yb-ease-out), box-shadow var(--yb-dur-fast) var(--yb-ease-out);
}
.pl-search input::placeholder {
  color: var(--yb-text-faint);
}
.pl-search input:focus {
  border-color: var(--yb-accent);
  /* 用软外环代替全局 --yb-focus-ring（双环内白覆盖了 1px accent border） */
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.22);
}

/* 自适应网格：窄窗自动减列，卡片不拉伸变形 */
.pgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--yb-space-3);
}
/* 插件卡：Launchpad 式（图标 + 名字 + id 垂直居中），hover 上浮 + 图标微放大 */
.pcard {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--yb-space-1);
  padding: var(--yb-space-4) var(--yb-space-2);
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.pcard:hover {
  border-color: var(--yb-accent);
  box-shadow: var(--yb-shadow-2);
  transform: translateY(-2px);
}
.pcard:active {
  transform: scale(0.97);
}
/* 图标：按 id 哈希到 5 色调色板（iconStyle 内联 background/color），首字承担 */
.pcard-ic {
  width: 46px;
  height: 46px;
  margin-bottom: var(--yb-space-2);
  display: grid;
  place-items: center;
  border-radius: var(--yb-radius-md);
  font-size: 19px;
  font-weight: var(--yb-fw-bold);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.pcard:hover .pcard-ic {
  transform: scale(1.07);
}
.pcard-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pcard-id {
  font-family: var(--yb-mono);
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pcard-subs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-1);
}
.pcard-sub {
  font-size: var(--yb-fs-xs);
  color: var(--yb-accent);
  cursor: pointer;
}
.pcard-sub:hover {
  text-decoration: underline;
}
.pl-empty {
  height: 100%;
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--yb-space-2);
  color: var(--yb-text-faint);
}
.pl-empty p {
  margin: 0;
  text-align: center;
  font-size: var(--yb-fs-lg);
  line-height: var(--yb-lh-base);
}
.pl-empty span {
  font-size: var(--yb-fs-md);
}
</style>
