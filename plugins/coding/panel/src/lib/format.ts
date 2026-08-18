// 展示格式化纯函数:fmtTok/fmtCost 对齐 chat.html:876-894 的顶栏成本语义;
// relTime 对齐 :2132-2151 的 wall subtitle 相对时间(now 由调用方注入,便于测试);
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
