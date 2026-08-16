<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { loadConn } from "../api/connection";
import { useFeed } from "../state/feed";
import { useReminders } from "../state/reminders";

const router = useRouter();
// 顶部两段：「动态 | 提醒」；互斥显示，两份数据进页各拉一次（切换即显不等请求）
const tab = ref<"feed" | "reminders">("feed");
// shallowRef：onMounted 里赋值要触发重渲染（与 Chat/Approvals 同模式）
const feed = shallowRef<ReturnType<typeof useFeed> | null>(null);
const reminders = shallowRef<ReturnType<typeof useReminders> | null>(null);
// 取消确认（二次点击制，不弹窗）：id → 超时句柄；3s 未复点自动退出确认态
const confirming = ref<Record<string, number>>({});

onMounted(async () => {
  const conn = await loadConn();
  if (!conn) return router.replace("/pairing");
  // useFeed 默认开 30s 轮询（M3 进行中区块靠它近实时）；卸载侧 stop
  feed.value = useFeed(conn);
  reminders.value = useReminders(conn);
  void feed.value.refresh();
  void reminders.value.refresh();
});

// 卸载清理（M2 评审移交）：确认态的 3s 超时句柄离页即清——不留悬挂定时器持有
// 已卸载组件的闭包（泄漏），页重建后也不该有旧定时器回来动 confirming；
// M3 增：feed 轮询 interval 一并停（跨页存留即泄漏，且会持续打 /v1/feed）
onUnmounted(() => {
  feed.value?.stop();
  for (const t of Object.values(confirming.value)) window.clearTimeout(t);
  confirming.value = {};
});

// ts 为 unix 秒：今天只显时分，跨天补「M月D日」前缀（Feed 是回看流，精确到分足够）
function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return d.toDateString() === new Date().toDateString() ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

const KIND_LABEL: Record<string, string> = { task: "任务", reminder: "提醒", event: "事件" };
// 进行中 kind 徽章（服务端只有 agent/script 两类：agents 库任务与后台盯命令）
const RUN_KIND_LABEL: Record<string, string> = { agent: "智能体", script: "脚本" };

// 取消按钮：首点进确认态（文案变「确认取消？」），3s 内复点才真取消——
// 不弹窗、不引左滑库（YAGNI），误触成本两步刚好
function askCancel(id: string): void {
  if (!reminders.value) return;
  const timer = confirming.value[id];
  if (timer) {
    window.clearTimeout(timer);
    delete confirming.value[id];
    void reminders.value.cancel(id);
    return;
  }
  confirming.value[id] = window.setTimeout(() => delete confirming.value[id], 3000);
}

function refreshAll(): void {
  void feed.value?.refresh();
  void reminders.value?.refresh();
}
</script>

<template>
  <div class="feed">
    <header class="head">
      <div class="seg">
        <button :class="{ on: tab === 'feed' }" @click="tab = 'feed'">动态</button>
        <button :class="{ on: tab === 'reminders' }" @click="tab = 'reminders'">提醒</button>
      </div>
      <button class="ghost" @click="refreshAll">↻ 刷新</button>
    </header>

    <main class="list">
      <!-- 动态段：倒序列表 + kind 三色轻着色（task 蓝 / reminder 橙 / event 绿） -->
      <template v-if="tab === 'feed'">
        <p v-if="!feed" class="empty">加载中…</p>
        <template v-else>
          <!-- 进行中区块（M3）：running_tasks 实时卡——kind 徽章 + label + prompt 单行截断；
               30s 轮询驱动，任务完成后条目消失并进下方动态流；空时整体隐藏 -->
          <div v-if="feed.running.value.length" class="running">
            <p class="r-head">进行中 · {{ feed.running.value.length }}</p>
            <div v-for="t in feed.running.value" :key="t.id" class="r-item">
              <span class="r-badge" :class="`k-${t.kind}`">{{ RUN_KIND_LABEL[t.kind] ?? "任务" }}</span>
              <div class="r-body">
                <p class="r-label">{{ t.label }}</p>
                <p v-if="t.prompt" class="r-prompt">{{ t.prompt }}</p>
              </div>
            </div>
          </div>
          <p v-if="feed.stats.value" class="statline">
            24 小时完成 {{ feed.stats.value.done_24h ?? 0 }} · 进行中 {{ feed.running.value.length }} · 待提醒
            {{ feed.stats.value.pending_reminders ?? 0 }}
          </p>
          <p v-if="feed.items.value.length === 0" class="empty">还没有动态——译宝忙起来就有了</p>
          <div
            v-for="it in feed.items.value"
            :key="it.id"
            class="item"
            :class="`k-${it.kind}`"
          >
            <div class="body">
              <p class="text">{{ it.text }}</p>
              <span class="time">{{ fmtTime(it.ts) }} · {{ KIND_LABEL[it.kind] ?? "事件" }}</span>
            </div>
          </div>
        </template>
      </template>

      <!-- 提醒段：待触发列表 + 二次点击取消；when 为服务端拼好的展示串 -->
      <template v-else>
        <p v-if="!reminders" class="empty">加载中…</p>
        <template v-else>
          <p v-if="reminders.error.value" class="err">{{ reminders.error.value }}</p>
          <p v-if="reminders.items.value.length === 0" class="empty">没有待触发的提醒</p>
          <div v-for="r in reminders.items.value" :key="r.id" class="rem">
            <div class="body">
              <p class="text">{{ r.text }}</p>
              <span class="time">{{ r.when }}</span>
            </div>
            <button class="cancel" :class="{ arm: confirming[r.id] }" @click="askCancel(r.id)">
              {{ confirming[r.id] ? "确认取消？" : "取消" }}
            </button>
          </div>
        </template>
      </template>
    </main>
  </div>
</template>

<style scoped>
.feed { display: flex; flex-direction: column; height: 100dvh;
  padding-bottom: calc(52px + env(safe-area-inset-bottom)); /* TabBar 让位 */ }
.head { display: flex; justify-content: space-between; align-items: center; padding: 6px 12px; }
.ghost { background: none; border: none; color: #2f6fed; font-size: 14px; }
/* 分段控件：胶囊底 + 选中高亮 */
.seg { display: flex; background: rgba(128, 128, 128, 0.14); border-radius: 11px; padding: 2px; }
.seg button { border: none; background: none; font-size: 14px; padding: 5px 16px; border-radius: 9px; color: #8e8e93; }
.seg button.on { background: var(--bg, #fff); color: #1d1d1f; font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12); }
.list { flex: 1; overflow-y: auto; padding: 8px 12px 12px; display: flex; flex-direction: column; gap: 8px; }
.statline { font-size: 12px; color: #8e8e93; margin: 0 0 4px; }
/* 进行中区块（M3）：橙色调「活着的」容器 + 呼吸点；条目 = kind 徽章 + label + prompt 截断 */
.running { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-radius: 12px;
  background: rgba(255, 159, 10, 0.08); border-left: 3px solid #ff9f0a; }
.r-head { margin: 0; font-size: 12px; color: #b25000; font-weight: 600; }
.r-head::before { content: "●"; margin-right: 4px; font-size: 8px;
  animation: breath 1.6s ease-in-out infinite; }
.r-item { display: flex; gap: 8px; align-items: flex-start; }
.r-badge { flex-shrink: 0; font-size: 11px; padding: 2px 7px; border-radius: 7px; margin-top: 1px;
  background: rgba(128, 128, 128, 0.16); }
.r-badge.k-agent { background: rgba(47, 111, 237, 0.16); }
.r-badge.k-script { background: rgba(52, 199, 89, 0.18); }
.r-body { flex: 1; min-width: 0; }
.r-label { margin: 0; font-size: 13px; font-weight: 500; }
.r-prompt { margin: 1px 0 0; font-size: 11px; color: #8e8e93; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; } /* 单行截断：prompt 可能是整段长命令 */
@keyframes breath { 50% { opacity: 0.25; } }
.empty { text-align: center; opacity: 0.55; padding: 40px 0; }
.err { color: #ff453a; font-size: 13px; }
/* kind 三色轻着色：左侧彩条 + 同色淡底（轻，不抢正文） */
.item { display: flex; gap: 10px; padding: 10px 12px; border-radius: 12px;
  border-left: 3px solid transparent; background: rgba(128, 128, 128, 0.07); }
.item.k-task { border-left-color: #2f6fed; background: rgba(47, 111, 237, 0.08); }
.item.k-reminder { border-left-color: #ff9f0a; background: rgba(255, 159, 10, 0.09); }
.item.k-event { border-left-color: #34c759; background: rgba(52, 199, 89, 0.09); }
.body { flex: 1; min-width: 0; }
.text { margin: 0; font-size: 14px; line-height: 1.45; word-break: break-word; }
.time { font-size: 11px; color: #8e8e93; }
/* 提醒行：正文 + 取消按钮 */
.rem { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-radius: 12px;
  background: rgba(128, 128, 128, 0.07); }
.cancel { border: none; border-radius: 10px; padding: 7px 12px; font-size: 13px;
  background: rgba(128, 128, 128, 0.16); color: inherit; flex-shrink: 0; }
.cancel.arm { background: #ff453a; color: #fff; font-weight: 600; }
</style>
