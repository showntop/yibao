<script setup lang="ts">
// schema 面板（协议 v1 附录 A）：白名单渲染 list/detail/form/board；未知 type 或无 schema → 折叠 JSON 降级。
// 标题栏/关闭由外层容器（PanelApp）负责；本组件只管内容，撑满容器高度、内部滚动。
import { computed, reactive, watchEffect } from "vue";
import { resolve, resolveParams, type ActionDecl, type BindCtx, type BoardColumn } from "../lib/schema";

const props = defineProps<{
  panel: string; // 面板引用（plugin_id:name），当前仅用于调试展示
  schema: Record<string, any> | null; // null → 未知降级
  data: Record<string, unknown>; // panel 事件注入的数据（$data.x）
}>();
const emit = defineEmits<{
  (e: "action", a: { method: string; params: Record<string, unknown> }): void;
}>();

const kind = computed<string | undefined>(() => props.schema?.type);
const ctx = computed<BindCtx>(() => ({ data: props.data }));

/** 展示用文本：绑定解析后转字符串（undefined/null → 空串）。 */
function text(v: unknown, item?: Record<string, unknown>): string {
  const r = resolve(v, { data: props.data, item });
  return r === undefined || r === null ? "" : String(r);
}

/** 触发 action：params 里的绑定按当前上下文解析后上抛。 */
function fire(a: ActionDecl, item?: Record<string, unknown>) {
  emit("action", { method: a.method, params: resolveParams(a.params, { data: props.data, item }) });
}

// ---- list ----
const listItems = computed<Record<string, unknown>[]>(() => {
  const bind = props.schema?.bind?.items;
  const v = bind ? resolve(bind, ctx.value) : undefined;
  return Array.isArray(v) ? (v as Record<string, unknown>[]) : [];
});
const itemTpl = computed(() => props.schema?.item ?? {});

// ---- board（items 解析复用 list 的 listItems）----
const boardColumns = computed<BoardColumn[]>(() => props.schema?.columns ?? []);
const cardTpl = computed(() => props.schema?.card ?? {});
/** 按 bind.column 求值分组；不匹配任何声明列的行归入第一列（不丢数据）。 */
const boardGroups = computed<{ column: BoardColumn; items: Record<string, unknown>[] }[]>(() => {
  const groups = boardColumns.value.map((column) => ({
    column,
    items: [] as Record<string, unknown>[],
  }));
  if (!groups.length) return groups;
  const colBind = props.schema?.bind?.column;
  for (const it of listItems.value) {
    const key = colBind ? String(resolve(colBind, { data: props.data, item: it })) : "";
    const g = groups.find((g) => g.column.key === key) ?? groups[0];
    g.items.push(it);
  }
  return groups;
});

// ---- detail ----
const detailFields = computed<{ label: string; value: string }[]>(() => props.schema?.fields ?? []);

// ---- form ----
const formFields = computed<{ name: string; label: string; input?: string }[]>(
  () => props.schema?.fields ?? [],
);
const submitDecl = computed<ActionDecl | undefined>(() => props.schema?.submit);
const formValues = reactive<Record<string, any>>({});
// schema 切换时补齐表单键（不覆盖已输入内容）
watchEffect(() => {
  for (const f of formFields.value) {
    if (!(f.name in formValues)) formValues[f.name] = f.input === "number" ? null : "";
  }
});
function onSubmit() {
  if (!submitDecl.value) return;
  // 提交时把表单值并入 params（协议：submit.params 里的绑定同样生效）
  emit("action", {
    method: submitDecl.value.method,
    params: { ...resolveParams(submitDecl.value.params, ctx.value), ...formValues },
  });
}

// ---- 未知降级 ----
const fallbackJson = computed(() =>
  JSON.stringify(props.schema ?? { data: props.data }, null, 2),
);
</script>

<template>
  <div class="panel">
    <!-- list：卡片列表 + 行级 action -->
    <div v-if="kind === 'list'" class="list">
      <div v-if="!listItems.length" class="empty">暂无数据</div>
      <div v-for="(it, i) in listItems" :key="i" class="card">
        <div class="card-main">
          <div class="card-title">{{ text(itemTpl.title, it) }}</div>
          <div v-if="itemTpl.subtitle" class="card-sub">{{ text(itemTpl.subtitle, it) }}</div>
        </div>
        <div v-if="itemTpl.actions?.length" class="card-actions">
          <button
            v-for="a in itemTpl.actions"
            :key="a.method + a.label"
            class="act"
            @click="fire(a, it)"
          >
            {{ a.label }}
          </button>
        </div>
      </div>
    </div>

    <!-- board：分列看板，卡片纵向堆叠 + 卡级 action -->
    <div v-else-if="kind === 'board'" class="board">
      <div v-if="!boardGroups.length" class="empty">暂无数据</div>
      <div v-for="g in boardGroups" :key="g.column.key" class="board-col">
        <div class="board-head">
          <span class="board-label">{{ g.column.label }}</span>
          <span class="board-count">{{ g.items.length }}</span>
        </div>
        <div v-if="!g.items.length" class="board-empty">空</div>
        <div v-for="(it, i) in g.items" :key="i" class="card">
          <div class="card-main">
            <div class="card-title">{{ text(cardTpl.title, it) }}</div>
            <div v-if="cardTpl.subtitle" class="card-sub">{{ text(cardTpl.subtitle, it) }}</div>
          </div>
          <div v-if="cardTpl.actions?.length" class="card-actions">
            <button
              v-for="a in cardTpl.actions"
              :key="a.method + a.label"
              class="act"
              @click="fire(a, it)"
            >
              {{ a.label }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- detail：字段表 + 底部 action 按钮行 -->
    <div v-else-if="kind === 'detail'" class="detail">
      <div v-for="(f, i) in detailFields" :key="i" class="row">
        <span class="k">{{ f.label }}</span>
        <span class="v">{{ text(f.value) }}</span>
      </div>
      <div v-if="!detailFields.length" class="empty">暂无数据</div>
      <div v-if="schema?.actions?.length" class="detail-actions">
        <button
          v-for="a in schema.actions"
          :key="a.method + a.label"
          class="act"
          @click="fire(a)"
        >
          {{ a.label }}
        </button>
      </div>
    </div>

    <!-- form：输入收集 + submit action -->
    <form v-else-if="kind === 'form'" class="form" @submit.prevent="onSubmit">
      <label v-for="f in formFields" :key="f.name" class="field">
        <span class="k">{{ f.label }}</span>
        <textarea v-if="f.input === 'textarea'" v-model="formValues[f.name]" rows="3" />
        <input v-else-if="f.input === 'number'" v-model.number="formValues[f.name]" type="number" />
        <input v-else v-model="formValues[f.name]" type="text" />
      </label>
      <div class="btns">
        <button type="submit" class="act primary">{{ submitDecl?.label ?? "提交" }}</button>
      </div>
    </form>

    <!-- 未知降级：折叠 JSON，不报错 -->
    <details v-else class="fallback">
      <summary>未知面板（{{ kind ?? "schema 缺失" }}），展开查看原始数据</summary>
      <pre>{{ fallbackJson }}</pre>
    </details>
  </div>
</template>

<style scoped>
.panel {
  height: 100%;
  box-sizing: border-box;
  overflow-y: auto;
  padding: var(--yb-space-3);
  font-size: var(--yb-fs-lg);
  color: var(--yb-text);
}
.empty {
  color: var(--yb-text-dim);
  text-align: center;
  padding: var(--yb-space-4) 0;
}
.card {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2) 10px;
  border-radius: var(--yb-radius-md);
  background: var(--yb-bubble-ai);
  margin-bottom: 6px;
}
.card-main {
  flex: 1;
  min-width: 0;
}
.card-title {
  font-size: var(--yb-fs-lg);
  line-height: 1.4;
  word-break: break-word;
}
.card-sub {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  margin-top: 2px;
}
.card-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.board {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
  height: 100%;
  overflow-x: auto;
}
.board-col {
  flex: 1 0 160px;
  min-width: 160px;
  box-sizing: border-box;
  padding: var(--yb-space-2);
  border-radius: var(--yb-radius-md);
  background: rgba(0, 0, 0, 0.03);
}
.board-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--yb-space-2);
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.board-count {
  font-size: var(--yb-fs-sm);
}
.board-empty {
  color: var(--yb-text-dim);
  text-align: center;
  font-size: var(--yb-fs-sm);
  padding: var(--yb-space-3) 0;
  opacity: 0.7;
}
/* board 内卡片：纵向堆叠、宽度撑满列 */
.board .card {
  flex-direction: column;
  align-items: stretch;
  width: 100%;
  box-sizing: border-box;
}
.board .card:last-child {
  margin-bottom: 0;
}
.detail-actions {
  display: flex;
  gap: 6px;
  margin-top: var(--yb-space-3);
}
.act {
  padding: 5px 12px;
  border-radius: var(--yb-radius-sm);
  border: none;
  cursor: pointer;
  font-size: var(--yb-fs-md);
  background: var(--yb-accent-soft);
  color: var(--yb-accent);
  transition: filter 0.15s;
}
.act:hover {
  filter: brightness(0.96);
}
.act.primary {
  background: var(--yb-accent);
  color: #fff;
}
.row {
  display: flex;
  gap: 10px;
  padding: 5px 2px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
}
.row:last-child {
  border-bottom: none;
}
.k {
  flex-shrink: 0;
  width: 64px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.v {
  word-break: break-word;
}
.form .field {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-1);
  margin-bottom: var(--yb-space-2);
}
.form input,
.form textarea {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: var(--yb-radius-sm);
  padding: 6px 8px;
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  background: #fff;
  color: var(--yb-text);
  outline: none;
}
.form input:focus,
.form textarea:focus {
  border-color: var(--yb-accent);
}
.btns {
  display: flex;
  justify-content: flex-end;
}
.fallback summary {
  cursor: pointer;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.fallback pre {
  margin: var(--yb-space-2) 0 0;
  font-size: var(--yb-fs-sm);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
