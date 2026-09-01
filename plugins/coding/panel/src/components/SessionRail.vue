<script lang="ts">
// 行类型与活体文案映射从本文件 export 供壳(T6 App.vue)用——<script setup> 不允许值导出,走普通脚本块。
import type { RailLive } from "../stores/stations";

export interface RailRow {
  id: string; title: string; subtitle: string; agent: string; cwd: string;
  live: RailLive; boundStationId: number | null;
}

export const LIVE_TEXT = { waiting: "等待审批", running: "运行中", idle: "空闲" } as const;
</script>

<script setup lang="ts">
// 会话抽屉(2026-09 入口合一):会话唯一的家——
//   顶部「上次会话」卡(聚焦工位项目语境,跨源检测:CC [继续] / Codex [原生续][交接给 CC]);
//   会话列表分「本项目 / 其他项目」两组(行=标题+活体点+副文,点击 加入工位/聚焦,行内 停止)。
// 原「接续浮层」(HistoryOverlay)与全局列表双入口 retired;数据编排(拉取/分组/上次会话)在壳,
// 卡片动作经函数 props 上抛 → 壳调聚焦工位暴露方法(attachCc/attachCodex/startCodexHandoff)。
// 本组件只渲染 props 与转发 emits,零 invoke、零 store。
import { computed, ref } from "vue";
import { relTime } from "../lib/format";
import type { LastSessions } from "../lib/types";
import { agentLabel } from "../stores/drivers";

interface Props {
  projectRows: RailRow[];       // 本项目会话行(壳按聚焦工位 cwd 分好组)
  otherRows: RailRow[];         // 其他项目会话行
  projectLabel: string;         // 聚焦工位项目名(分区标题/空态语境)
  last: LastSessions | null;    // 上次会话跨源检测(失败降级 null → 空态)
  lastLoading: boolean;         // 上次会话数据未到
  onAttachCc: (ccSid: string) => Promise<boolean>;   // 受理 true(抽屉随恢复收起)/失败 false 解锁
  onAttachCodex: (sid: string) => Promise<boolean>;
  addDisabled?: boolean;        // 满 3 工位时壳禁用「+ 新工位」
}

const props = withDefaults(defineProps<Props>(), { addDisabled: false });

const emit = defineEmits<{
  join: [sid: string, agent: string]; // 点未绑行 = 加入工位
  stop: [sid: string];                // 行内「停止」
  "new-session": [];                  // 顶部「+ 新工位」(满 3 时壳侧禁用)
  "focus-station": [id: number];      // 点已绑行 = 聚焦对应工位
  "close-drawer": [];                 // 抽屉模式:点罩层/选中后收抽屉
  handoff: [];                        // 上次会话 Codex [交接给 CC](壳:关抽屉 → 聚焦工位交接流程)
}>();

const cc = computed(() => props.last?.cc || null);
const codex = computed(() => props.last?.codex || null);
const ccMeta = computed(() =>
  cc.value && cc.value.message_count != null ? (Number(cc.value.message_count) || 0) + " 条消息" : "");

// attach 钮防连点:飞行中禁用;失败由壳返回 false 解锁(成功则抽屉收起)
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

// 行点击按绑定态分流:已绑 → 聚焦工位;未绑 → 加入工位;两种都顺手收抽屉
function onRow(r: RailRow) {
  if (r.boundStationId !== null) { emit("focus-station", r.boundStationId); emit("close-drawer"); }
  else { emit("join", r.id, r.agent); emit("close-drawer"); }
}
</script>

<template>
  <div class="rail-mask" @click="emit('close-drawer')"></div>
  <aside class="rail rail-drawer">
    <div class="rail-head">
      <span class="rail-title">会话</span>
      <button type="button" class="rail-add" :disabled="addDisabled" @click="emit('new-session')">+ 新工位</button>
    </div>

    <div class="rail-rows">
      <!-- 上次会话(聚焦工位项目语境;跨源检测失败降级为无卡,不拖垮列表) -->
      <div class="rail-sec">
        <div class="rail-sec-title">上次会话<template v-if="projectLabel"> · {{ projectLabel }}</template></div>
        <div v-if="lastLoading" class="rail-last-loading">读取中…</div>
        <template v-else-if="cc || codex">
          <div v-if="cc" class="rail-last-card">
            <div class="rlc-head">
              <span class="rlc-badge">CC</span>
              <span class="rlc-time">{{ relTime(Date.now(), cc.ts) || "(未知时间)" }}</span>
            </div>
            <div class="rlc-sum">{{ cc.summary || "(无摘要)" }}</div>
            <div v-if="ccMeta" class="rlc-meta">{{ ccMeta }}</div>
            <div class="rlc-acts">
              <button type="button" class="rlc-act" :disabled="attachingCc" @click="clickAttachCc(cc.cc_session_id)">继续</button>
            </div>
          </div>
          <div v-if="codex" class="rail-last-card">
            <div class="rlc-head">
              <span class="rlc-badge codex">Codex</span>
              <span class="rlc-time">{{ relTime(Date.now(), codex.ts) || "(未知时间)" }}</span>
            </div>
            <div class="rlc-sum">{{ codex.summary || "(无摘要)" }}</div>
            <div class="rlc-meta">交接 = 生成摘要交给 Claude Code，非完整搬移</div>
            <div class="rlc-acts">
              <button type="button" class="rlc-act" :disabled="attachingCodex" @click="clickAttachCodex(codex.session_id)">原生续</button>
              <button type="button" class="rlc-act ghost" @click="emit('handoff')">交接给 CC</button>
            </div>
          </div>
        </template>
        <div v-else class="rail-last-loading">
          {{ projectLabel ? "该项目还没有历史会话——直接输入任务开始（" + agentLabel("claude-code") + "）" : "先选项目目录，这里会显示上次会话" }}
        </div>
      </div>

      <!-- 本项目 -->
      <div class="rail-sec">
        <div class="rail-sec-title">本项目<template v-if="projectLabel"> · {{ projectLabel }}</template></div>
        <div
          v-for="r in projectRows" :key="r.id" class="rail-row" :class="{ bound: r.boundStationId !== null }"
          @click="onRow(r)"
        >
          <div class="rail-row-title">{{ r.title }}</div>
          <div class="rail-row-sub">
            <span v-if="r.boundStationId !== null" class="rail-badge">工位 {{ r.boundStationId }}</span>
            <span class="live-dot" :class="r.live"></span>
            {{ r.subtitle }}
          </div>
          <button
            v-if="r.live !== 'idle'" type="button" class="rail-stop"
            @click.stop="emit('stop', r.id)"
          >停止</button>
        </div>
        <div v-if="!projectRows.length" class="rail-empty">本项目还没有会话</div>
      </div>

      <!-- 其他项目 -->
      <div v-if="otherRows.length" class="rail-sec">
        <div class="rail-sec-title">其他项目</div>
        <div
          v-for="r in otherRows" :key="r.id" class="rail-row" :class="{ bound: r.boundStationId !== null }"
          @click="onRow(r)"
        >
          <div class="rail-row-title">{{ r.title }}</div>
          <div class="rail-row-sub">
            <span v-if="r.boundStationId !== null" class="rail-badge">工位 {{ r.boundStationId }}</span>
            <span class="live-dot" :class="r.live"></span>
            {{ r.subtitle }}
          </div>
          <button
            v-if="r.live !== 'idle'" type="button" class="rail-stop"
            @click.stop="emit('stop', r.id)"
          >停止</button>
        </div>
      </div>
    </div>
  </aside>
</template>
