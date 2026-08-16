<script setup lang="ts">
import { onMounted, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { loadConn, type ConnConfig } from "../api/connection";

const router = useRouter();
// shallowRef：onMounted 里赋值要触发重渲染
const conn = shallowRef<ConnConfig | null>(null);

onMounted(async () => {
  conn.value = await loadConn();
  if (!conn.value) return router.replace("/pairing");
});

// token 打码展示：连接信息是排查用，不整串亮在屏幕上
function maskToken(t: string): string {
  return t.length <= 8 ? "••••••" : `${t.slice(0, 4)}••••${t.slice(-4)}`;
}
</script>

<template>
  <div class="settings">
    <header class="head"><span class="title">设置</span></header>
    <main class="list">
      <!-- 连接信息（本任务占位框架；T3 充实） -->
      <section class="card">
        <h2>连接</h2>
        <p v-if="conn" class="row"><span class="k">大脑地址</span><span class="v">{{ conn.host }}</span></p>
        <p v-if="conn" class="row"><span class="k">令牌</span><span class="v mono">{{ maskToken(conn.token) }}</span></p>
        <p v-else class="row">加载中…</p>
      </section>

      <!-- 记忆库入口：M2 只做占位（disabled），T3 落列表页 -->
      <section class="card">
        <h2>数据</h2>
        <button class="entry" disabled>📖 记忆库（即将上线）</button>
      </section>

      <section class="card">
        <h2>关于</h2>
        <p class="row"><span class="k">版本</span><span class="v">译宝伴生端 v0.1</span></p>
        <p class="note">与桌面译宝同一颗大脑：对话、审批、动态全同步。</p>
      </section>
    </main>
  </div>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; height: 100dvh;
  padding-bottom: calc(52px + env(safe-area-inset-bottom)); /* TabBar 让位 */ }
.head { display: flex; align-items: center; padding: 0 12px; }
.title { font-size: 16px; font-weight: 600; }
.list { flex: 1; overflow-y: auto; padding: 8px 12px 12px; display: flex; flex-direction: column; gap: 10px; }
.card { border: 1px solid rgba(128, 128, 128, 0.22); border-radius: 14px; padding: 12px;
  display: flex; flex-direction: column; gap: 8px; }
.card h2 { margin: 0; font-size: 13px; color: #8e8e93; font-weight: 600; }
.row { margin: 0; display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.k { color: #8e8e93; flex-shrink: 0; }
.v { word-break: break-all; text-align: right; }
.mono { font-family: ui-monospace, monospace; }
.entry { border: none; border-radius: 10px; padding: 11px 12px; font-size: 14px; text-align: left;
  background: rgba(47, 111, 237, 0.1); color: inherit; }
.entry:disabled { opacity: 0.45; }
.note { margin: 0; font-size: 12px; color: #8e8e93; line-height: 1.5; }
</style>
