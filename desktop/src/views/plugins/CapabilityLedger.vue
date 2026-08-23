<script setup lang="ts">
// 能力台账（P3 管理面板）：展示全部 Tool + 来源形态 / 风险 / 展开态 / 特权标记。
// 数据源 = sidecar `tool_ledger` 直调（L0 只读、不占槽位不抢占对话）。行内操作按形态出：
// core 只读 / plugin·mcp 经对话（use_plugin / use_mcp）展开，skill 域见 skills.list。
import { computed, onMounted, ref } from "vue";
import { runPanelAction } from "../../lib/home/home-panel-run";
import YbIcon from "../../components/common/YbIcon.vue";

interface LedgerRow {
  id: string;
  source_type: "core" | "plugin" | "mcp";
  risk: string;
  expanded: boolean;
  disabled: boolean;
  privileged?: boolean;
}

const emit = defineEmits<{ back: [] }>();

const rows = ref<LedgerRow[]>([]);
const err = ref("");
const loading = ref(false);
const lastUpdated = ref("");

const counts = computed(() => {
  const c: Record<string, number> = {};
  for (const r of rows.value) c[r.source_type] = (c[r.source_type] ?? 0) + 1;
  return c;
});

const TYPE_LABEL: Record<string, string> = { core: "底座", plugin: "插件", mcp: "MCP" };
const TYPE_TONE: Record<string, string> = { core: "t-core", plugin: "t-plugin", mcp: "t-mcp" };

function riskTone(risk: string): string {
  if (risk.startsWith("L0")) return "r-l0";
  if (risk.startsWith("L1")) return "r-l1";
  if (risk.startsWith("L2")) return "r-l2";
  return "r-l3";
}

async function load() {
  loading.value = true;
  err.value = "";
  try {
    const res = await runPanelAction("tool_ledger");
    rows.value = (res?.tools as LedgerRow[]) ?? [];
    lastUpdated.value = new Date().toLocaleTimeString();
  } catch (e) {
    err.value = String(e);
  } finally {
    loading.value = false;
  }
}

/** 行内操作：停用/启用/卸载（L2/L3 走全局确认流程，确认后执行 + 刷新台账）。 */
async function runOp(method: string, id: string) {
  await runPanelAction(method, { id });
  await load();
}

function toggle(r: LedgerRow) {
  void runOp(r.disabled ? "tool_enable" : "tool_disable", r.id);
}

function uninstall(r: LedgerRow) {
  if (!window.confirm(`确定卸载「${r.id}」？将删除来源（插件目录 / MCP 配置 / 技能目录），不可撤销。`)) return;
  void runOp("tool_uninstall", r.id);
}

onMounted(load);
</script>

<template>
  <div class="ledger">
    <div class="ledger-head">
      <button class="back" title="返回插件列表" @click="emit('back')">
        <YbIcon name="x" :size="13" />
      </button>
      <span class="crumb">插件</span>
      <span class="crumb-sep">›</span>
      <span class="ledger-title">能力台账</span>
      <span class="ledger-counts">
        <span v-for="(n, k) in counts" :key="k" class="count">
          <i :class="TYPE_TONE[k]" />{{ TYPE_LABEL[k] ?? k }} {{ n }}
        </span>
      </span>
      <button class="refresh" :disabled="loading" title="刷新台账" @click="load">
        <YbIcon name="spinner" :size="13" :spin="loading" />
        <span v-if="lastUpdated" class="updated">{{ lastUpdated }}</span>
      </button>
    </div>

    <div v-if="err" class="error-bar"><YbIcon name="alert" :size="14" />{{ err }}</div>

    <div class="ledger-body">
      <table class="ledger-table">
        <thead>
          <tr>
            <th class="col-id">id</th>
            <th class="col-type">形态</th>
            <th class="col-risk">风险</th>
            <th class="col-state">状态</th>
            <th class="col-note">备注</th>
            <th class="col-op">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td class="col-id"><code>{{ r.id }}</code><i v-if="r.privileged" class="priv" title="特权插件：管理面只读">★</i></td>
            <td class="col-type"><span class="badge" :class="TYPE_TONE[r.source_type]">{{ TYPE_LABEL[r.source_type] ?? r.source_type }}</span></td>
            <td class="col-risk"><span class="risk" :class="riskTone(r.risk)">{{ r.risk }}</span></td>
            <td class="col-state">
              <span v-if="r.disabled" class="state s-off">已停用</span>
              <span v-else-if="r.source_type === 'core'" class="state s-on">常驻</span>
              <span v-else-if="r.expanded" class="state s-on">已展开</span>
              <span v-else class="state s-off">默认隐藏</span>
            </td>
            <td class="col-note">
              <span v-if="r.privileged" class="note">特权插件</span>
              <span v-else-if="r.disabled" class="note">已停用（tool_enable 恢复）</span>
              <span v-else-if="r.source_type === 'core'" class="note">底座能力</span>
              <span v-else-if="r.expanded" class="note">经 use_plugin/use_mcp 展开</span>
              <span v-else class="note">对话中 use_plugin / use_mcp 展开</span>
            </td>
            <td class="col-op">
              <template v-if="r.source_type === 'core'">
                <span class="op-hint">只读</span>
              </template>
              <template v-else-if="r.privileged">
                <span class="op-hint">特权</span>
              </template>
              <template v-else>
                <button class="op" @click="toggle(r)">{{ r.disabled ? "启用" : "停用" }}</button>
                <button class="op danger" @click="uninstall(r)">卸载</button>
              </template>
            </td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="6" class="empty">台账为空（sidecar 未就绪或 tool_ledger 未注册）</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.ledger {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--yb-content-bg);
}
.ledger-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: 0 var(--yb-space-4) var(--yb-space-3);
  border-bottom: 1px solid var(--yb-border-base);
  user-select: none;
}
.back {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: 50%;
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.back:hover { background: var(--yb-btn-neutral-hover); color: var(--yb-text); }
.crumb { font-size: var(--yb-fs-xl); color: var(--yb-text-dim); }
.crumb-sep { color: var(--yb-text-faint); }
.ledger-title { font-size: var(--yb-fs-xl); font-weight: var(--yb-fw-bold); color: var(--yb-text-strong); }
.ledger-counts { flex: 1; display: flex; gap: var(--yb-space-3); margin-left: var(--yb-space-3); }
.count {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.count i { width: 8px; height: 8px; border-radius: 50%; }
.t-core { background: var(--yb-accent); }
.t-plugin { background: var(--yb-intent-ok); }
.t-mcp { background: var(--yb-warn); }
.refresh {
  height: 26px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
}
.refresh:hover:not(:disabled) { border-color: rgba(var(--yb-c-sky-rgb), 0.28); color: var(--yb-accent); }
.refresh:disabled { opacity: 0.5; cursor: default; }
.updated { color: var(--yb-text-faint); }
.error-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  margin: var(--yb-space-2) var(--yb-space-4) 0;
  padding: 6px var(--yb-space-3);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
  font-size: var(--yb-fs-md);
}
.ledger-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--yb-space-3) var(--yb-space-4);
}
.ledger-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--yb-fs-md);
}
.ledger-table th {
  text-align: left;
  padding: 6px var(--yb-space-2);
  color: var(--yb-text-faint);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-medium);
  border-bottom: 1px solid var(--yb-border-base);
}
.ledger-table td {
  padding: 7px var(--yb-space-2);
  border-bottom: 1px solid var(--yb-border-faint);
  vertical-align: middle;
}
.ledger-table tr:hover td { background: rgba(var(--yb-c-sky-rgb), 0.03); }
.col-id { width: 36%; }
.col-type { width: 9%; }
.col-risk { width: 9%; }
.col-state { width: 13%; }
.col-note { width: 23%; }
.col-op { width: 10%; text-align: right; }
.op-hint { font-size: var(--yb-fs-xs); color: var(--yb-text-faint); }
.op {
  padding: 2px 8px;
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-radius-xs);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
  font: inherit;
  font-size: var(--yb-fs-xs);
  cursor: pointer;
  margin-left: 4px;
}
.op:hover { border-color: rgba(var(--yb-c-sky-rgb), 0.28); color: var(--yb-accent); }
.op.danger:hover { border-color: rgba(var(--yb-danger-rgb, 255, 77, 79), 0.35); color: var(--yb-danger); }
.col-id code {
  font-family: var(--yb-font-mono, ui-monospace, "SF Mono", Menlo, monospace);
  font-size: var(--yb-fs-sm);
  color: var(--yb-text);
}
.priv { margin-left: 6px; color: var(--yb-warn); font-style: normal; }
.badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--yb-radius-lg);
  font-size: var(--yb-fs-xs);
  color: #fff;
}
.t-core.badge { background: var(--yb-accent); }
.t-plugin.badge { background: var(--yb-intent-ok); }
.t-mcp.badge { background: var(--yb-warn); }
.risk { font-size: var(--yb-fs-xs); font-family: var(--yb-font-mono, ui-monospace, Menlo, monospace); }
.r-l0 { color: var(--yb-text-faint); }
.r-l1 { color: var(--yb-intent-ok); }
.r-l2 { color: var(--yb-warn); }
.r-l3 { color: var(--yb-danger); }
.state { font-size: var(--yb-fs-xs); }
.s-on { color: var(--yb-intent-ok); }
.s-off { color: var(--yb-text-faint); }
.note { font-size: var(--yb-fs-xs); color: var(--yb-text-dim); }
.empty { text-align: center; color: var(--yb-text-faint); padding: 24px 0; }
</style>
