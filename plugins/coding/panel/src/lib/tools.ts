// 工具卡的纯展示助手(移植 chat.html:1363-1389 工具图标与意图摘要、
// :1478-1491 结果计数)。DOM 无关,node 可测。

// 工具图标:emoji/字符与现有 ✎/🔐 风格一致;未列出的工具默认 ⚙
const TOOL_ICONS: Record<string, string> = {
  Bash: "❯", Read: "📄", Write: "✎", Edit: "✎", MultiEdit: "✎",
  Grep: "🔍", Glob: "📂", WebFetch: "🌐", WebSearch: "🔎",
  Task: "🤖", TodoWrite: "☰",
};

export function toolIcon(tool: string): string {
  return TOOL_ICONS[tool] || "⚙";
}

// 意图摘要:从 input 提炼单行意图(换行压平、超 60 字截断;拿不到返回空串)
export function summarizeTool(tool: string, input: Record<string, unknown> | null | undefined): string {
  if (!input || typeof input !== "object") return "";
  let s = "";
  if (tool === "Bash") s = String(input.command || "");
  else if (tool === "Read" || tool === "Glob") s = String(input.file_path || input.pattern || input.path || "");
  else if (tool === "Grep") s = input.pattern ? '"' + input.pattern + '"' : "";
  else if (tool === "Edit" || tool === "Write" || tool === "MultiEdit") s = String(input.file_path || "");
  else if (tool === "WebFetch") s = String(input.url || "");
  else if (tool === "WebSearch") s = String(input.query || "");
  else if (tool === "Task") s = String(input.description || "");
  else {
    for (const k in input) {           // 其它工具:首个字符串参数
      if (typeof input[k] === "string") { s = input[k] as string; break; }
    }
  }
  s = String(s).replace(/\s+/g, " ").trim();
  return s.length > 60 ? s.slice(0, 59) + "…" : s;
}

// 结果计数:Grep→matches 数(非空行);Read/Bash→行数;其余工具拿不到 → 空串(不计)
export function resultTally(tool: string, text: string): string {
  if (!text) return "";
  const lines = text.split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  if (tool === "Grep") {
    let n = 0;
    for (const ln of lines) if (ln.trim()) n++;
    return n ? n + " matches" : "";
  }
  if (tool === "Read" || tool === "Bash") {
    return lines.length ? lines.length + " lines" : "";
  }
  return "";
}
