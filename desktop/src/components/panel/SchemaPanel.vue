<script setup lang="ts">
// schema 面板（协议 v1 附录 A）：白名单渲染 list/detail/form/board；未知 type 或无 schema → 折叠 JSON 降级。
// 标题栏/关闭由外层容器（PanelApp）负责；本组件只管内容，撑满容器高度、内部滚动。
import { computed, reactive, ref, watchEffect } from "vue";
import { resolve, resolveParams, type ActionDecl, type BindCtx, type BoardColumn } from "../../lib/schema";
import { renderMarkdownLite } from "../../lib/markdown";
import { relativeTime } from "../../lib/time";

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

/** 行级动作：行数据自带 actions 数组时覆盖 item 模板（core 产物列表按行给动作，
 *  非文件型行不落按钮）；无则退回模板声明（现有插件行为不变）。 */
function rowActions(it: Record<string, unknown>): ActionDecl[] {
  return Array.isArray(it.actions) ? (it.actions as ActionDecl[]) : (itemTpl.value.actions ?? []);
}

// ---- board（items 解析复用 list 的 listItems）----
const boardColumns = computed<BoardColumn[]>(() => props.schema?.columns ?? []);
const cardTpl = computed(() => props.schema?.card ?? {});

/** 卡片徽章行（协议扩展：card.badges = [{text: 绑定, color?, format?: "time"}]，空值跳过）。 */
type BadgeDecl = { text: unknown; color?: string; format?: string };
const badgeDecls = computed<BadgeDecl[]>(() => props.schema?.card?.badges ?? []);
function cardBadges(it: Record<string, unknown>): { text: string; color?: string }[] {
  const out: { text: string; color?: string }[] = [];
  for (const b of badgeDecls.value) {
    const v = resolve(b.text, { data: props.data, item: it });
    if (v === undefined || v === null || v === "") continue;
    const s = b.format === "time" ? relativeTime(Number(v)) : String(v);
    if (s) out.push({ text: s, color: b.color });
  }
  return out;
}
const dragDecl = computed(() => props.schema?.drag);
const quickAddDecl = computed(() => props.schema?.quick_add);
/** 拖拽中的卡片 + 悬停目标列（drag-over 高亮用）。 */
const draggingItem = ref<Record<string, unknown> | null>(null);
const dragOverCol = ref<string | null>(null);
const quickAddText = ref("");

/** 拖拽开始：WKWebView 下 dragstart 必须写 dataTransfer，否则拖拽会话根本不建立（drop 永不触发）。 */
function onCardDragStart(e: DragEvent, it: Record<string, unknown>) {
  draggingItem.value = it;
  e.dataTransfer?.setData("text/plain", String(it.id ?? ""));
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
}

/** 悬停列：高亮 + 声明 dropEffect（缺了它部分内核不派 drop）。 */
function onColDragOver(e: DragEvent, colKey: string) {
  dragOverCol.value = colKey;
  if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
}

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

// ---- doc（markdown 文档阅读：bind.title / bind.text）----
const docTitle = computed(() => text(props.schema?.bind?.title));
const docHtml = computed(() => renderMarkdownLite(text(props.schema?.bind?.text)));

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
  // 只并入本表单声明的字段：formValues 是常驻 reactive，跨面板复用组件实例时会带上
  // 上一个表单的残留键（如先开过 forge 裁决表再开来录数据表），全量合并会污染提交参数
  const declared: Record<string, unknown> = {};
  for (const f of formFields.value) declared[f.name] = formValues[f.name];
  // 提交时把表单值并入 params（协议：submit.params 里的绑定同样生效）
  emit("action", {
    method: submitDecl.value.method,
    params: { ...resolveParams(submitDecl.value.params, ctx.value), ...declared },
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
      <div v-if="!listItems.length" class="empty">
        <div class="empty-title">{{ schema?.empty?.title || "这里还空空的" }}</div>
        <div class="empty-hint">{{ schema?.empty?.hint || "去跟译宝说一句试试，让它帮你添一条" }}</div>
      </div>
      <div v-for="(it, i) in listItems" :key="i" class="card">
        <div class="card-main">
          <div class="card-title">{{ text(itemTpl.title, it) }}</div>
          <div v-if="itemTpl.subtitle" class="card-sub">{{ text(itemTpl.subtitle, it) }}</div>
        </div>
        <div v-if="rowActions(it).length" class="card-actions">
          <button
            v-for="a in rowActions(it)"
            :key="a.method + a.label"
            class="btn ghost sm"
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
      <div v-if="!boardGroups.length" class="empty">
        <div class="empty-title">这里还空空的</div>
        <div class="empty-hint">去跟译宝说一句试试</div>
      </div>
      <div
        v-for="g in boardGroups"
        :key="g.column.key"
        class="board-col"
        :class="{ 'drag-over': dragOverCol === g.column.key }"
        @dragover.prevent="onColDragOver($event, g.column.key)"
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
            @dragstart="onCardDragStart($event, it)"
            @dragend="draggingItem = null; dragOverCol = null"
            @click="cardTpl.actions?.length && fire(cardTpl.actions[0], it)"
          >
            <div class="card-main">
              <div class="card-title">{{ text(cardTpl.title, it) }}</div>
              <div v-if="cardTpl.subtitle" class="card-sub">{{ text(cardTpl.subtitle, it) }}</div>
            </div>
            <div v-if="badgeDecls.length" class="card-badges">
              <span
                v-for="(b, bi) in cardBadges(it)"
                :key="bi"
                class="card-badge"
                :style="b.color ? { color: b.color } : {}"
              >{{ b.text }}</span>
            </div>
            <div v-if="cardTpl.actions?.length" class="card-hover-acts" @click.stop>
              <button
                v-for="a in cardTpl.actions"
                :key="a.method + a.label"
                class="btn ghost mini"
                @click="fire(a, it)"
              >
                {{ a.label }}
              </button>
            </div>
          </div>
          <div v-if="!g.items.length" key="__empty__" class="board-empty">
            {{ dragDecl ? "把卡片拖到这里来" : "这一列还空着" }}
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

    <!-- detail：字段卡片 + 底部 action 按钮行（首个主按钮，其余 ghost） -->
    <div v-else-if="kind === 'detail'" class="detail body-scroll">
      <div class="detail-card">
        <div v-for="(f, i) in detailFields" :key="i" class="row">
          <span class="k">{{ f.label }}</span>
          <span class="v">{{ text(f.value) }}</span>
        </div>
        <div v-if="!detailFields.length" class="empty">
          <div class="empty-title">这里还空空的</div>
        </div>
      </div>
      <div v-if="schema?.actions?.length" class="detail-actions">
        <button
          v-for="(a, i) in schema.actions"
          :key="a.method + a.label"
          class="btn"
          :class="i === 0 ? 'primary' : 'ghost'"
          @click="fire(a)"
        >
          {{ a.label }}
        </button>
      </div>
    </div>

    <!-- doc：markdown 文档阅读（挑战/PRD 等，back 返回上一级） -->
    <div v-else-if="kind === 'doc'" class="doc body-scroll">
      <div class="doc-title">{{ docTitle }}</div>
      <div class="doc-body" v-html="docHtml"></div>
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
        <button type="submit" class="btn primary">{{ submitDecl?.label ?? "提交" }}</button>
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
/* 视觉收敛到全局 token（assets/tokens.css 天青 Sky）：次要表面底 + 白卡片 + 天青主按钮 */
.panel {
  height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 14px;
  background: var(--yb-surface-2);
  border-radius: inherit;
  font: var(--yb-fs-lg)/var(--yb-lh-base) var(--yb-font);
  color: var(--yb-text);
}
.body-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  scrollbar-width: thin;
}
/* 返回导航 */
.back-row {
  flex-shrink: 0;
  margin: -4px 0 6px -6px;
}
.back {
  border: none;
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  padding: 4px 10px;
  border-radius: var(--yb-radius-sm);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.back:hover {
  color: var(--yb-accent-deep);
  background: var(--yb-accent-soft);
}
/* 空态：主句 + 引导句 */
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 36px 16px;
  text-align: center;
}
.board > .empty {
  flex: 1;
}
.empty-title {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
}
.empty-hint {
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
/* ---- 按钮两级体系：主按钮实底 / 次按钮 ghost ---- */
.btn {
  border: none;
  cursor: pointer;
  font-family: inherit;
  border-radius: var(--yb-radius-sm);
  padding: 6px 16px;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.btn:active {
  transform: scale(0.97);
}
.btn.primary {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  box-shadow: var(--yb-shadow-soft);
}
.btn.primary:hover {
  background: var(--yb-accent-deep);
}
.btn.ghost {
  background: transparent;
  color: var(--yb-text-dim);
  border: 1px solid var(--yb-surface-border);
  font-weight: var(--yb-fw-medium);
}
.btn.ghost:hover {
  background: var(--yb-btn-neutral);
  color: var(--yb-text);
}
.btn.sm {
  padding: 4px 12px;
  font-size: var(--yb-fs-md);
}
.btn.mini {
  padding: 2px 9px;
  font-size: var(--yb-fs-sm);
}
/* ---- 卡片（list 行 / board 条目共用基础） ---- */
.card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 14px;
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow);
  margin-bottom: 8px;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.card-main {
  flex: 1;
  min-width: 0;
}
.card-title {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
  line-height: 1.5;
  word-break: break-word;
}
.card-sub {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
  margin-top: 2px;
}
/* 卡片徽章行：平台 / 相对时间等弱信息，次要表面药丸 */
.card-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}
.card-badge {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
  background: var(--yb-well);
  border-radius: var(--yb-radius-pill);
  padding: 1px 8px;
}
.card-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
/* ---- 看板：列 = 白底卡片，条目 = 次要表面小卡 ---- */
.board {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 10px;
  overflow-x: auto;
  padding: 2px;
}
.board-col {
  flex: 0 0 296px; /* 固定列宽左对齐 + 横向滚动，不再均分拉伸 */
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 10px;
  border-radius: var(--yb-radius-md);
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  box-shadow: var(--yb-shadow);
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.board-head {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-shrink: 0;
  padding: 2px 4px 10px;
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
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text);
}
.board-count {
  margin-left: auto;
  font-size: var(--yb-fs-sm);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
  background: var(--yb-well);
  border-radius: var(--yb-radius-pill);
  padding: 1px 8px;
}
.board-cards {
  flex: 1;
  min-height: 40px;
  overflow-y: auto;
  scrollbar-width: thin;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 2px;
}
.board-empty {
  border: 1.5px dashed var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  color: var(--yb-text-dim);
  text-align: center;
  font-size: var(--yb-fs-md);
  padding: 18px 8px;
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
  border-color: transparent;
}
/* 快捷新增：钉在列底部，次要表面底、聚焦时点亮 */
.quick-add {
  flex-shrink: 0;
  width: 100%;
  box-sizing: border-box;
  margin-top: 8px;
  padding: 7px 12px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-2);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  outline: none;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.quick-add::placeholder {
  color: var(--yb-text-dim);
}
.quick-add:hover {
  background: var(--yb-surface-solid);
}
.quick-add:focus {
  background: var(--yb-surface-solid);
  border-color: var(--yb-accent);
}
/* 卡片跨列移动过渡（视觉回响） */
.card-move-move {
  transition: transform var(--yb-dur) var(--yb-ease-out);
}
.card-move-enter-active {
  transition:
    opacity var(--yb-dur) var(--yb-ease-out),
    transform var(--yb-dur) var(--yb-ease-out);
}
.card-move-enter-from {
  opacity: 0;
  transform: translateY(-6px);
}
.card-move-leave-active {
  display: none;
}
/* board 内卡片：次要表面嵌在白列里；点击进详情，悬停浮出 action */
.board .card {
  flex-direction: column;
  align-items: stretch;
  width: 100%;
  box-sizing: border-box;
  position: relative;
  margin-bottom: 0;
  padding: 10px 12px;
  background: var(--yb-surface-2);
  border-color: var(--yb-line);
  box-shadow: none;
}
.board .card.clickable {
  cursor: pointer;
}
.board .card.clickable.draggable {
  cursor: grab;
}
.board .card.clickable:hover {
  transform: translateY(-1px);
  background: var(--yb-surface-solid);
  border-color: var(--yb-surface-border);
  box-shadow: var(--yb-shadow);
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
/* ---- detail：字段卡片 + 主/次按钮行 ---- */
.detail-card {
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  box-shadow: var(--yb-shadow);
  padding: 4px 14px;
}
.detail-card .empty {
  padding: 24px 12px;
}
.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.row {
  display: flex;
  gap: 12px;
  padding: 9px 0;
  border-bottom: 1px solid var(--yb-line);
}
.row:last-child {
  border-bottom: none;
}
.k {
  flex-shrink: 0;
  width: 72px;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.v {
  word-break: break-word;
}
/* ---- doc：markdown 文档阅读 ---- */
.doc-title {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-bold);
  padding: 2px 4px 10px;
}
.doc-body {
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  padding: 12px 16px;
  line-height: 1.7;
}
.doc-body :deep(.md-h) {
  font-weight: var(--yb-fw-bold);
  margin: 10px 0 2px;
}
.doc-body :deep(.md-li) {
  padding-left: 2px;
}
.doc-body :deep(.md-gap) {
  height: 8px;
}
.doc-body :deep(.md-hr) {
  border-top: 1px solid var(--yb-line);
  margin: 8px 0;
}
.doc-body :deep(.md-kv-h) {
  color: var(--yb-text-dim);
}
.doc-body :deep(code) {
  font-family: var(--yb-mono);
  background: var(--yb-code-inline-bg);
  border-radius: var(--yb-radius-xs);
  padding: 0 4px;
  font-size: var(--yb-fs-md);
}
.doc-body :deep(pre) {
  margin: 6px 0;
  padding: 8px 10px;
  background: var(--yb-code-bg);
  border-radius: var(--yb-radius-sm);
  overflow-x: auto;
}
.doc-body :deep(pre code) {
  background: transparent;
  padding: 0;
  border-radius: 0;
}
/* ---- form ---- */
.form .field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 12px;
}
.form .k {
  width: auto;
  font-weight: var(--yb-fw-medium);
}
.form input,
.form textarea {
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-sm);
  padding: 8px 12px;
  font-size: var(--yb-fs-lg);
  font-family: inherit;
  background: var(--yb-card-bg);
  color: var(--yb-text);
  outline: none;
  resize: vertical;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.form input:hover,
.form textarea:hover {
  border-color: var(--yb-accent-soft);
}
.form input:focus,
.form textarea:focus {
  border-color: var(--yb-accent);
}
.btns {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}
/* ---- 未知降级 ---- */
.fallback summary {
  cursor: pointer;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.fallback pre {
  margin: 10px 0 0;
  padding: 12px 14px;
  background: var(--yb-card-bg);
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  font: var(--yb-fs-md)/var(--yb-lh-base) var(--yb-mono);
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--yb-text);
}
</style>
