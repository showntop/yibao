<script setup lang="ts">
// 设计走查页：逐项验证设计令牌的落地效果。开发时访问 /design.html 截图比对。
// 分区对应 ui-plan.md 第七节「走查画布」。
import Avatar from "./components/Avatar.vue";
import Bubble from "./components/Bubble.vue";
import InputBar from "./components/InputBar.vue";
import SchemaPanel from "./components/SchemaPanel.vue";
import YbIcon from "./components/YbIcon.vue";

const states = ["idle", "listen", "think", "work", "say", "success", "error", "notify", "drowsy"] as const;
const stateLabel: Record<string, string> = {
  idle: "待机", listen: "聆听", think: "思考", work: "干活", say: "说话",
  success: "成功", error: "出错", notify: "有事找你", drowsy: "发呆",
};
const avatarSizes = [64, 36, 24];

// 图标全集（与 YbIcon 的 IconName 同步）
const iconNames = [
  "clock", "chat", "gear", "spinner", "check", "x", "stop", "lock",
  "pin", "doc", "alert", "inbox", "sparkle", "plug", "dumpling", "mic",
] as const;

// 语义色板：分组展示，色块直接消费令牌（改令牌这里立刻变）
const paletteGroups: Array<{ title: string; items: Array<{ name: string; token: string; ink?: boolean }> }> = [
  {
    title: "表面 surface",
    items: [
      { name: "bg (L1)", token: "--yb-bg" },
      { name: "surface-1 (L2)", token: "--yb-surface-1" },
      { name: "surface-2", token: "--yb-surface-2" },
      { name: "surface-3", token: "--yb-surface-3" },
      { name: "glass (L3)", token: "--yb-glass" },
    ],
  },
  {
    title: "文字 text",
    items: [
      { name: "text", token: "--yb-text", ink: true },
      { name: "text-dim", token: "--yb-text-dim", ink: true },
      { name: "text-faint", token: "--yb-text-faint", ink: true },
      { name: "text-on-accent", token: "--yb-text-on-accent", ink: true },
    ],
  },
  {
    title: "主色 accent",
    items: [
      { name: "accent", token: "--yb-accent" },
      { name: "accent-deep", token: "--yb-accent-deep" },
      { name: "accent-soft", token: "--yb-accent-soft" },
    ],
  },
  {
    title: "意图 intent",
    items: [
      { name: "pending", token: "--yb-intent-pending" },
      { name: "pending-ink", token: "--yb-intent-pending-ink", ink: true },
      { name: "pending-soft", token: "--yb-intent-pending-soft" },
      { name: "ok", token: "--yb-intent-ok" },
      { name: "danger", token: "--yb-intent-danger" },
    ],
  },
  {
    title: "天线状态灯 state",
    items: [
      { name: "idle", token: "--yb-state-idle" },
      { name: "listen", token: "--yb-state-listen" },
      { name: "think", token: "--yb-state-think" },
      { name: "work", token: "--yb-state-work" },
      { name: "say", token: "--yb-state-say" },
      { name: "success", token: "--yb-state-success" },
      { name: "error", token: "--yb-state-error" },
      { name: "notify", token: "--yb-state-notify" },
      { name: "drowsy", token: "--yb-state-drowsy" },
    ],
  },
];

const fontScale = [
  { name: "xs · 11", token: "--yb-fs-xs", sample: "状态胶囊 Status pill 12:30" },
  { name: "sm · 11.5", token: "--yb-fs-sm", sample: "辅助说明 Secondary 12:30" },
  { name: "md · 12.5", token: "--yb-fs-md", sample: "正文内容 Body text 正文 12:30" },
  { name: "lg · 13.5", token: "--yb-fs-lg", sample: "常规对话 Regular chat 对话 12:30" },
  { name: "xl · 15", token: "--yb-fs-xl", sample: "区块标题 Heading 标题 12:30" },
];

type DemoBubble = {
  role: "user" | "ai" | "sys";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
  streaming?: boolean;
};
const chat: DemoBubble[] = [
  { role: "user", text: "帮我把这个点子记下来：给播客做一期「AI 桌宠的一天」" },
  { role: "sys", text: "已附带选中文字 328 字", icon: "doc" },
  { role: "sys", text: "记下来源页面", pstate: "ok" },
  { role: "sys", text: "同步到笔记库", pstate: "run" },
  { role: "sys", text: "生成封面图 · 网络超时", pstate: "fail" },
  {
    role: "ai",
    text: "### 选题已记录\n| 项目 | 内容 |\n|------|------|\n| **标题** | AI 桌宠的一天 |\n| **状态** | 候选 |\n\n想补充一下吗：\n- **切入角度** — 从哪个点写？\n- **目标平台** — 发在哪？",
  },
  { role: "ai", text: "周五 14:00 产品评审", icon: "clock" },
  { role: "ai", text: "大脑掉线（ECONNREFUSED），正在自动重启…", icon: "alert" },
  { role: "ai", text: "这段话说到一半被打断了", halted: true },
  { role: "ai", text: "好嘞，它在闪念里等你。随时喊我。", streaming: true },
  // 代码块 + 行内 code 走查样例
  { role: "ai", text: '顺手给了个标题生成的小脚本：\n```python\ndef title(topic: str) -> str:\n    return f"AI 桌宠的一天 · {topic}"\n```\n跑 `python title.py` 就能用。' },
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
    <h1 class="page-title">译宝设计走查 · 天青</h1>

    <!-- 1. 形象九态 × 三尺寸：验证「小尺寸可辨」与光晕收紧 -->
    <section class="block">
      <h2>形象 · 九态 × 三尺寸（64 / 36 / 24）</h2>
      <div class="avatar-grid">
        <div v-for="s in states" :key="s" class="avatar-col">
          <div v-for="sz in avatarSizes" :key="sz" class="avatar-cell">
            <Avatar :state="s" :size="sz" />
            <span class="avatar-label">{{ sz }}</span>
          </div>
          <span class="avatar-name">{{ stateLabel[s] }}</span>
        </div>
      </div>
    </section>

    <!-- 2. 语义色板：改令牌这里立刻变 -->
    <section class="block">
      <h2>色彩令牌 · 语义层</h2>
      <div class="palette">
        <div v-for="g in paletteGroups" :key="g.title" class="pal-group">
          <h3>{{ g.title }}</h3>
          <div class="pal-row">
            <div v-for="it in g.items" :key="it.name" class="pal-cell">
              <span class="pal-chip" :style="{ background: `var(${it.token})` }" />
              <span class="pal-name" :style="it.ink ? { color: `var(${it.token})` } : undefined">{{ it.name }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. 字阶与行高：五级封顶 + 中英混排 + 数字等宽 -->
    <section class="block">
      <h2>字阶与行高</h2>
      <div class="type-sample">
        <div v-for="f in fontScale" :key="f.name" class="type-row">
          <span class="type-name yb-num">{{ f.name }}</span>
          <span :style="{ fontSize: `var(${f.token})` }">{{ f.sample }}</span>
        </div>
        <div class="type-row">
          <span class="type-name">mono</span>
          <span class="yb-num">0123456789 12:30:45 · 计数 1,024 不跳动</span>
        </div>
      </div>
    </section>

    <!-- 4. 图标全集 × 三档尺寸 -->
    <section class="block">
      <h2>图标 · 全集（16 / 20 / 24）</h2>
      <div class="icon-grid">
        <div v-for="n in iconNames" :key="n" class="icon-cell">
          <span class="icon-trio">
            <YbIcon :name="n" :size="16" />
            <YbIcon :name="n" :size="20" />
            <YbIcon :name="n" :size="24" />
          </span>
          <span class="icon-name">{{ n }}</span>
        </div>
      </div>
    </section>

    <!-- 5. 按钮四态矩阵 + 开关 / 复选 -->
    <section class="block">
      <h2>按钮四态 · 开关 · 复选</h2>
      <div class="btn-matrix">
        <span class="bm-head" />
        <span class="bm-head">常态</span>
        <span class="bm-head">hover</span>
        <span class="bm-head">active</span>
        <span class="bm-head">disabled</span>
        <span class="bm-row">主按钮</span>
        <button class="btn btn-primary">批准</button>
        <button class="btn btn-primary is-hover">批准</button>
        <button class="btn btn-primary is-active">批准</button>
        <button class="btn btn-primary" disabled>批准</button>
        <span class="bm-row">中性</span>
        <button class="btn btn-neutral">忽略</button>
        <button class="btn btn-neutral is-hover">忽略</button>
        <button class="btn btn-neutral is-active">忽略</button>
        <button class="btn btn-neutral" disabled>忽略</button>
        <span class="bm-row">危险</span>
        <button class="btn btn-danger">拒绝</button>
        <button class="btn btn-danger is-hover">拒绝</button>
        <button class="btn btn-danger is-active">拒绝</button>
        <button class="btn btn-danger" disabled>拒绝</button>
      </div>
      <div class="ctl-row">
        <label class="switch"><input type="checkbox" checked /><i />开启态</label>
        <label class="switch"><input type="checkbox" /><i />关闭态</label>
        <label class="chk"><input type="checkbox" checked />记住选择</label>
        <label class="chk"><input type="checkbox" />未选</label>
      </div>
    </section>

    <!-- 6+7. 大窗主屏语言：统一时间线（日期分段头 + 类型图标 + hover 操作）+ 右副列 -->
    <section class="block">
      <h2>大窗主屏 · 时间线 + 右副列</h2>
      <div class="feed-mock">
        <!-- 左主列：时间线 -->
        <div class="tlm-col">
          <div class="tlm-head">
            <div class="segmented">
              <button class="seg on">全部</button>
              <button class="seg">未读 <span class="seg-n yb-num">2</span></button>
              <button class="seg">跟进</button>
              <button class="seg">已忽略</button>
            </div>
            <button class="link-btn">全部标为已读</button>
          </div>
          <div class="tlm-date">今天</div>
          <div class="tlm-row unread">
            <span class="tlm-ic ic-clock"><YbIcon name="clock" :size="13" /></span>
            <span class="tlm-main"><span class="tlm-text">周五 14:00 产品评审要带 demo 清单</span></span>
            <span class="tlm-time yb-num">14:32</span>
          </div>
          <div class="tlm-row unread">
            <span class="tlm-ic ic-check"><YbIcon name="check" :size="13" /></span>
            <span class="tlm-main">
              <span class="tlm-text">整理上周设计稿截图，已归档 24 张</span>
              <span class="tlm-tag">完成</span>
            </span>
            <span class="tlm-time yb-num">11:05</span>
          </div>
          <div class="tlm-row st-follow">
            <span class="tlm-ic"><YbIcon name="chat" :size="13" /></span>
            <span class="tlm-main"><span class="tlm-text">小红书评论区有人问「这个桌宠哪买的」</span></span>
            <span class="tlm-time yb-num">09:48</span>
            <span class="tlm-acts"><button class="tlm-act on"><YbIcon name="pin" :size="12" /></button><button class="tlm-act"><YbIcon name="x" :size="12" /></button></span>
          </div>
          <div class="tlm-row">
            <span class="tlm-ic ic-x"><YbIcon name="x" :size="13" /></span>
            <span class="tlm-main">
              <span class="tlm-text">生成播客封面图 · 网络超时</span>
              <span class="tlm-tag tag-failed">失败</span>
            </span>
            <span class="tlm-time yb-num">09:12</span>
          </div>
          <div class="tlm-date">昨天</div>
          <div class="tlm-row st-ignore">
            <span class="tlm-ic"><YbIcon name="chat" :size="13" /></span>
            <span class="tlm-main"><span class="tlm-text">每周一的磁盘空间检查（已忽略）</span></span>
            <span class="tlm-time yb-num">18:42</span>
          </div>
        </div>
        <!-- 右副列：待批准 + 进行中 -->
        <div class="sidem-col">
          <section class="panel panel-pending">
            <div class="panel-head">
              <span class="panel-title"><YbIcon name="lock" :size="12" />待批准 <span class="count yb-num">2</span></span>
            </div>
            <div class="panel-body">
              <div class="ap-card selected">
                <div class="ap-top">
                  <label class="ap-check"><input type="checkbox" checked /></label>
                  <div class="ap-info"><strong>读取日历</strong><span>接下来 7 天日程</span></div>
                </div>
                <div class="ap-btns"><button class="btn btn-ghost">拒绝</button><button class="btn btn-primary">批准</button></div>
              </div>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head">
              <span class="panel-title"><YbIcon name="spinner" :size="12" spin />进行中 <span class="count yb-num">1</span></span>
              <button class="link-btn">查看</button>
            </div>
            <div class="panel-body">
              <div class="run-row">
                <span class="run-dot" />
                <span class="run-main"><strong>整理上周截图</strong><span>已运行 2 分钟</span></span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </section>

    <!-- 8. 对话流：用户 / AI / 过程行 / 提醒 / 告警 / 打断 / Markdown 表格 -->
    <section class="block">
      <h2>对话流</h2>
      <div class="chat-mock">
        <div class="chat-header-mock">
          <Avatar state="say" :size="40" />
          <div class="chat-meta">
            <strong>译宝</strong>
            <span class="chat-state">正在说话…</span>
          </div>
        </div>
        <div class="bubbles">
          <Bubble
            v-for="(m, i) in chat"
            :key="i"
            :role="m.role"
            :text="m.text"
            :pstate="m.pstate"
            :halted="m.halted"
            :icon="m.icon"
            :streaming="m.streaming"
          />
          <Bubble role="ai" text="" typing />
        </div>
        <InputBar />
      </div>
    </section>

    <!-- 9. 空态 + Dock + 层级 L1–L3 对照 -->
    <section class="block">
      <h2>空态 · Dock · 层级对照</h2>
      <div class="row-wrap">
        <div class="empty-mock">
          <span class="empty-ic"><YbIcon name="dumpling" :size="26" :stroke="1.5" /></span>
          <strong>这里还空空的</strong>
          <span class="empty-hint">去跟译宝说一句试试</span>
        </div>
        <div class="dock-mock">
          <div class="dock-cell">
            <span class="dock-ic">磨</span><span class="dock-name">需求磨刀</span>
          </div>
          <div class="dock-cell">
            <span class="dock-ic">捕</span><span class="dock-name">选题捕捉</span>
            <span class="dock-pin on"><YbIcon name="pin" :size="10" /></span>
          </div>
          <div class="dock-cell">
            <span class="dock-ic">日历</span><span class="dock-name">日程</span>
          </div>
        </div>
        <div class="layer-mock">
          <div class="l1">L1 页面底
            <div class="l2">L2 内容卡（实色 + shadow-1）
              <div class="l3">L3 浮层（glass + blur + shadow-3）</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 10. 看板面板（既有走查，保留） -->
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
  font-family: var(--yb-font);
  font-size: var(--yb-fs-md);
}
.page-title {
  margin: 0 0 20px;
  font-size: 22px; /* 展示级页标题，UI 字阶之外的单点例外 */
  font-weight: var(--yb-fw-bold);
}
.block {
  margin-bottom: 36px;
}
.block h2 {
  margin: 0 0 12px;
  font-size: var(--yb-fs-xl);
  color: var(--yb-text-dim);
  font-weight: var(--yb-fw-bold);
}
.block h3 {
  margin: 0 0 6px;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  font-weight: var(--yb-fw-medium);
}

/* ---- 1. 形象网格 ---- */
.avatar-grid {
  display: flex;
  gap: 28px;
  flex-wrap: wrap;
}
.avatar-col {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-border-base);
}
.avatar-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-height: 72px;
  justify-content: flex-end;
}
.avatar-label {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}
.avatar-name {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
  font-weight: var(--yb-fw-medium);
}

/* ---- 2. 色板 ---- */
.palette {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-4);
}
.pal-row {
  display: flex;
  gap: var(--yb-space-3);
  flex-wrap: wrap;
}
.pal-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.pal-chip {
  width: 56px;
  height: 36px;
  border-radius: var(--yb-radius-sm);
  border: 1px solid var(--yb-border-base);
}
.pal-name {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  font-family: var(--yb-mono);
}

/* ---- 3. 字阶 ---- */
.type-sample {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-border-base);
  max-width: 560px;
}
.type-row {
  display: flex;
  align-items: baseline;
  gap: var(--yb-space-4);
}
.type-name {
  width: 72px;
  flex-shrink: 0;
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
}

/* ---- 4. 图标 ---- */
.icon-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--yb-space-2);
}
.icon-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: var(--yb-space-2) var(--yb-space-3);
  border-radius: var(--yb-radius-sm);
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-border-base);
  color: var(--yb-text);
}
.icon-trio {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
}
.icon-name {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-dim);
  font-family: var(--yb-mono);
}

/* ---- 5. 按钮矩阵（预览页自定义演示样式，映射四态规范） ---- */
.btn-matrix {
  display: grid;
  grid-template-columns: 64px repeat(4, auto);
  gap: var(--yb-space-2);
  align-items: center;
  max-width: 520px;
}
.bm-head {
  font-size: var(--yb-fs-xs);
  color: var(--yb-text-faint);
  text-align: center;
}
.bm-row {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.btn {
  padding: 5px 14px;
  border-radius: var(--yb-radius-sm);
  border: none;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
  font-family: inherit;
  cursor: pointer;
  transition: all var(--yb-dur-fast) var(--yb-ease-out);
}
.btn-primary {
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.btn-primary.is-hover {
  background: var(--yb-accent-deep);
}
.btn-primary.is-active {
  transform: scale(0.97);
  background: var(--yb-accent-deep);
}
.btn-neutral {
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
}
.btn-neutral.is-hover {
  background: var(--yb-btn-neutral-hover);
  color: var(--yb-text);
}
.btn-neutral.is-active {
  transform: scale(0.97);
}
.btn-danger {
  background: transparent;
  border: 1px solid var(--yb-border-strong);
  color: var(--yb-text-dim);
}
.btn-danger.is-hover {
  color: var(--yb-danger);
  border-color: var(--yb-danger);
}
.btn-danger.is-active {
  transform: scale(0.97);
  color: var(--yb-danger);
  border-color: var(--yb-danger);
}
.ctl-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-5);
  margin-top: var(--yb-space-4);
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.switch {
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-2);
  cursor: pointer;
}
.switch input {
  position: absolute;
  opacity: 0;
}
.switch i {
  width: 32px;
  height: 18px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral-hover);
  position: relative;
  transition: background var(--yb-dur-fast) var(--yb-ease-out);
}
.switch i::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: var(--yb-shadow-1);
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}
.switch input:checked + i {
  background: var(--yb-accent);
}
.switch input:checked + i::after {
  transform: translateX(14px);
}
.chk {
  display: inline-flex;
  align-items: center;
  gap: var(--yb-space-2);
  cursor: pointer;
}
.chk input {
  accent-color: var(--yb-accent);
}

/* ---- 6+7. 大窗主屏 mock：时间线 + 右副列 ---- */
.feed-mock {
  display: flex;
  gap: var(--yb-space-5);
  padding: var(--yb-space-4);
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-md);
  background: var(--yb-content-bg);
}
.tlm-col {
  flex: 1;
  min-width: 0;
}
.sidem-col {
  width: 300px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-3);
}
.tlm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-3);
  padding-bottom: var(--yb-space-3);
}
/* 分段控件（与主屏同款） */
.segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--yb-radius-xs);
  background: var(--yb-segment-track);
}
.seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px var(--yb-space-3);
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
}
.seg.on {
  background: var(--yb-segment-thumb);
  color: var(--yb-text);
  font-weight: var(--yb-fw-medium);
  box-shadow: var(--yb-shadow-1);
}
.seg-n {
  font-size: var(--yb-fs-xs);
  color: var(--yb-accent-deep);
}
.link-btn {
  border: none;
  background: transparent;
  color: var(--yb-accent-deep);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
}
/* sticky 日期分段头 */
.tlm-date {
  padding: var(--yb-space-2) var(--yb-space-1);
  font-size: var(--yb-fs-xs);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
  letter-spacing: 0.04em;
}
/* 时间线行：Finder 列表语义（hairline 分隔，无卡片描边） */
.tlm-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--yb-space-3);
  padding: var(--yb-space-3) var(--yb-space-2);
  border-bottom: 1px solid var(--yb-card-row-line);
  font-size: var(--yb-fs-lg);
}
/* 未读：左侧 accent 竖条（macOS 邮件语义） */
.tlm-row.unread::before {
  content: "";
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-accent);
}
.tlm-row.unread .tlm-text {
  font-weight: var(--yb-fw-medium);
}
.tlm-row.st-follow .tlm-ic {
  color: var(--yb-accent);
}
.tlm-row.st-ignore {
  opacity: 0.5;
}
.tlm-ic {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--yb-surface-3);
  color: var(--yb-text-faint);
}
.tlm-ic.ic-clock {
  color: var(--yb-accent);
}
.tlm-ic.ic-check {
  color: var(--yb-intent-ok);
}
.tlm-ic.ic-x {
  color: var(--yb-danger);
}
.tlm-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
}
.tlm-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tlm-tag {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-xs);
}
.tlm-tag.tag-failed {
  background: var(--yb-danger-soft);
  color: var(--yb-danger);
}
.tlm-time {
  flex-shrink: 0;
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-faint);
}
/* 行内操作：走查页常显（真实界面是 hover 才浮现） */
.tlm-acts {
  flex-shrink: 0;
  display: flex;
  gap: 2px;
}
.tlm-act {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border: none;
  border-radius: var(--yb-radius-xs);
  background: transparent;
  color: var(--yb-text-faint);
  cursor: pointer;
}
.tlm-act.on {
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
}
/* 右副列分组卡 */
.panel {
  border: 1px solid var(--yb-card-border);
  border-radius: var(--yb-card-radius);
  background: var(--yb-card-bg);
  overflow: hidden;
}
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2) var(--yb-space-3);
  border-bottom: 1px solid var(--yb-card-row-line);
  background: var(--yb-card-page-bg);
}
.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-text-dim);
}
.count {
  padding: 0 5px;
  border-radius: var(--yb-radius-pill);
  background: var(--yb-btn-neutral);
  font-size: var(--yb-fs-xs);
}
.panel-body {
  padding: var(--yb-space-2);
}
.panel-pending {
  border-color: var(--yb-intent-pending);
  box-shadow: var(--yb-shadow-1);
}
.panel-pending .panel-head {
  background: var(--yb-intent-pending-soft);
}
.panel-pending .panel-title {
  color: var(--yb-intent-pending-ink);
}
.ap-card {
  padding: var(--yb-space-2);
  border-radius: var(--yb-radius-xs);
}
.ap-card.selected {
  background: var(--yb-row-selected);
}
.ap-top {
  display: flex;
  align-items: flex-start;
  gap: var(--yb-space-2);
}
.ap-check input {
  margin: 2px 0 0;
  accent-color: var(--yb-accent);
}
.ap-info {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}
.ap-info strong {
  font-size: var(--yb-fs-lg);
  font-weight: var(--yb-fw-medium);
}
.ap-info span {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.ap-btns {
  display: flex;
  gap: var(--yb-space-2);
  margin-top: var(--yb-space-2);
}
.ap-btns .btn {
  flex: 1;
}
.btn-ghost {
  border: 1px solid var(--yb-border-strong);
  background: var(--yb-card-bg);
  color: var(--yb-text-dim);
}
.run-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding: var(--yb-space-2);
}
.run-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--yb-accent);
  box-shadow: 0 0 0 3px var(--yb-accent-soft);
}
.run-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.run-main strong {
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-medium);
}
.run-main span {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}

/* ---- 8. 对话流 ---- */
.chat-mock {
  width: 400px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-glass);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  box-shadow: var(--yb-shadow-3);
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
  line-height: var(--yb-lh-tight);
}
.chat-state {
  font-size: var(--yb-fs-md);
  color: var(--yb-text-dim);
}
.bubbles {
  display: flex;
  flex-direction: column;
  gap: var(--yb-space-2);
  padding: var(--yb-space-3) var(--yb-space-4);
  min-height: 120px;
}

/* ---- 9. 空态 / Dock / 层级 ---- */
.row-wrap {
  display: flex;
  gap: var(--yb-space-5);
  flex-wrap: wrap;
  align-items: stretch;
}
.empty-mock {
  width: 220px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: var(--yb-space-5) var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-2);
  border: 1px solid var(--yb-border-base);
  color: var(--yb-text-dim);
  font-size: var(--yb-fs-md);
}
.empty-ic {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--yb-accent-soft);
  color: var(--yb-accent-deep);
  display: grid;
  place-items: center;
  margin-bottom: 4px;
}
.empty-hint {
  font-size: var(--yb-fs-sm);
}
.dock-mock {
  display: flex;
  gap: var(--yb-space-3);
  padding: var(--yb-space-3);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-border-base);
  align-items: flex-start;
}
.dock-cell {
  position: relative;
  width: 64px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.dock-ic {
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: var(--yb-radius-md);
  background: linear-gradient(160deg, var(--yb-accent-soft), var(--yb-surface-1));
  border: 1px solid var(--yb-border-base);
  box-shadow: var(--yb-shadow-1);
  font-size: var(--yb-fs-md);
  font-weight: var(--yb-fw-bold);
  color: var(--yb-accent-deep);
}
.dock-name {
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.dock-pin {
  position: absolute;
  top: -4px;
  right: 6px;
  width: 18px;
  height: 18px;
  display: grid;
  place-items: center;
  border: 1px solid var(--yb-border-base);
  border-radius: 50%;
  background: var(--yb-surface-1);
  color: var(--yb-text-dim);
}
.dock-pin.on {
  background: var(--yb-accent);
  border-color: var(--yb-accent);
  color: var(--yb-text-on-accent);
}
.layer-mock {
  flex: 1;
  min-width: 240px;
}
.l1 {
  height: 100%;
  min-height: 150px;
  padding: var(--yb-space-3);
  background: var(--yb-bg);
  border: 1px dashed var(--yb-border-strong);
  border-radius: var(--yb-radius-md);
  font-size: var(--yb-fs-sm);
  color: var(--yb-text-dim);
}
.l2 {
  margin-top: var(--yb-space-2);
  padding: var(--yb-space-3);
  background: var(--yb-surface-1);
  border: 1px solid var(--yb-border-base);
  border-radius: var(--yb-radius-md);
  box-shadow: var(--yb-shadow-1);
  color: var(--yb-text);
}
.l3 {
  margin-top: var(--yb-space-2);
  padding: var(--yb-space-3);
  background: var(--yb-glass);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  border-radius: var(--yb-radius-md);
  box-shadow: var(--yb-shadow-3);
}

/* ---- 10. 看板面板 ---- */
.panel-mock {
  width: 860px;
  border-radius: var(--yb-radius-lg);
  background: var(--yb-glass);
  backdrop-filter: var(--yb-blur);
  border: 1px solid var(--yb-glass-border);
  box-shadow: var(--yb-shadow-3);
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
  color: var(--yb-text-dim);
  cursor: default;
}
.panel-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
