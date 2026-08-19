// 展示格式化纯函数:fmtTok/fmtCost 对齐 chat.html:876-894 的顶栏成本语义;
// relTime 会话相对时间(rail 行/历史浮层用;now 由调用方注入,便于测试);
// humanFirstLine 移植 :1181-1191 的 errbar 摘要规则。

// token 计数:999→"999"、1500→"1.5k"、2300000→"2.3M";非有限数 → "0"
export function fmtTok(n: number): string {
  if (!isFinite(n)) return "0";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  return n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
}

// 会话成本:四位小数美元(原 renderCost 的 "$" + cost.toFixed(4));非有限数 → "$0.0000"
export function fmtCost(n: number): string {
  if (!isFinite(n)) return "$0.0000";
  return "$" + n.toFixed(4);
}

interface DoneUsage {
  duration_ms?: number | null;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number | null;
}

// 完成状态行(移植 chat.html:1566-1579 setStatusDone):「✓ 完成 · Ns · X tok · $Y」。
// 数值强转 + isFinite 守御:usage 段坏值静默略过该段,绝不上抛(旧码直接 .toFixed 抛错,
// initCbs try/catch 会吞掉后续终态处理 → 流式卡死);cost 段三位小数(与顶栏四位聚合不同,原样)。
export function doneStatusText(u?: DoneUsage | null): string {
  const parts = ["✓ 完成"];
  if (u) {
    const dur = Number(u.duration_ms);
    if (u.duration_ms != null && isFinite(dur)) parts.push(Math.round(dur / 1000) + "s");
    const tok = (Number(u.input_tokens) || 0) + (Number(u.output_tokens) || 0);
    if (tok) parts.push(fmtTok(tok) + " tok");
    const cost = Number(u.cost_usd);
    if (u.cost_usd != null && isFinite(cost)) parts.push("$" + cost.toFixed(3));
  }
  return parts.join(" · ");
}

// 错误对象 → 人话消息(移植 chat.html:856 emsg):鸭子类型取 .message(桥 reject 可能是
// 裸对象而非 Error 实例),取不到 String 化,空值 → ""
export function emsg(e: unknown): string {
  const m = (e as { message?: unknown } | null | undefined)?.message;
  return e && m ? String(m) : String(e || "");
}

// 数字 ts 兼容秒级(<1e12 → ×1000);坏值 → null
function toDate(ts: number | string | null | undefined): Date | null {
  if (ts == null) return null;
  const d = typeof ts === "number" ? new Date(ts < 1e12 ? ts * 1000 : ts) : new Date(ts);
  return isNaN(d.getTime()) ? null : d;
}

// 相对时间:<1min「刚刚」/ <1h「N 分钟前」/ <1d「N 小时前」/ <7d「N 天前」/ 更早绝对日期(本地时区)
export function relTime(now: number, ts: number | string | null | undefined): string {
  const d = toDate(ts);
  if (!d) return "";
  const m = Math.floor(Math.max(0, now - d.getTime()) / 60000);
  if (m < 1) return "刚刚";
  if (m < 60) return m + " 分钟前";
  const h = Math.floor(m / 60);
  if (h < 24) return h + " 小时前";
  const days = Math.floor(h / 24);
  if (days < 7) return days + " 天前";
  const pad = (x: number) => (x < 10 ? "0" + x : "" + x);
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
}

// Codex 推送的 timestamp 兼容 ISO 字符串 / epoch 毫秒数字 / 已格式化字符串
// (移植 chat.html:2122-2129 fmtTs,handoff picker 条目用);非法值原样返回
export function fmtTs(ts: number | string | null | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  const pad = (n: number) => (n < 10 ? "0" + n : "" + n);
  return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
         " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}

// cwd 比较归一化:去尾部斜杠(移植 chat.html:2452 normCwd;sessions 表 cwd 与输入路径可能差一个 /)
export function normCwd(p: string): string {
  return (p || "").replace(/\/+$/, "");
}

// 审批摘要(镜像 _runner._summarize_tool_input):input 为 dict 时 command/file_path/path
// 取其一,否则 JSON 全量;单行化(\n→空格)trim 后截 80 字。非 dict 输入对齐后端 d={} → "{}";
// JSON 段走 JSON.stringify(无空格分隔,与后端 json.dumps 的空格风格有微差,展示用不逐字对齐)。
export function permSummary(tool: string, input: unknown): string {
  const d = (input !== null && typeof input === "object" && !Array.isArray(input)
    ? input : {}) as Record<string, unknown>;
  const text = d.command || d.file_path || d.path || JSON.stringify(d);
  return String(text).replace(/\n/g, " ").trim().slice(0, 80);
}

// 审批卡公开参数(镜像 _runner._public_params):command/file_path/path 取一截 200 字,否则 {}——
// 悬停详情只给决策有关字段,不带全量 input(review 栏 tap 的 upsert 用)
export function permPublicParams(input: unknown): Record<string, string> {
  const d = (input !== null && typeof input === "object" && !Array.isArray(input)
    ? input : {}) as Record<string, unknown>;
  for (const k of ["command", "file_path", "path"]) {
    if (d[k]) return { [k]: String(d[k]).slice(0, 200) };
  }
  return {};
}

// 人话首行:跳过空行 / 协议 XML 行(< 开头)/ 堆栈帧(at …、File "…"、Traceback),
// 行内残留标签剥掉;全都不是人话时给兜底文案(错误全文仍在详情区可见)
export function humanFirstLine(text: string): string {
  const lines = String(text ?? "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    let ln = lines[i].trim();
    if (!ln || ln.charAt(0) === "<") continue;
    if (/^(at\s|File\s"|Traceback)/.test(ln)) continue;
    ln = ln.replace(/<[^>]*>/g, "").trim();
    if (ln) return ln;
  }
  return "执行出错（点「详情」查看全文）";
}
