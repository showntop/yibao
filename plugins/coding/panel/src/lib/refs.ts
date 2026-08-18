// @ 引用纯函数(移植 chat.html::2711-2713 composeRefs、:2705-2709 addAtRef 去重、
// :2694 renderAtRefs 的 basename 显示、:2776-2784 input 处理器的 @ 触发正则)。
// 组件无关,单测覆盖;store.send 经 composeRefs 组装引用段(唯一出处,不重复拼)。

// 发送时把 @ 引用 chips 组装进 prompt 尾:「\n\n引用文件:\n@a\n@b」;空 → ""
export function composeRefs(refs: string[]): string {
  return refs.length ? "\n\n引用文件:\n" + refs.map((r) => "@" + r).join("\n") : "";
}

// @ 触发正则:@ 后跟路径字符(字母数字 _ - . /)直到文本尾(对齐 chat.html:2777)
const AT_TRIGGER = /@([\w\-./]*)$/;

// 光标前文本里的 @ 触发片段:命中返回片段起点(start)与 query;未命中 null。
// start 供 atInsert 切删「@query」片段用(配合触发时缓存的 caret,不用 live 光标)。
export function matchAtQuery(textBeforeCaret: string): { start: number; query: string } | null {
  const m = AT_TRIGGER.exec(textBeforeCaret);
  if (!m) return null;
  return { start: textBeforeCaret.length - m[0].length, query: m[1] };
}

// chip 显示名:取路径末段(对齐 renderAtRefs 的 rel.split("/").pop();
// 空串/尾斜杠得空串,保持与原 parity)
export function basename(p: string): string {
  const seg = p.split("/").pop();
  return seg ?? p; // split 恒非空数组,?? 仅满足类型
}

// 去重追加(对齐 addAtRef:同文件重复引用无意义);已存在 → 返回原数组(同引用),否则新数组
export function pushRef(refs: string[], rel: string): string[] {
  return refs.indexOf(rel) >= 0 ? refs : [...refs, rel];
}
