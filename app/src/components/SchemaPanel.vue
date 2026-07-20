<script setup lang="ts">
// schema 面板（协议 v1 附录 A）：白名单渲染 list/detail/form/board；未知 type 或无 schema → 折叠 JSON 降级。
// 标题栏/关闭由外层容器（PanelApp）负责；本组件只管内容，撑满容器高度、内部滚动。
import { computed, reactive, ref, watchEffect } from "vue";
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
/** 返回导航（协议：任意 type 可声明 back，渲染为左上角「‹ 返回」链接，本质是一个 action） */
const backDecl = computed<ActionDecl | undefined>(() => props.schema?.back);

/** 展示用文本：绑定解析后转字符串（undefined/null → 空串）。 */
function text(v: unknown, item?: Record<string, unknown>): string {
  const r = resolve(v, { data: props.data, item });
  return r === undefined || r === null ? "" : String(r);
}

/** 触发 action：params 里的绑定按当前上下文解析后上抛。 */
function fire(a: ActionDecl, item?: Record<string, unknown>) {
  emit("action", { method: a.method, params: resolveParams(a.params, { data: props.data, item }) });
}

/** 返回导航点击（backDecl 存在才渲染按钮，这里只是收窄类型）。 */
function fireBack() {
  if (backDecl.value) fire(backDecl.value);
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
const dragDecl = computed(() => props.schema?.drag);
const quickAddDecl = computed(() => props.schema?.quick_add);
/** 拖拽中的卡片 + 悬停目标列（drag-over 高亮用）。 */
const draggingItem = ref<Record<string, unknown> | null>(null);
const dragOverCol = ref<string | null>(null);
const quickAddText = ref("");

/** 触发拖拽流转：$column 解析为目标列 key，其余绑定照旧。 */
function dropOn(colKey: string) {
  const d = dragDecl.value;
  const it = draggingItem.value;
  draggingItem.value = null;
  dragOverCol.value = null;
  if (!d || !it) return;
  const params = resolveParams(d.params, { data: props.data, item: it });
  for (const k of Object.keys(params)) {
    if (params[k] === "$column") params[k] = colKey;
  }
  emit("action", { method: d.method, params });
}

/** 快捷新增：Enter 提交，$text 解析为输入内容。 */
function quickAdd() {
  const q = quickAddDecl.value;
  const t = quickAddText.value.trim();
  if (!q || !t) return;
  quickAddText.value = "";
  const params: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(q.params ?? {})) params[k] = v === "$text" ? t : v;
  emit("action", { method: q.method, params });
}
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
    <!-- 返回导航（可选）：回到上一级面板，本质是一个 action（走正常白名单/闸门） -->
    <div v-if="backDecl" class="back-row">
      <button class="back" @click="fireBack">‹ {{ backDecl.label || "返回" }}</button>
    </div>

    <!-- list：卡片列表 + 行级 action -->
    <div v-if="kind === 'list'" class="list body-scroll">
      <div v-if="!listItems.length" class="empty">还没有内容，来一条？</div>
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

    <!-- board：全高分列看板。列 = 头部（色点+计数徽标）/ 卡片滚动区 / 底部快捷新增；
         卡片点击 = 首个卡级 action（通常进详情），悬停浮出全部 action -->
    <div v-else-if="kind === 'board'" class="board">
      <div v-if="!boardGroups.length" class="empty">还没有内容，来一条？</div>
      <div
        v-for="g in boardGroups"
        :key="g.column.key"
        class="board-col"
        :class="{ 'drag-over': dragOverCol === g.column.key }"
        @dragover.prevent="dragOverCol = g.column.key"
        @dragleave="dragOverCol === g.column.key && (dragOverCol = null)"
        @drop.prevent="dropOn(g.column.key)"
      >
        <div class="board-head">
          <span class="col-dot" :style="g.column.color ? { background: g.column.color } : {}" />
          <span class="board-label">{{ g.column.label }}</span>
          <span class="board-count">{{ g.items.length }}</span>
        </div>
        <TransitionGroup name="card-move" tag="div" class="board-cards">
          <div
            v-for="it in g.items"
            :key="String(it.id ?? JSON.stringify(it))"
            class="card"
            :class="{
              draggable: !!dragDecl,
              dragging: draggingItem === it,
              clickable: !!cardTpl.actions?.length,
            }"
            :draggable="!!dragDecl"
            @dragstart="draggingItem = it"
            @dragend="draggingItem = null; dragOverCol = null"
            @click="cardTpl.actions?.length && fire(cardTpl.actions[0], it)"
          >
            <div class="card-main">
              <div class="card-title">{{ text(cardTpl.title, it) }}</div>
              <div v-if="cardTpl.subtitle" class="card-sub">{{ text(cardTpl.subtitle, it) }}</div>
            </div>
            <div v-if="cardTpl.actions?.length" class="card-hover-acts" @click.stop>
              <button
                v-for="a in cardTpl.actions"
                :key="a.method + a.label"
                class="act mini"
                @click="fire(a, it)"
              >
                {{ a.label }}
              </button>
            </div>
          </div>
          <div v-if="!g.items.length" key="__empty__" class="board-empty">
            {{ dragDecl ? "拖卡片到这里" : "空" }}
          </div>
        </TransitionGroup>
        <input
          v-if="quickAddDecl && (!quickAddDecl.column || quickAddDecl.column === g.column.key)"
          v-model="quickAddText"
          class="quick-add"
          :placeholder="quickAddDecl.placeholder ?? '快速记一条…'"
          @keyup.enter="quickAdd"
        />
      </div>
    </div>

    <!-- detail：字段表 + 底部 action 按钮行 -->
    <div v-else-if="kind === 'detail'" class="detail body-scroll">
      <div v-for="(f, i) in detailFields" :key="i" class="row">
        <span class="k">{{ f.label }}</span>
        <span class="v">{{ text(f.value) }}</span>
      </div>
      <div v-if="!detailFields.length" class="empty">还没有内容</div>
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
    <form v-else-if="kind === 'form'" class="form body-scroll" @submit.prevent="onSubmit">
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
    <details v-else class="fallback body-scroll">
      <summary>未知面板（{{ kind ?? "schema 缺失" }}），展开查看原始数据</summary>
      <pre>{{ fallbackJson }}</pre>
    </details>
  </div>
</template>

<style scoped>
.panel {
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: var(--yb-space-3);
  font-size: var(--yb-fs-lg);
  color: var(--yb-text);
}
.body-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
/* 返回导航 */
.back-row {
  flex-shrink: 0;
  margin: calc(-1 * var(--yb-space-1)) 0 var(--yb-space-1) calc(-1 * var(--yb-space-1));
}
.back {
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  cursor: pointer;
  padding: 3px 10px;
  border-radius: var(--yb-radius-sm);
}
.back:hover {
  color: var(--yb-accent);
  background: var(--yb-btn-neutral);
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
  background: var(--yb-surface);
  box-shadow: var(--yb-shadow-soft);
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
/* ---- 看板：全高列布局 ---- */
.board {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--yb-space-2);
  overflow-x: auto;
}
.board-col {
  flex: 1 0 168px;
  min-width: 168px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: var(--yb-space-2);
  border-radius: var(--yb-radius-md);
  background: var(--yb-well);
}
.board-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  padding: 2px 4px var(--yb-space-2);
}
.col-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-text-dim);
  flex-shrink: 0;
}
.board-label {
  font-size: var(--yb-fs-md);
  font-weight: 600;
  color: var(--yb-text-dim);
}
.board-count {
  margin-left: auto;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  background: var(--yb-btn-neutral);
  border-radius: 999px;
  padding: 1px 8px;
}
.board-cards {
  flex: 1;
  min-height: 40px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 2px;
}
.board-empty {
  border: 1.5px dashed var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  color: var(--yb-text-dim);
  text-align: center;
  font-size: var(--yb-fs-sm);
  padding: var(--yb-space-4) var(--yb-space-2);
  opacity: 0.8;
}
/* 拖拽流转：可拖卡片 / 拖动中 / 目标列高亮 */
.card.draggable {
  cursor: grab;
}
.card.dragging {
  opacity: 0.45;
}
.board-col.drag-over {
  outline: 2px dashed var(--yb-accent);
  outline-offset: -2px;
  background: var(--yb-accent-soft);
}
/* 快捷新增：钉在列底部，平时低调、聚焦才显形 */
.quick-add {
  flex-shrink: 0;
  width: 100%;
  box-sizing: border-box;
  margin-top: 6px;
  padding: 6px 10px;
  border: 1px solid transparent;
  border-radius: var(--yb-radius-sm);
  background: transparent;
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  outline: none;
  transition: background 0.15s, border-color 0.15s;
}
.quick-add::placeholder {
  color: var(--yb-text-dim);
  opacity: 0.75;
}
.quick-add:hover {
  background: var(--yb-surface);
}
.quick-add:focus {
  background: var(--yb-surface);
  border-color: var(--yb-accent);
}
/* 卡片跨列移动过渡（视觉回响） */
.card-move-move {
  transition: transform 0.25s var(--yb-ease);
}
.card-move-enter-active {
  transition: opacity 0.2s var(--yb-ease), transform 0.25s var(--yb-ease);
}
.card-move-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}
.card-move-leave-active {
  display: none;
}
/* board 内卡片：纵向堆叠、宽度撑满列；点击进详情，悬停浮出 action */
.board .card {
  flex-direction: column;
  align-items: stretch;
  width: 100%;
  box-sizing: border-box;
  position: relative;
  margin-bottom: 0;
  padding: 10px 12px;
  background: var(--yb-surface-solid);
  transition: transform 0.15s, box-shadow 0.15s;
}
.board .card.clickable:hover {
  transform: translateY(-1px);
  box-shadow: var(--yb-shadow-soft), 0 4px 14px rgba(120, 72, 40, 0.1);
}
.board .card.clickable {
  cursor: pointer;
}
.board .card.clickable.draggable {
  cursor: grab;
}
.card-hover-acts {
  position: absolute;
  top: 6px;
  right: 8px;
  display: none;
  gap: 4px;
}
.board .card:hover .card-hover-acts {
  display: flex;
}
.act.mini {
  padding: 2px 8px;
  font-size: var(--yb-fs-sm);
  background: var(--yb-accent-soft);
}
.detail-actions {
  display: flex;
  flex-wrap: wrap;
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
  border-bottom: 1px solid var(--yb-surface-border);
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
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  padding: 6px 8px;
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  background: var(--yb-surface-solid);
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
