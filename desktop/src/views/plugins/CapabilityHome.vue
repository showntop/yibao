<script setup lang="ts">
// 「能力」页列表态（HomePlugins 列表视图 v2）：插件 / 技能 / 底座 三段分组总览。
// 数据：plugins 由父级给（listPlugins）；skills + 工具数自取（tool_ledger L0 只读直调，
// 失败静默降级为只显示插件宫格）。纯展示 + 事件上抛；launch/open-panel 带 MouseEvent
// （父级 captureOrigin 记录面板生长动效锚点）；技能卡点击 → chat 草稿上抛（一句话直达）。
import { computed, onMounted, ref } from "vue";
import YbIcon from "../../components/common/YbIcon.vue";
import { iconStyle, initial } from "../../lib/icons";
import { runPanelAction } from "../../lib/home/home-panel-run";
import {
  filterCapabilities,
  groupCapabilities,
  type CapabilityGroups,
  type LedgerSkill,
  type LedgerTool,
  type PluginLike,
} from "../../lib/capability-groups";

interface PluginPanelEntry { name: string; label: string; open: string }
export interface PluginInfo { id: string; name: string; panels?: PluginPanelEntry[] }

const props = defineProps<{
  plugins: PluginInfo[];
  err: string;
}>();

const emit = defineEmits<{
  launch: [plugin: PluginInfo, event?: MouseEvent];
  "open-panel": [plugin: PluginInfo, panel: PluginPanelEntry, event?: MouseEvent];
  chat: [draft: string];
}>();

// ---- 自取数：tool_ledger（skills + 各插件工具数 + 底座清单）----
const ledgerTools = ref<LedgerTool[]>([]);
const ledgerSkills = ref<LedgerSkill[]>([]);
const ledgerReady = ref(false);
onMounted(async () => {
  const res = await runPanelAction("tool_ledger");
  ledgerTools.value = (res?.tools as LedgerTool[]) ?? [];
  ledgerSkills.value = (res?.skills as LedgerSkill[]) ?? [];
  ledgerReady.value = true;
});

const groups = computed<CapabilityGroups>(() =>
  groupCapabilities(ledgerTools.value, ledgerSkills.value, props.plugins as PluginLike[]),
);

// 跨组搜索过滤（插件名/id + 技能名/描述/owner + 底座工具 id）
const query = ref("");
const filtered = computed(() => filterCapabilities(groups.value, query.value));
const searching = computed(() => query.value.trim().length > 0);
const nothingFound = computed(
  () =>
    searching.value &&
    !filtered.value.plugins.length &&
    !filtered.value.skillGroups.length &&
    !filtered.value.coreTools.length &&
    !filtered.value.mcpTools.length,
);

/** 技能卡 → 对话草稿：owner 前缀 id 无歧义，用户补半句即发车。 */
function skillDraft(s: LedgerSkill): string {
  return `用 ${s.id} 技能：`;
}
</script>

<template>
  <div class="cap">
    <div v-if="err" class="cap-err"><YbIcon name="alert" :size="14" />{{ err }}</div>
    <label v-if="plugins.length" class="cap-search">
      <input v-model="query" placeholder="搜插件、技能、底座能力…" />
    </label>

    <div v-if="nothingFound" class="cap-empty">
      <YbIcon name="plug" :size="26" :stroke="1.4" />
      <p>没找到「{{ query }}」<br /><span>换个关键词试试</span></p>
    </div>

    <template v-else>
      <!-- 插件：Launchpad 宫格（保留原视觉语言）+ 工具数 -->
      <section v-if="filtered.plugins.length" class="cap-sec" style="--stagger: 0">
        <h2 class="sec-title">插件 <span class="sec-count yb-num">{{ filtered.plugins.length }}</span></h2>
        <div class="pgrid">
          <button v-for="p in filtered.plugins" :key="p.id" class="pcard" @click="emit('launch', p, $event)">
            <span class="pcard-ic" :style="iconStyle(p.id)">{{ initial(p.name) }}</span>
            <span class="pcard-name">{{ p.name }}</span>
            <span class="pcard-id">{{ p.id }}<template v-if="ledgerReady && p.toolCount"> · {{ p.toolCount }} 工具</template></span>
            <span v-if="p.panels?.length" class="pcard-subs">
              <span v-for="panel in p.panels" :key="panel.name" class="pcard-sub" @click.stop="emit('open-panel', p, panel, $event)">{{ panel.label }}</span>
            </span>
          </button>
        </div>
      </section>

      <!-- 技能：按 owner 分组（独立技能 + 各插件包内），点击 = 对话草稿直达 -->
      <section v-if="filtered.skillGroups.length" class="cap-sec" style="--stagger: 1">
        <h2 class="sec-title">技能 <span class="sec-count yb-num">{{ filtered.skillGroups.reduce((n, g) => n + g.skills.length, 0) }}</span></h2>
        <div v-for="g in filtered.skillGroups" :key="g.owner ?? '__independent'" class="skill-group">
          <h3 class="skill-owner">{{ g.ownerName }}</h3>
          <div class="sgrid">
            <button
              v-for="s in g.skills"
              :key="s.id"
              class="scard"
              :title="`${s.id} — 点击填入对话草稿`"
              @click="emit('chat', skillDraft(s))"
            >
              <span class="scard-name">{{ s.name }}</span>
              <span class="scard-desc">{{ s.description || s.id }}</span>
            </button>
          </div>
        </div>
      </section>

      <!-- 底座 / MCP：紧凑 chip 流（只读，无卡片重量） -->
      <section v-if="filtered.coreTools.length || filtered.mcpTools.length" class="cap-sec" style="--stagger: 2">
        <h2 class="sec-title">底座 <span class="sec-count yb-num">{{ filtered.coreTools.length + filtered.mcpTools.length }}</span></h2>
        <div class="chips">
          <code v-for="id in filtered.coreTools" :key="id" class="chip" :title="`底座工具 ${id}（常驻）`">{{ id }}</code>
          <code v-for="id in filtered.mcpTools" :key="id" class="chip mcp" :title="`MCP 工具 ${id}`">{{ id }}</code>
        </div>
      </section>

      <div v-if="!plugins.length && !err" class="cap-empty">
        <YbIcon name="plug" :size="26" :stroke="1.4" />
        <p>还没装插件<br /><span>插件放在 plugins/ 目录，重启大脑后出现在这里</span></p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--yb-space-5) var(--yb-space-4);
  scrollbar-width: thin;
}
.cap-err {
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
.cap-search {
  display: block;
  width: 100%;
  max-width: 260px;
  margin-bottom: var(--yb-space-5);
}
.cap-search input {
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
.cap-search input::placeholder { color: var(--yb-text-faint); }
.cap-search input:focus {
  border-color: var(--yb-accent);
  box-shadow: 0 0 0 3px rgba(var(--yb-c-sky-rgb), 0.22);
}

/* 分组：标题 + 计数；staggered 入场（仅首挂载，不做循环装饰） */
.cap-sec {
  margin-bottom: var(--yb-space-6);
  animation: cap-in var(--yb-dur-slow) var(--yb-ease-spring) backwards;
  animation-delay: calc(var(--stagger) * 60ms);
}
@keyframes cap-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  .cap-sec { animation: none; }
}
.sec-title {
  display: flex;
  align-items: baseline;
  gap: var(--yb-space-2);
  margin-bottom: var(--yb-space-3);
  font-size: var(--yb-fs-xl);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-strong);
}
.sec-count {
  font-size: var(--yb-fs-sm);
  font-weight: normal;
  color: var(--yb-text-faint);
}

/* 插件卡：与 PluginGrid 同一视觉语言（图标哈希配色 + hover 上浮） */
.pgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--yb-space-3);
}
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
.pcard:active { transform: scale(0.97); }
.pcard:focus-visible { outline: none; box-shadow: var(--yb-focus-ring); }
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
.pcard:hover .pcard-ic { transform: scale(1.07); }
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
.pcard-sub:hover { text-decoration: underline; }

/* 技能卡：左对齐文本卡（与插件宫的居中图标卡拉开层级） */
.skill-group { margin-bottom: var(--yb-space-4); }
.skill-owner {
  margin-bottom: var(--yb-space-2);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text-dim);
}
.sgrid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: var(--yb-space-3);
}
.scard {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--yb-space-3);
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-1);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.scard:hover {
  border-color: var(--yb-accent);
  box-shadow: var(--yb-shadow-1);
  transform: translateY(-1px);
}
.scard:active { transform: scale(0.97); }
.scard:focus-visible { outline: none; box-shadow: var(--yb-focus-ring); }
.scard-name {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  color: var(--yb-text);
}
.scard-desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: var(--yb-fs-sm);
  line-height: var(--yb-lh-ui);
  color: var(--yb-text-dim);
}

/* 底座/MCP chip 流 */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-2);
}
.chip {
  padding: 3px 8px;
  border: 1px solid var(--yb-border-soft);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-surface-2);
  color: var(--yb-text-dim);
  font-family: var(--yb-mono);
  font-size: var(--yb-fs-xs);
}
.chip.mcp { color: var(--yb-accent); border-color: var(--yb-accent-line); }

.cap-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--yb-space-2);
  padding: var(--yb-space-6) 0;
  color: var(--yb-text-faint);
  text-align: center;
}
.cap-empty p { font-size: var(--yb-fs-md); line-height: var(--yb-lh-ui); }
.cap-empty span { font-size: var(--yb-fs-sm); }
</style>
