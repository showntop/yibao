// schema 协议 v1（docs/superpowers/specs/2026-07-18-yibao-v2-plugin-architecture.md 附录 A）：
// 四组件 list/detail/form/board + 绑定语法（$data.x 注入数据 / $item.x list/board item 上下文）。

/** 面板动作声明：method 必须在 api.toml 白名单，params 值支持绑定语法。 */
export interface ActionDecl {
  label: string;
  method: string;
  params?: Record<string, unknown>;
}

export interface ListSchema {
  version?: number;
  type: "list";
  bind?: { items?: string };
  item?: { title?: string; subtitle?: string; actions?: ActionDecl[] };
}

export interface DetailSchema {
  version?: number;
  type: "detail";
  fields?: { label: string; value: string }[];
  actions?: ActionDecl[];
}

export interface FormSchema {
  version?: number;
  type: "form";
  fields?: { name: string; label: string; input?: "text" | "textarea" | "number" }[];
  submit?: ActionDecl;
}

export interface BoardColumn {
  key: string;
  label: string;
}

export interface BoardSchema {
  version?: number;
  type: "board";
  bind?: { items?: string; column?: string };
  columns?: BoardColumn[];
  card?: { title?: string; subtitle?: string; actions?: ActionDecl[] };
  /** 拖拽流转（可选）：卡片拖入目标列时触发；params 里 $column = 目标列 key */
  drag?: { method: string; params?: Record<string, unknown> };
  /** 快捷新增（可选）：指定列顶部输入框，Enter 触发；params 里 $text = 输入内容 */
  quick_add?: { method: string; params?: Record<string, unknown>; column?: string; placeholder?: string };
}

export type SchemaDoc = ListSchema | DetailSchema | FormSchema | BoardSchema;

/** 绑定解析上下文：data 是 panel 事件注入的数据；item 仅 list/board item 模板内有。 */
export interface BindCtx {
  data: Record<string, unknown>;
  item?: Record<string, unknown>;
}

// 绑定表达式：$data.a.b / $item.x，可带管道过滤器（$item.created_at|date）
const FULL_BIND = /^\$((?:data|item)(?:\.[\w-]+)*(?:\|[\w-]+)*)$/;
const EMBED_BIND = /\$((?:data|item)(?:\.[\w-]+)*(?:\|[\w-]+)*)/g;

function lookupPath(root: unknown, keys: string[]): unknown {
  let cur = root;
  for (const k of keys) {
    if (cur === null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

/** 管道过滤器（v1 仅 date：unix 秒 → "M月d日 HH:mm"）；未知过滤器原样透传不炸。 */
function applyFilter(v: unknown, name: string): unknown {
  if (name === "date") {
    const n = typeof v === "number" ? v : Number(v);
    if (!Number.isFinite(n) || n <= 0) return "";
    const d = new Date(n * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${d.getMonth() + 1}月${d.getDate()}日 ${hh}:${mm}`;
  }
  return v;
}

function bindValue(expr: string, ctx: BindCtx): unknown {
  const [path, ...filters] = expr.split("|");
  const [root, ...keys] = path.split(".");
  let v = lookupPath(root === "data" ? ctx.data : ctx.item, keys);
  for (const f of filters) v = applyFilter(v, f);
  return v;
}

/**
 * 绑定解析：整串恰好是一个绑定 → 取原值（保留类型）；含绑定的普通字符串 → 插值；
 * 非字符串原样返回。取不到的键给空串，不炸。
 */
export function resolve(value: unknown, ctx: BindCtx): unknown {
  if (typeof value !== "string") return value;
  const full = FULL_BIND.exec(value.trim());
  if (full) {
    const v = bindValue(full[1], ctx);
    return v === undefined || v === null ? "" : v;
  }
  return value.replace(EMBED_BIND, (_, path: string) => {
    const v = bindValue(path, ctx);
    return v === undefined || v === null ? "" : String(v);
  });
}

/** 解析 params 对象里的所有绑定（action 触发时调用），浅拷贝不改动原对象。 */
export function resolveParams(
  params: Record<string, unknown> | undefined,
  ctx: BindCtx,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params ?? {})) out[k] = resolve(v, ctx);
  return out;
}
