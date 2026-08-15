<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { loadConn } from "../api/connection";
import { buildEventsUrl, useEventStream } from "../api/events";
import { useApprovals, type PendingConfirm } from "../state/approvals";

const router = useRouter();
// shallowRef：onMounted 里赋值要触发重渲染
const approvals = shallowRef<ReturnType<typeof useApprovals> | null>(null);
// 自建事件流（与 Chat 同模式）：进入页面 start / 离开 stop；只消费 confirmation_needed 帧
const stream = shallowRef<ReturnType<typeof useEventStream> | null>(null);
// 每卡独立的「本会话不再询问」勾选（默认关；批准时随 remember 传给服务端）
const remembers = ref<Record<string, boolean>>({});
// gone 提示位：decide 返回 gone 时亮一条（该审批已在桌面处理）
const goneNote = ref("");

onMounted(async () => {
  const conn = await loadConn();
  if (!conn) return router.replace("/pairing");
  stream.value = useEventStream(() => buildEventsUrl(conn));
  approvals.value = useApprovals(conn, stream.value);
  stream.value.start(); // 先挂 handler 再连接：start 后到达的 confirmation_needed 才不漏
  void approvals.value.refresh();
});
onUnmounted(() => stream.value?.stop());

async function onDecide(p: PendingConfirm, approved: boolean) {
  if (!approvals.value) return;
  const res = await approvals.value.decide(p.id, approved, !!remembers.value[p.id]);
  if (res === "gone") goneNote.value = "该审批已在桌面处理，列表已刷新";
}
</script>

<template>
  <div class="approvals">
    <header class="head">
      <button class="ghost" @click="router.replace('/chat')">← 返回</button>
      <span class="title">待审批</span>
      <span class="count" v-if="approvals">{{ approvals.pendings.value.length }} 项</span>
    </header>
    <main class="list">
      <p v-if="!approvals" style="padding:24px">加载中…</p>
      <template v-else>
        <p v-if="approvals.error.value" class="err">{{ approvals.error.value }}</p>
        <p v-if="goneNote" class="gone">{{ goneNote }}</p>
        <p v-if="!approvals.loading.value && approvals.pendings.value.length === 0" class="empty">
          没有待审批的事项
        </p>
        <div
          v-for="p in approvals.pendings.value"
          :key="p.id"
          class="card"
          :class="{ high: p.risk >= 3 }"
        >
          <div class="row">
            <span class="skill">{{ p.skill_id }}</span>
            <span class="risk" :class="p.risk >= 3 ? 'r3' : 'r2'">风险 L{{ p.risk }}</span>
          </div>
          <p class="summary">{{ p.summary }}</p>
          <p class="time">{{ new Date(p.created_at * 1000).toLocaleTimeString() }}</p>
          <label class="remember">
            <input type="checkbox" v-model="remembers[p.id]" /> 本会话不再询问
          </label>
          <div class="btns">
            <button class="deny" @click="onDecide(p, false)">拒绝</button>
            <button class="ok" @click="onDecide(p, true)">批准</button>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.approvals { display: flex; flex-direction: column; height: 100dvh; }
.head { display: flex; justify-content: space-between; align-items: center; padding: 0 12px; }
.title { font-size: 16px; font-weight: 600; }
.count { font-size: 13px; opacity: 0.6; }
.ghost { background: none; border: none; color: #2f6fed; font-size: 14px; }
.list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.err { color: #ff453a; font-size: 13px; }
.gone { color: #b25000; font-size: 13px; background: rgba(255, 159, 10, 0.12); border-radius: 10px; padding: 8px 10px; }
.empty { text-align: center; opacity: 0.55; padding: 40px 0; }
.card { border: 1px solid rgba(128, 128, 128, 0.25); border-radius: 14px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.card.high { border-color: rgba(255, 69, 58, 0.45); }
.row { display: flex; justify-content: space-between; align-items: center; }
.skill { font-weight: 600; font-size: 15px; }
.risk { font-size: 12px; padding: 2px 8px; border-radius: 8px; }
.risk.r2 { background: rgba(255, 204, 0, 0.18); }
.risk.r3 { background: rgba(255, 69, 58, 0.16); color: #c0392b; }
.summary { font-size: 14px; word-break: break-all; background: rgba(128, 128, 128, 0.1); border-radius: 10px; padding: 8px 10px; }
.time { font-size: 12px; opacity: 0.5; }
.remember { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.btns { display: flex; gap: 8px; }
.btns button { flex: 1; padding: 10px 0; border-radius: 12px; border: none; font-size: 15px; }
.deny { background: rgba(128, 128, 128, 0.18); }
.ok { background: #2f6fed; color: #fff; }
</style>
