<script setup lang="ts">
import { onMounted, shallowRef } from "vue";
import { useRouter } from "vue-router";
import { loadConn } from "../api/connection";
import { useMemories } from "../state/memories";

const router = useRouter();
// shallowRef：onMounted 里赋值要触发重渲染（与 Chat/Feed 同模式）
const mem = shallowRef<ReturnType<typeof useMemories> | null>(null);

onMounted(async () => {
  const conn = await loadConn();
  if (!conn) return router.replace("/pairing");
  mem.value = useMemories(conn);
  void mem.value.refresh();
});

// created_at 为服务端拼好的字符串（mem0 原样，形如 2026-08-15 09:30:…）：
// 只做「T→空格 + 截到分」的友好裁剪，可能为空（旧数据无时间戳）
function fmtTime(s: string): string {
  return s ? s.replace("T", " ").slice(0, 16) : "";
}
</script>

<template>
  <div class="memories">
    <header class="head">
      <button class="ghost" @click="router.replace('/settings')">← 设置</button>
      <span class="title">记忆库</span>
      <button class="ghost" @click="mem?.refresh()">↻ 刷新</button>
    </header>
    <main class="list">
      <p v-if="!mem" class="empty">加载中…</p>
      <template v-else>
        <!-- 错误态与空态分开：专页里「没拉到」不能冒充「没记忆」 -->
        <p v-if="mem.error.value" class="err">{{ mem.error.value }}</p>
        <p v-else-if="mem.loading.value" class="empty">正在拉取…</p>
        <p v-else-if="mem.items.value.length === 0" class="empty">还没有记忆——让译宝记住点什么就有了</p>
        <div v-for="m in mem.items.value" :key="m.id" class="item">
          <div class="body">
            <p class="text">{{ m.text }}</p>
            <span class="meta">
              <span v-if="m.label" class="ns">{{ m.label }}</span>
              <span v-if="fmtTime(m.created_at)">{{ fmtTime(m.created_at) }}</span>
            </span>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.memories { display: flex; flex-direction: column; height: 100dvh;
  padding-bottom: calc(52px + env(safe-area-inset-bottom)); /* TabBar 让位 */ }
.head { display: flex; justify-content: space-between; align-items: center; padding: 0 12px; }
.title { font-size: 16px; font-weight: 600; }
.ghost { background: none; border: none; color: #2f6fed; font-size: 14px; }
.list { flex: 1; overflow-y: auto; padding: 8px 12px 12px; display: flex; flex-direction: column; gap: 8px; }
.empty { text-align: center; opacity: 0.55; padding: 40px 0; }
.err { color: #ff453a; font-size: 13px; text-align: center; padding: 24px 0; }
.item { padding: 10px 12px; border-radius: 12px; background: rgba(128, 128, 128, 0.07); }
.body { min-width: 0; }
/* 记忆文本截断：最多三行，长文折叠（点开全文是桌面记忆管理页的事，手机只读浏览） */
.text { margin: 0; font-size: 14px; line-height: 1.45; word-break: break-word;
  overflow: hidden; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
.meta { display: flex; gap: 8px; align-items: center; font-size: 11px; color: #8e8e93; }
/* 命名空间徽标：每条都带（底座「译宝」/插件名）——列表是跨空间平铺的，徽标是唯一出处标识 */
.ns { padding: 0 6px; border-radius: 6px; background: rgba(47, 111, 237, 0.12); color: #2f6fed; }
</style>
