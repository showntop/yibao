<script setup lang="ts">
// Codex session 选择器(R4 阶段二 T7;对齐 chat.html:2072-2119 showHandoffPicker):
// handoff_list 多条时弹出——条目 fmtTs(timestamp)+first_line,点选上抛 pick;
// backdrop 点击 / [取消] 上抛 close。fixed 弹层(iframe srcdoc 里 absolute 含块不可靠),
// 静态定位 header 下方靠右(同 agent picker)。互收与 esc 链在 App(openLayer="handoff")。
import { fmtTs } from "../lib/format";
import type { HandoffSessionItem } from "../lib/types";

defineProps<{ sessions: HandoffSessionItem[] }>();
const emit = defineEmits<{
  pick: [sid: string];
  close: [];
}>();
</script>

<template>
  <div id="handoff-backdrop" @click="emit('close')"></div>
  <div id="handoff-picker" @click.stop>
    <div
      v-for="s in sessions"
      :key="s.session_id"
      class="pick-item"
      @click="emit('pick', s.session_id)"
    >
      <span class="ts">{{ fmtTs(s.timestamp) || "(未知时间)" }}</span>
      <span class="fl">{{ s.first_line || "(无摘要)" }}</span>
    </div>
    <button type="button" class="pick-close" @click="emit('close')">取消</button>
  </div>
</template>
