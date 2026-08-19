// 行级 LCS diff(移植 chat.html:1311-1336 的 DP 实现,原样翻成 TS;字段名 t/s → type/text,
// "s" 改名 "ctx" 更可读)。空串视为零行:空 old → 全 add;空 new → 全 del。
export interface DiffLine {
  type: "add" | "del" | "ctx";
  text: string;
}

// 行序列 → {a,d} 增删行数统计(移植 chat.html:1339-1346;文件改动卡头行 +a/-d 用)
export function diffStats(lines: DiffLine[]): { a: number; d: number } {
  let a = 0, d = 0;
  for (const l of lines) {
    if (l.type === "add") a++;
    else if (l.type === "del") d++;
  }
  return { a, d };
}

function toLines(text: string | null | undefined): string[] {
  if (text == null || text === "") return [];
  return String(text).split("\n");
}

export function lcsLines(oldText: string, newText: string): DiffLine[] {
  const A = toLines(oldText);
  const B = toLines(newText);
  const n = A.length;
  const m = B.length;
  // dp[i][j] = A[i:] 与 B[j:] 的 LCS 长度
  const dp: Int32Array[] = new Array(n + 1);
  for (let i = 0; i <= n; i++) {
    dp[i] = new Int32Array(m + 1);
  }
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) {
      out.push({ type: "ctx", text: A[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "del", text: A[i] });
      i++;
    } else {
      out.push({ type: "add", text: B[j] });
      j++;
    }
  }
  for (; i < n; i++) out.push({ type: "del", text: A[i] });
  for (; j < m; j++) out.push({ type: "add", text: B[j] });
  return out;
}

export interface MultiEditSegment {
  head: string; // 段头「第 N 处」(对齐原 renderMultiEdit)
  lines: DiffLine[];
}

export interface MultiEditDiff {
  segments: MultiEditSegment[];
}

interface EditEntry {
  old_string?: unknown;
  new_string?: unknown;
}

// MultiEdit 的 new 是 edits 的 JSON 字符串:runner 现状发裸数组(json.dumps(edits),
// _runner.py:263),也容忍 {"edits":[...]} 包装形态;解析失败/非数组 → null(组件退回原文本)。
export function multiEditDiff(newJson: string): MultiEditDiff | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(newJson);
  } catch {
    return null;
  }
  const arr: unknown[] | null = Array.isArray(parsed)
    ? parsed
    : parsed && typeof parsed === "object" && Array.isArray((parsed as { edits?: unknown }).edits)
      ? ((parsed as { edits: unknown[] }).edits)
      : null;
  if (!arr) return null;
  return {
    segments: arr.map((e, idx) => {
      const entry = (e ?? {}) as EditEntry;
      return {
        head: `第 ${idx + 1} 处`,
        lines: lcsLines(
          entry.old_string == null ? "" : String(entry.old_string),
          entry.new_string == null ? "" : String(entry.new_string),
        ),
      };
    }),
  };
}
