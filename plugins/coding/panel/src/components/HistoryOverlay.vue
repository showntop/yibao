<script setup lang="ts">
// 接续浮层(R4 阶段二 T7;对齐 chat.html:2388-2554 renderResumePopover/lastCard/historyRow):
//   区 1「上次会话」跨源检测卡——CC 卡 [继续](attach_cc 导入 → resumeSession);
//     Codex 卡 [原生续](attach_codex → resumeSession)/ [交接给 CC](关闭浮层走 handoff 流程);
//   区 2「译宝历史（本项目）」行——引擎徽标 + 时间 + 目录名 + prompt 首行 42 字 + status 着色
//     (done绿/running蓝/failed红/stopped琥珀),点击 resumeSession;
//   两区皆空 → 空态文案;数据未到 → 「加载中…」;overlay 空白点击/[关闭] 上抛 close。
// 数据获取(两路并发/失败降级)与浮层互收、esc 链都在 App;rows 由 App 按 normCwd 过滤好传入。
// chip 引擎过滤通道(filterAgent/onlyIfContent)在原码已无调用方,不移植。
import { computed, ref } from "vue";
import { relTime } from "../lib/format";
import type { LastSessions, SessionRow } from "../lib/types";
import { agentLabel } from "../stores/drivers";

const props = defineProps<{
  loading: boolean;          // 两路并发数据未到(「加载中…」)
  last: LastSessions | null; // 区 1 跨源检测(失败降级 null)
  rows: SessionRow[];        // 区 2 译宝历史(已按 normCwd 过滤)
  listErr: string | null;    // 区 2 加载失败(区 1 有卡时单独亮一行)
  curAgent: string;          // 空态文案里的引擎名(原取 curAgent)
  onAttachCc: (ccSid: string) => Promise<boolean>;     // 受理 true(浮层随 resume 关闭)/失败 false 解锁
  onAttachCodex: (sid: string) => Promise<boolean>;
}>();
const emit = defineEmits<{
  close: [];
  resume: [row: SessionRow]; // 区 2 行点击(App:resumeSession(row.id, row.agent))
  handoff: [];               // Codex 卡 [交接给 CC](App:关闭浮层 → handoff 流程)
}>();

const cc = computed(() => props.last?.cc || null);
const codex = computed(() => props.last?.codex || null);
const ccMeta = computed(() =>
  cc.value && cc.value.message_count != null ? (Number(cc.value.message_count) || 0) + " 条消息" : "");
const emptyText = computed(() =>
  props.listErr ? "加载失败：" + props.listErr
                : "新项目——直接输入任务开始（" + agentLabel(props.curAgent) + "）");

// attach 钮防连点:飞行中禁用;失败由 App 返回 false 解锁(成功则浮层关闭组件卸载)
const attachingCc = ref(false);
const attachingCodex = ref(false);
async function clickAttachCc(ccSid: string | undefined) {
  if (!ccSid || attachingCc.value) return;
  attachingCc.value = true;
  const ok = await props.onAttachCc(ccSid).catch(() => false);
  if (!ok) attachingCc.value = false;
}
async function clickAttachCodex(sid: string | undefined) {
  if (!sid || attachingCodex.value) return;
  attachingCodex.value = true;
  const ok = await props.onAttachCodex(sid).catch(() => false);
  if (!ok) attachingCodex.value = false;
}

// 区 2 行渲染辅助(对齐 historyRow:created_at 秒级 → 「M/D HH:MM」;目录名取末段;prompt 首行 42 字)
function rowTime(createdAt: number): string {
  if (!createdAt) return "";
  const d = new Date(createdAt * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (d.getMonth() + 1) + "/" + d.getDate() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
}
function cwdShort(p: string): string {
  return (p || "").split("/").filter(Boolean).pop() || p || "";
}
function firstLine(prompt: string): string {
  return String(prompt || "").split("\n")[0].slice(0, 42);
}
</script>

<template>
  <div id="history-overlay" @click.self="emit('close')">
    <div id="history-picker" @click.stop>
      <div id="history-body">
        <div v-if="loading" class="mempty">加载中…</div>
        <template v-else>
          <!-- 区 1「上次会话」:跨源每源最新一条,按各自存在性显示 -->
          <template v-if="cc || codex">
            <div class="sec-title">上次会话</div>
            <div v-if="cc" class="last-card">
              <div class="lc-head">
                <span class="lc-badge">CC</span>
                <span class="lc-time">{{ relTime(Date.now(), cc.ts) || "(未知时间)" }}</span>
              </div>
              <div class="lc-sum">{{ cc.summary || "(无摘要)" }}</div>
              <div v-if="ccMeta" class="lc-meta">{{ ccMeta }}</div>
              <div class="lc-acts">
                <button type="button" class="lc-act" :disabled="attachingCc" @click="clickAttachCc(cc.cc_session_id)">继续</button>
              </div>
            </div>
            <div v-if="codex" class="last-card">
              <div class="lc-head">
                <span class="lc-badge codex">Codex</span>
                <span class="lc-time">{{ relTime(Date.now(), codex.ts) || "(未知时间)" }}</span>
              </div>
              <div class="lc-sum">{{ codex.summary || "(无摘要)" }}</div>
              <div class="lc-note">交接 = 生成摘要交给 Claude Code，非完整搬移</div>
              <div class="lc-acts">
                <button type="button" class="lc-act" :disabled="attachingCodex" @click="clickAttachCodex(codex.session_id)">原生续</button>
                <button type="button" class="lc-act ghost" @click="emit('handoff')">交接给 CC</button>
              </div>
            </div>
          </template>

          <!-- 区 2「译宝历史（本项目）」:点击 resumeSession(row.id, row.agent) -->
          <template v-if="rows.length">
            <div class="sec-title">译宝历史（本项目）</div>
            <button
              v-for="row in rows"
              :key="row.id"
              type="button"
              class="hrow"
              @click="emit('resume', row)"
            >
              <span class="ht"><span class="hb" :class="{ codex: row.agent === 'codex' }">{{ row.agent === "codex" ? "Codex" : "CC" }}</span>{{ rowTime(row.created_at) }}</span>
              <span class="hc">{{ cwdShort(row.cwd) }}</span>
              <span class="hp">{{ firstLine(row.prompt) }}</span>
              <span class="hs" :class="row.status || ''">{{ row.status || "" }}</span>
            </button>
          </template>
          <div v-else-if="listErr && (cc || codex)" class="mempty">译宝历史加载失败：{{ listErr }}</div>

          <!-- 空态(两区皆空):新项目 -->
          <div v-if="!cc && !codex && !rows.length" class="mempty">{{ emptyText }}</div>
        </template>
      </div>
      <button id="history-close" type="button" class="pick-close" @click="emit('close')">关闭</button>
    </div>
  </div>
</template>
