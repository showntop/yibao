<script setup lang="ts">
// 文件改动卡(对齐 chat.html:1193-1359):默认展开,头行「✎ path +a -d tool」(title 全路径)。
// 体:old+new → lcsLines 着色 diff;仅 new 且 MultiEdit → multiEditDiff 逐段(段头「第 N 处」),
// 解析失败退纯文本;仅 new(Write)→ 全文 add(末尾换行不产生幻影空行);无 old/new → note 说明。
import { computed } from "vue";
import type { RenderItem } from "../stores/session";
import { diffStats, lcsLines, multiEditDiff, type DiffLine, type MultiEditSegment } from "../lib/diff";

type FileEditItem = Extract<RenderItem, { type: "fileedit" }>;
const props = defineProps<{ item: FileEditItem }>();

type Stats = { a: number; d: number };
type BodyModel =
  | { kind: "diff"; lines: DiffLine[]; stats: Stats }
  | { kind: "segments"; segments: MultiEditSegment[]; stats: Stats }
  | { kind: "write"; lines: string[]; stats: Stats }
  | { kind: "plain"; lines: string[]; stats: null }   // MultiEdit JSON 解析失败兜底
  | { kind: "note"; text: string; stats: null };      // runner 未推内容(codex file_change 等)

const model = computed<BodyModel>(() => {
  const { tool, old: o, new: n } = props.item;
  if (o != null && n != null) {
    const lines = lcsLines(o, n);
    return { kind: "diff", lines, stats: diffStats(lines) };
  }
  if (n != null) {
    if (tool === "MultiEdit") {
      const d = multiEditDiff(n);
      if (d) {
        const stats = { a: 0, d: 0 };
        for (const s of d.segments) {
          const st = diffStats(s.lines);
          stats.a += st.a; stats.d += st.d;
        }
        return { kind: "segments", segments: d.segments, stats };
      }
      return { kind: "plain", lines: String(n).split("\n"), stats: null };
    }
    // Write:全文按新增着色
    const lines = String(n).split("\n");
    if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop(); // 末尾换行不产生幻影空行
    return { kind: "write", lines, stats: { a: lines.length, d: 0 } };
  }
  return {
    kind: "note",
    text: "仅记录改动路径（runner 未推送 old/new）；" + (tool ? "工具：" + tool + "。" : "") +
      "后续 runner 推送内容后此处自动展示。",
    stats: null,
  };
});

const displayPath = computed(() => props.item.path || "(未知路径)");
const DIFF_CLS: Record<DiffLine["type"], string> = { add: "diff-add", del: "diff-del", ctx: "diff-ctx" };
</script>

<template>
  <div class="row ai">
    <div class="card fileedit">
      <div class="edit-head" :title="displayPath">✎ {{ displayPath
        }}<template v-if="model.stats"> <span class="add">+{{ model.stats.a }}</span> <span class="del">-{{ model.stats.d }}</span></template> <span class="tool-tag">{{ item.tool }}</span></div>
      <div class="card-body open">
        <template v-if="model.kind === 'diff'">
          <div v-for="(l, i) in model.lines" :key="i" class="dl" :class="DIFF_CLS[l.type]">{{ l.text || " " }}</div>
        </template>
        <template v-else-if="model.kind === 'segments'">
          <template v-for="(seg, si) in model.segments" :key="si">
            <div class="seg-head">{{ seg.head }}</div>
            <div v-for="(l, i) in seg.lines" :key="i" class="dl" :class="DIFF_CLS[l.type]">{{ l.text || " " }}</div>
          </template>
        </template>
        <template v-else-if="model.kind === 'write'">
          <div v-for="(l, i) in model.lines" :key="i" class="dl diff-add">{{ l || " " }}</div>
        </template>
        <template v-else-if="model.kind === 'plain'">
          <div v-for="(l, i) in model.lines" :key="i" class="dl s">{{ l || " " }}</div>
        </template>
        <div v-else class="note">{{ model.text }}</div>
      </div>
    </div>
  </div>
</template>
