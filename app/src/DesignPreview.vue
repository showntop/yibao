<script setup lang="ts">
// 设计预览页：用真实组件 + mock 数据渲染宠物窗 / 聊天流 / 看板面板，供设计走查截图。
import Avatar from "./components/Avatar.vue";
import Bubble from "./components/Bubble.vue";
import InputBar from "./components/InputBar.vue";
import SchemaPanel from "./components/SchemaPanel.vue";

const states = ["idle", "listen", "think", "work", "say"] as const;
const stateLabel: Record<string, string> = {
  idle: "待机",
  listen: "聆听",
  think: "思考",
  work: "干活",
  say: "说话",
};

const chat: Array<{ role: "user" | "ai"; text: string }> = [
  { role: "user", text: "帮我把这个点子记下来：给播客做一期「AI 桌宠的一天」" },
  {
    role: "ai",
    text: '### ✅ 选题已记录！\n| 项目 | 内容 |\n|------|------|\n| 📌 **标题** | AI 桌宠的一天 |\n| 🗂 **状态** | 🟡 候选 |\n\n想补充一下吗：\n- 🎯 **切入角度** — 从哪个点写？\n- 📱 **目标平台** — 发在哪？\n\n或者直接说「写初稿」也行～',
  },
  { role: "user", text: "先不用，我自己再看看" },
  { role: "ai", text: "好嘞，它在闪念里等你。随时喊我。" },
];

const boardSchema = {
  version: 1,
  type: "board",
  bind: { items: "$data.rows", column: "$item.status" },
  columns: [
    { key: "灵感", label: "灵感" },
    { key: "快筛过", label: "快筛过" },
    { key: "挑战中", label: "挑战中" },
    { key: "已立项", label: "已立项" },
    { key: "已搁置", label: "已搁置" },
    { key: "已否决", label: "已否决" },
  ],
  card: {
    title: "$item.title",
    subtitle: "$item.pain",
    actions: [{ label: "详情", method: "forge.get", params: { id: "$item.id" } }],
  },
};

const boardData = {
  rows: [
    { id: "1", title: "AI 桌宠的一天", pain: "播客选题荒", status: "灵感" },
    { id: "2", title: "通勤语音笔记", pain: "路上想法留不住", status: "快筛过" },
    { id: "3", title: "选题温度计", pain: "不知道哪个能火", status: "挑战中" },
    { id: "4", title: "评论区挖掘机", pain: "用户需求藏太深", status: "已立项" },
    { id: "5", title: "封面图生成器", pain: "做图太费时间", status: "灵感" },
    { id: "6", title: "标题 A/B 台", pain: "打开率猜不透", status: "已搁置" },
    { id: "7", title: "弹幕复读机", pain: "缺乏差异点", status: "已否决" },
  ],
};
</script>

<template>
  <div class="design-root">
    <h1 class="page-title">译宝 UI 预览 · 治愈系</h1>

    <section class="block">
      <h2>团子 · 五状态</h2>
      <div class="avatar-row">
        <div v-for="s in states" :key="s" class="avatar-cell">
          <Avatar :state="s" :size="88" />
          <span class="avatar-label">{{ stateLabel[s] }}</span>
        </div>
      </div>
    </section>

    <section class="block">
      <h2>聊天窗</h2>
      <div class="chat-mock">
        <div class="chat-header-mock">
          <Avatar state="say" :size="40" />
          <div class="chat-meta">
            <strong>译宝</strong>
            <span class="chat-state">正在说话…</span>
          </div>
        </div>
        <div class="bubbles">
          <Bubble v-for="(m, i) in chat" :key="i" :role="m.role" :text="m.text" />
        </div>
        <InputBar />
      </div>
    </section>

    <section class="block">
      <h2>看板面板 · 需求磨刀</h2>
      <div class="panel-mock">
        <div class="panel-titlebar">
          <strong>需求磨刀 · 选题看板</strong>
          <span class="panel-close">✕</span>
        </div>
        <div class="panel-body">
          <SchemaPanel panel="forge:board" :schema="boardSchema" :data="boardData" />
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.design-root {
  min-height: 100vh;
  padding: 24px 28px 48px;
  background: var(--yb-bg);
  color: var(--yb-text);
  font-family: var(--yb-font, -apple-system, "PingFang SC", sans-serif);
}
.page-title {
  margin: 0 0 20px;
  font-size: 22px;
}
.block {
  margin-bottom: 36px;
}
.block h2 {
  margin: 0 0 12px;
  font-size: 15px;
  opacity: 0.65;
  font-weight: 600;
}
.avatar-row {
  display: flex;
  gap: 32px;
  align-items: flex-end;
}
.avatar-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.avatar-label {
  font-size: 12px;
  opacity: 0.6;
}
.chat-mock {
  width: 400px;
  border-radius: var(--yb-radius-lg, 14px);
  background: var(--yb-shell-bg, rgba(255, 255, 255, 0.7));
  box-shadow: 0 8px 32px rgba(20, 20, 40, 0.14);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.chat-header-mock {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
}
.chat-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.chat-state {
  font-size: 12px;
  opacity: 0.6;
}
.bubbles {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  min-height: 120px;
}
.panel-mock {
  width: 860px;
  border-radius: var(--yb-radius-lg, 14px);
  background: var(--yb-shell-bg, rgba(255, 255, 255, 0.7));
  box-shadow: 0 8px 32px rgba(20, 20, 40, 0.14);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  height: 420px;
}
.panel-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
}
.panel-close {
  opacity: 0.5;
  cursor: default;
}
.panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
