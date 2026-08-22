// 聊天气泡专用 markdown-lite：窄气泡里渲染常见标记（含 ``` 围栏代码块），表格转「键：值」行。
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
    // 围栏代码块：``` 起止。内容已在入口整体转义，此处原样拼出、不再做行内替换，
    // 保证块内 **、` 等不被二次替换（XSS 面不扩大）；未闭合则收到文末
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
        code.push(lines[i]);
        i++;
      }
      i++; // 跳过收尾围栏（或已到文末）
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
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
    } else {
      // 可勾选清单：`- [ ] 任务` / `- [x] 完成` → checkbox（AI 给可操作项时的清单卡）
      const task = line.match(/^\s*[-*]\s+\[([ xX])\]\s+(.*)$/);
      if (task) {
        const checked = task[1].toLowerCase() === "x";
        out.push(
          `<label class="md-task"><input type="checkbox"${checked ? " checked" : ""} />` +
          `<span>${inline(task[2])}</span></label>`,
        );
      } else if (/^\s*[-*]\s+/.test(line)) {
      // 列表保留原标记（多是「- emoji 文字」，再叠项目符号会显脏），仅收窄缩进
      out.push(`<div class="md-li">${inline(line.trim())}</div>`);
    } else if (/^\s*\|?\s*-{3,}/.test(line) || /^\s*-{3,}\s*$/.test(line)) {
      out.push('<div class="md-hr"></div>');
    } else if (line.trim() === "") {
      out.push('<div class="md-gap"></div>');
    } else {
      out.push(`<div>${inline(line)}</div>`);
    }
    }
    i++;
  }
  let html = out.join("");
  // 去掉首尾空行占位：md-gap 仅作行间分隔，首/尾出现会让选区下探、气泡底部多出空位
  html = html.replace(/^(<div class="md-gap"><\/div>)+/, "");
  html = html.replace(/(<div class="md-gap"><\/div>)+$/, "");
  return html;
}
