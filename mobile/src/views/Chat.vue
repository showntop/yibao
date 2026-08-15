<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import { useRouter } from "vue-router";
import { loadConn, clearConn } from "../api/connection";
import { useChat } from "../state/chat";
import { useSessions } from "../state/sessions";
import ConnBar from "../components/ConnBar.vue";
import MessageBody from "../components/MessageBody.vue";

const router = useRouter();
const input = ref("");
// shallowRef：onMounted 里赋值要触发重渲染（普通 let 赋值模板不更新）
const chat = shallowRef<ReturnType<typeof useChat> | null>(null);
const sessions = shallowRef<ReturnType<typeof useSessions> | null>(null);
const drawerOpen = ref(false);
// 抽屉列表快照（computed 解开双层 ref，模板/vue-tsc 都省事）
const sessionList = computed(() => sessions.value?.list.value ?? []);

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
  sessions.value = useSessions(conn);
  void chat.value.syncPendingCount(); // 从审批页返回也会重跑（onMounted 每次进页触发）
});
onUnmounted(() => {
  window.clearTimeout(retryTimer);
  chat.value?.stream.stop();
});

// 打开会话抽屉：每次都重新拉列表（服务端桶随时在变，列表很便宜）
function openDrawer(): void {
  drawerOpen.value = true;
  void sessions.value?.refresh();
}

// 点选会话：拉历史 → 重建消息 → 切 conversationId（后续 send 落到该桶）
async function pickSession(cid: string): Promise<void> {
  if (!chat.value || !sessions.value) return;
  const items = await sessions.value.open(cid);
  chat.value.loadHistory(items);
  chat.value.conversationId.value = cid;
  drawerOpen.value = false;
}

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
        <button class="ghost" @click="openDrawer">历史</button>
        <button class="ghost" @click="chat.newChat()">新对话</button>
        <button class="ghost" @click="rePair">重新配对</button>
      </div>
    </header>
    <main class="list">
      <!-- 消息用 div：assistant done 走 Markdown（块级元素），p 内嵌块级不合规范 -->
      <div v-for="(m, i) in chat.messages.value" :key="i" class="msg" :class="m.role">
        <MessageBody v-if="m.role === 'assistant' && m.done" :text="m.text" />
        <template v-else>{{ m.text }}<span v-if="m.role === 'assistant' && !m.done" class="cursor">▍</span></template>
        <span v-if="m.interrupted" class="stopped">（已打断）</span>
      </div>
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

    <!-- 会话抽屉：覆层点空白关闭；列表项 = preview 两行截断 + 桶内消息数 -->
    <div v-if="drawerOpen" class="mask" @click.self="drawerOpen = false">
      <aside class="drawer">
        <header class="d-head">
          <h2>历史会话</h2>
          <button class="ghost" @click="drawerOpen = false">关闭</button>
        </header>
        <div class="d-list">
          <p v-if="sessionList.length === 0" class="d-empty">
            {{ sessions?.loading.value ? "正在拉取…" : "还没有历史会话" }}
          </p>
          <button
            v-for="s in sessionList"
            :key="s.id"
            class="d-item"
            :class="{ cur: s.id === chat.conversationId.value }"
            @click="pickSession(s.id)"
          >
            <span class="d-preview">{{ s.preview || "（无内容）" }}</span>
            <span class="d-turns">{{ s.turns }} 条消息</span>
          </button>
        </div>
        <p class="d-note">每个会话只保留最近 10 轮，回显即最近上下文</p>
      </aside>
    </div>
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
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); z-index: 20; display: flex; justify-content: flex-end; }
.drawer { width: min(78vw, 320px); height: 100%; background: var(--bg, #fff); display: flex; flex-direction: column;
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.18); }
.d-head { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px 8px; }
.d-head h2 { font-size: 16px; margin: 0; }
.d-list { flex: 1; overflow-y: auto; padding: 4px 8px; display: flex; flex-direction: column; gap: 6px; }
.d-empty { color: #888; font-size: 14px; padding: 16px 8px; }
.d-item { display: flex; flex-direction: column; gap: 4px; align-items: stretch; text-align: left; padding: 10px 12px;
  border: none; border-radius: 12px; background: rgba(128, 128, 128, 0.08); }
.d-item.cur { background: rgba(47, 111, 237, 0.14); }
.d-preview { font-size: 14px; line-height: 1.4; overflow: hidden; display: -webkit-box;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2; } /* 两行截断 */
.d-turns { font-size: 11px; color: #999; }
.d-note { margin: 0; padding: 8px 14px calc(10px + env(safe-area-inset-bottom)); font-size: 11px; color: #aaa; }
</style>
