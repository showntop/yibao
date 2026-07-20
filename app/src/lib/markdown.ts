// 聊天气泡专用 markdown-lite：窄气泡里渲染常见标记，表格转「键：值」行。
// 安全：先整体 HTML 转义，再做标记替换，不引第三方库、不支持原始 HTML/图片/链接注入。

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 行内标记：粗体 / 行内代码 / 斜体（*单个*，避免误伤乘号语境的 **） */
function inline(s: string): string {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>");
}

function isTableLine(line: string): boolean {
  return /^\s*\|.*\|\s*$/.test(line);
}

function isTableSeparator(line: string): boolean {
  return /^\s*\|[\s:|-]+\|\s*$/.test(line) && line.includes("-");
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

/** markdown 文本 → 气泡内嵌 HTML。 */
export function renderMarkdownLite(src: string): string {
  const lines = escapeHtml(src).split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 表格块：连续的 | 行 → 每行一条「键：值」（两列）或「a · b · c」；
    // 紧随分隔行的首行视为表头，降灰显示（多数是「项目/内容」这类低信息行）
    if (isTableLine(line)) {
      let first = true;
      while (i < lines.length && isTableLine(lines[i])) {
        if (!isTableSeparator(lines[i])) {
          const isHead = first && i + 1 < lines.length && isTableSeparator(lines[i + 1]);
          const cells = splitRow(lines[i]).filter((c) => c !== "");
          const cls = isHead ? "md-kv md-kv-h" : "md-kv";
          if (cells.length === 2) out.push(`<div class="${cls}">${inline(cells[0])}：${inline(cells[1])}</div>`);
          else if (cells.length > 0) out.push(`<div class="${cls}">${cells.map(inline).join(" · ")}</div>`);
          first = false;
        }
        i++;
      }
      continue;
    }
    const h = line.match(/^\s{0,3}(#{1,4})\s+(.*)$/);
    if (h) {
      out.push(`<div class="md-h">${inline(h[2])}</div>`);
    } else if (/^\s*[-*]\s+/.test(line)) {
      out.push(`<div class="md-li">${inline(line.replace(/^\s*[-*]\s+/, ""))}</div>`);
    } else if (/^\s*\|?\s*-{3,}/.test(line) || /^\s*-{3,}\s*$/.test(line)) {
      out.push('<div class="md-hr"></div>');
    } else if (line.trim() === "") {
      out.push('<div class="md-gap"></div>');
    } else {
      out.push(`<div>${inline(line)}</div>`);
    }
    i++;
  }
  return out.join("");
}
