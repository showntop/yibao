<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import { useRouter } from "vue-router";
import { loadConn, clearConn } from "../api/connection";
import { useChat } from "../state/chat";
import ConnBar from "../components/ConnBar.vue";

const router = useRouter();
const input = ref("");
// shallowRef：onMounted 里赋值要触发重渲染（普通 let 赋值模板不更新）
const chat = shallowRef<ReturnType<typeof useChat> | null>(null);

// 兜底重连：浏览器实测 EventSource 断线重连若落在服务端下线窗口（CORS 归类为致命），
// Chromium 不再自愈——state 停留 error。5s 后手动 start() 重建连接；open 即取消。
let retryTimer: number | undefined;

// 顶层同步注册：await 之后注册的 watch 脱离组件作用域（无主 watcher，卸载后仍跑）
watch(
  () => chat.value?.stream.state.value,
  (s) => {
    window.clearTimeout(retryTimer);
    if (s === "error") retryTimer = window.setTimeout(() => chat.value?.stream.start(), 5000);
  },
);

onMounted(async () => {
  const conn = await loadConn();
  if (!conn) return router.replace("/pairing");
  chat.value = useChat(conn); // 构造即 start，无需再显式连
  void chat.value.syncPendingCount(); // 从审批页返回也会重跑（onMounted 每次进页触发）
});
onUnmounted(() => {
  window.clearTimeout(retryTimer);
  chat.value?.stream.stop();
});

async function onSend() {
  if (!chat.value || !input.value.trim()) return;
  const t = input.value;
  input.value = "";
  await chat.value.send(t);
}

// 坏配置出口：清掉落盘连接回配对页（否则 malformed 配置会困死在 /chat）
async function rePair() {
  chat.value?.stream.stop();
  await clearConn();
  router.replace("/pairing");
}
</script>

<template>
  <div class="chat" v-if="chat">
    <header class="head">
      <ConnBar :state="chat.stream.state.value" />
      <div class="actions">
        <!-- 待批角标：有待批才显示，点进审批页处理 -->
        <router-link v-if="chat.pendingCount.value > 0" class="badge" to="/approvals">
          ⏳ {{ chat.pendingCount.value }}
        </router-link>
        <button class="ghost" @click="chat.newChat()">新对话</button>
        <button class="ghost" @click="rePair">重新配对</button>
      </div>
    </header>
    <main class="list">
      <p v-for="(m, i) in chat.messages.value" :key="i" class="msg" :class="m.role">
        {{ m.text }}<span v-if="m.role === 'assistant' && !m.done" class="cursor">▍</span>
        <span v-if="m.interrupted" class="stopped">（已打断）</span>
      </p>
      <p v-if="chat.error.value" class="err">{{ chat.error.value }}</p>
    </main>
    <footer class="inputbar">
      <button v-if="chat.busy.value" class="stop" @click="chat.interrupt()">⏹</button>
      <input
        v-model="input"
        placeholder="对译宝说…"
        enterkeyhint="send"
        @keydown.enter.prevent="onSend"
        :disabled="chat.busy.value"
      />
      <button class="send" :disabled="!input.trim() || chat.busy.value" @click="onSend">发送</button>
    </footer>
  </div>
  <p v-else style="padding:24px">加载中…</p>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; height: 100dvh; }
.head { display: flex; justify-content: space-between; align-items: center; }
.ghost { background: none; border: none; color: #2f6fed; font-size: 14px; }
.actions { display: flex; gap: 4px; align-items: center; }
.badge { font-size: 13px; color: #b25000; background: rgba(255, 159, 10, 0.15); border-radius: 10px; padding: 3px 8px; text-decoration: none; }
.list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 82%; padding: 10px 12px; border-radius: 14px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #2f6fed; color: #fff; }
.msg.assistant { align-self: flex-start; background: rgba(128, 128, 128, 0.16); }
.cursor { animation: blink 1s infinite; }
.stopped { font-size: 12px; opacity: 0.6; }
.err { color: #ff453a; font-size: 13px; }
.inputbar { display: flex; gap: 8px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom)); }
.inputbar input { flex: 1; padding: 10px 12px; border-radius: 12px; border: 1px solid #ccc; background: transparent; color: inherit; font-size: 15px; }
.stop, .send { padding: 10px 14px; border-radius: 12px; border: none; }
.stop { background: #ff453a; color: #fff; }
.send { background: #2f6fed; color: #fff; }
.send:disabled { opacity: 0.4; }
@keyframes blink { 50% { opacity: 0; } }
</style>
