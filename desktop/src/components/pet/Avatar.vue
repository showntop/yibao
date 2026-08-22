<script setup lang="ts">
import { ref, watch, computed, onUnmounted } from "vue";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";

/* 译宝 · 天青鹅蛋角色：立体光影 + 小手 + 天线（兼状态灯）。
 *
 * 【组件定位：宠物形象】
 * 这是译宝的「宠物/角色」本体（用于宠物窗 + 左栏身份头部），与「顶栏产品
 * logo（阴阳鱼 src-tauri/icons/icon.png）」明确分离：品牌=产品，角色=宠物。
 *
 * 扩展性：
 * - `character` prop 预留多角色（默认 "tuanzi" = 天青鹅蛋）。后续要支持猫狗等
 *   宠物形象时，只需在内部按 character 切换 body/face 渲染即可，不必改 API。
 * - 当前实现只渲染天青鹅蛋；character 字段接入但不消费。
 *
 * 十态：idle/listen/think/work/say + 短暂 valence（success/error）；
 * 环境态：notify（有事找你：招手+「!」徽标）/ drowsy（发呆：垂眼+Zz）
 * / stretch（久坐做操：弯眼笑+双臂上举，一次性 squash-stretch，由 flashState 触发）。
 * size：常态球 64 / 聊天头部 44 / 列表与侧边栏 24–36 / 身份头像 48。
 * 保留 click/longpress/drag 手势状态机。
 *
 * ── 几何：为什么坐标看起来「不是整数」 ─────────────────────────
 * 旧实现用 .av { transform: scaleY(0.78) } 把整个元素压扁，再给脸部套
 * matrix(1,0,0,1.282,0,-16.9) 反向拉回。双重变换有两个副作用：
 *   1. 描边被各向异性缩放 → 横竖粗细不一致（2.2px 横 vs 1.7px 竖）
 *   2. 天线灯与 think 虚线环被压成椭圆
 *
 * 现在把压扁「烘进」路径坐标，中心锚定映射：
 *     y_new = 60 + (y_old - 60) × 0.78  ≡  0.78·y_old + 13.2
 * 因 scaleY 以元素中心（user space y=60）为原点，烘焙后渲染结果与原先
 * 逐像素一致 —— 宽 48 单位不变、高 65.5 单位不变，**宽高比精确保持**。
 * 半径类属性（ry / 相对 dy）同步乘 0.78，但**圆形保持为真圆**，这正是
 * 修掉天线被压扁的地方。
 *
 * 脸部：matrix 与 0.78 复合后净变换为 y → y + 0.018，即恒等。所以移除
 * matrix 后十套表情坐标**一个都不用改**。
 * ──────────────────────────────────────────────────────────── */

const props = withDefaults(
  defineProps<{
    state: "idle" | "listen" | "think" | "work" | "say" | "success" | "error" | "notify" | "drowsy" | "stretch";
    size?: number;
    /** 小尺寸模式：去光晕、放大状态灯并加白描边。默认由 size < 40 自动判定 */
    compact?: boolean;
    /** 感知观察中叠加点：右上角青白小点，独立于 state 编码通道 */
    observing?: boolean;
    /**
     * 宠物形象：默认 "tuanzi"（天青鹅蛋）。后续支持多角色时（如 cat/dog），
     * 在此字段上加分支切换 body/face 渲染即可，调用方 API 不变。
     * 当前为预留字段，未消费（无视觉差异）。
     */
    character?: string;
  }>(),
  { size: 64, character: "tuanzi" },
);
const emit = defineEmits<{
  (e: "click"): void;
  (e: "longpress"): void;
  /** 拖动开始/结束：拖动期间窗口必须整窗可交互（setInteractiveFull(true)），
   *  否则贴顶区团子位置实时变化、热区同步有 IPC 窗口期，Rust 会判定光标
   *  「不在热区」而切穿透 → WebKit 丢失 pointer 事件 → 拖动中断。 */
  (e: "drag-start"): void;
  (e: "drag-end"): void;
}>();

/* 小尺寸下满幅光晕会糊成一团蓝雾（侧边栏 36px 实测），且状态信息全压在
 * 3.4px 的灯点上不可辨。compact 同时解决两者。 */
const compact = computed(() => props.compact ?? props.size < 40);

// 状态灯半径：compact 下放大并加白描边，任意底色上都能拓出轮廓
const dotR = computed(() => (compact.value ? 4.6 : 3.4));

const STATE_LABEL: Record<string, string> = {
  idle: "待机",
  listen: "聆听中",
  think: "思考中",
  work: "执行中",
  say: "说话中",
  success: "已完成",
  error: "出错了",
  notify: "有事找你",
  drowsy: "发呆中",
  stretch: "伸展中",
};
const label = computed(() => `译宝 · ${STATE_LABEL[props.state] ?? props.state}`);

// 拖动 vs 点击 vs 长按：pointerdown 记坐标并起 450ms 计时；
// 移动 >4px 进入手动拖动（取消计时）；到点未动未抬 = 长按（voice）；提前抬起且未拖 = click。
//
// 手动拖动（不用系统 startDragging）：系统拖动会把窗口顶部 clamp 到 macOS 菜单栏下缘，
// 团子窗口内 y:82 → 拖到最上时团子顶部 ≈ 107px，够不到屏幕顶。
// 这里用 setPosition 逐帧跟随鼠标（屏幕绝对坐标，无漂移），窗口顶部可超出屏幕上方，
// 团子即可贴到屏幕最上方；配合 RAF 节流保持流畅。
const THRESHOLD = 4;
const LONGPRESS_MS = 450;
let down: { x: number; y: number } | null = null;
let downScreen: { x: number; y: number } | null = null;
let dragging = false;
let longFired = false;
let timer: ReturnType<typeof setTimeout> | null = null;
const holding = ref(false);

let winPos = { x: 0, y: 0 };                 // 拖动起点窗口位置（物理像素）
let scale = 1;
let rafId: ReturnType<typeof requestAnimationFrame> | null = null;
let lastMove: PointerEvent | null = null;

function cancelTimer() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  holding.value = false;
}
async function recordDragStart() {
  try {
    const win = getCurrentWindow();
    const [pos, sf] = await Promise.all([win.outerPosition(), win.scaleFactor()]);
    winPos = { x: pos.x, y: pos.y };
    scale = sf;
  } catch {
    // 非 Tauri 环境忽略
  }
}
function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  down = { x: e.clientX, y: e.clientY };
  downScreen = { x: e.screenX, y: e.screenY };
  dragging = false;
  longFired = false;
  holding.value = true;
  (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  void recordDragStart();
  timer = setTimeout(() => {
    if (down && !dragging) {
      longFired = true;
      emit("longpress");
    }
    cancelTimer();
  }, LONGPRESS_MS);
}
function onPointerMove(e: PointerEvent) {
  if (!down) return;
  if (!dragging) {
    if (Math.hypot(e.clientX - down.x, e.clientY - down.y) <= THRESHOLD) return;
    dragging = true;
    cancelTimer();
    emit("drag-start");
  }
  // 手动拖动：RAF 节流，取最新事件；screenX/Y 是屏幕绝对坐标，窗口移动不漂移
  lastMove = e;
  if (rafId === null) {
    rafId = requestAnimationFrame(applyFreeDrag);
  }
}
function applyFreeDrag() {
  rafId = null;
  const e = lastMove;
  lastMove = null;
  if (!e || !down || !downScreen || !dragging) return;
  const dx = (e.screenX - downScreen.x) * scale;
  const dy = (e.screenY - downScreen.y) * scale;
  void getCurrentWindow()
    .setPosition(new PhysicalPosition(Math.round(winPos.x + dx), Math.round(winPos.y + dy)))
    .catch(() => {});
}
function onPointerUp() {
  const wasDragging = dragging;
  cancelTimer();
  if (down && !dragging && !longFired) emit("click");
  down = null;
  downScreen = null;
  dragging = false;
  longFired = false;
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
  lastMove = null;
  if (wasDragging) emit("drag-end");
}

const INK = "var(--yb-body-ink)";
const BLUSH = "var(--yb-body-blush)";

// idle 随机眨眼：JS 随机间隔触发（2.2–5.8s），比固定周期自然；仅 idle 时跑
const blinking = ref(false);
let blinkTimer: ReturnType<typeof setTimeout> | null = null;
function scheduleBlink() {
  blinkTimer = setTimeout(() => {
    blinking.value = true;
    setTimeout(() => { blinking.value = false; }, 130);
    scheduleBlink();
  }, 2200 + Math.random() * 3600);
}
// idle 偶发小动作：照抄 scheduleBlink 模式——JS 随机稀疏触发（8–20s），
// 身体轻摇 / 天线轻晃随机二选一，单次播放不循环；仅 idle 时跑
const quirk = ref<"" | "sway" | "wiggle">("");
let quirkTimer: ReturnType<typeof setTimeout> | null = null;
let quirkOffTimer: ReturnType<typeof setTimeout> | null = null;
function clearQuirk() {
  if (quirkTimer) { clearTimeout(quirkTimer); quirkTimer = null; }
  if (quirkOffTimer) { clearTimeout(quirkOffTimer); quirkOffTimer = null; }
  quirk.value = "";
}
function scheduleIdleQuirk() {
  quirkTimer = setTimeout(() => {
    quirk.value = Math.random() < 0.5 ? "sway" : "wiggle";
    // 动画播完（sway 0.7s / wiggle 0.8s，留 900ms 余量）摘 class 再排下一次，互不重叠
    quirkOffTimer = setTimeout(() => {
      quirk.value = "";
      scheduleIdleQuirk();
    }, 900);
  }, 8000 + Math.random() * 12000);
}
watch(
  () => props.state,
  (s) => {
    if (s === "idle") { scheduleBlink(); scheduleIdleQuirk(); }
    else {
      if (blinkTimer) { clearTimeout(blinkTimer); blinkTimer = null; blinking.value = false; }
      clearQuirk();
    }
  },
  { immediate: true },
);
onUnmounted(() => { if (blinkTimer) clearTimeout(blinkTimer); clearQuirk(); });
</script>

<template>
  <div
    class="av"
    :class="[state, quirk, { holding, compact }]"
    :style="{ width: props.size + 'px', height: props.size + 'px' }"
    role="img"
    :aria-label="label"
    @pointerdown.prevent="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <svg viewBox="0 0 120 120" class="yb" aria-hidden="true">
      <defs>
        <linearGradient id="yb-body" x1="34%" y1="6%" x2="66%" y2="100%">
          <stop offset="0%" stop-color="var(--yb-body-hi)" />
          <stop offset="46%" stop-color="var(--yb-body-mid)" />
          <stop offset="100%" stop-color="var(--yb-body-lo)" />
        </linearGradient>
        <radialGradient id="yb-hi" cx="36%" cy="22%" r="32%">
          <stop offset="0%" stop-color="#ffffff" stop-opacity="0.95" />
          <stop offset="100%" stop-color="#ffffff" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-sh" cx="74%" cy="80%" r="46%">
          <stop offset="0%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0.42" />
          <stop offset="100%" stop-color="var(--yb-body-core-shadow)" stop-opacity="0" />
        </radialGradient>
        <radialGradient id="yb-dot-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--dot)" stop-opacity="0.55" />
          <stop offset="100%" stop-color="var(--dot)" stop-opacity="0" />
        </radialGradient>
        <!-- 接触光：贴身低透明，替代原先 r=58 的满幅光晕 -->
        <radialGradient id="yb-aura" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="var(--yb-accent)" stop-opacity="0.42" />
          <stop offset="55%" stop-color="var(--yb-accent)" stop-opacity="0.22" />
          <stop offset="100%" stop-color="var(--yb-accent)" stop-opacity="0" />
        </radialGradient>
        <clipPath id="yb-clip">
          <path d="M60 27.24 C76 27.24 84 39.72 84 57.66 C84 78.72 74 92.76 60 92.76 C46 92.76 36 78.72 36 57.66 C36 39.72 44 27.24 60 27.24 Z" />
        </clipPath>
        <filter id="yb-b1"><feGaussianBlur stdDeviation="1.4" /></filter>
        <filter id="yb-b2"><feGaussianBlur stdDeviation="3.2" /></filter>
        <filter id="yb-b3"><feGaussianBlur stdDeviation="5.5" /></filter>
      </defs>

      <!-- 落地投影 -->
      <ellipse cx="63" cy="100.56" rx="33" ry="5.07" fill="var(--yb-shadow-ink)" opacity="0.16" filter="url(#yb-b3)" />

      <!-- 接触光：只在非 compact 渲染；idle 缓慢呼吸（给存在感） -->
      <ellipse v-if="!compact" class="aura" cx="60" cy="64" rx="30" ry="24" fill="url(#yb-aura)" />

      <!-- 身体（呼吸在这层） -->
      <g class="body-grp">
        <path d="M60 27.24 C76 27.24 84 39.72 84 57.66 C84 78.72 74 92.76 60 92.76 C46 92.76 36 78.72 36 57.66 C36 39.72 44 27.24 60 27.24 Z" fill="url(#yb-body)" />
        <g clip-path="url(#yb-clip)">
          <rect x="28" y="21" width="86" height="78" fill="url(#yb-sh)" />
          <ellipse cx="44" cy="39.72" rx="28" ry="18.72" fill="url(#yb-hi)" />
          <ellipse cx="60" cy="95.88" rx="28" ry="7.8" fill="var(--yb-body-contact)" opacity="0.26" filter="url(#yb-b2)" />
        </g>
        <!-- 左侧边缘反光（描边改为各向同性 2.0） -->
        <path d="M40 38.16 C37.6 49.86 37.8 70.14 44 86.52" fill="none" stroke="#ffffff" stroke-opacity="0.9" stroke-width="2" filter="url(#yb-b1)" />
        <!-- 小手（左手包组：notify 态的招手动画挂点） -->
        <g class="hand-l">
          <ellipse cx="33" cy="63.12" rx="7.5" ry="8.97" fill="url(#yb-body)" transform="rotate(-14 33 63.12)" />
          <ellipse cx="34" cy="60" rx="3" ry="3.9" fill="#ffffff" opacity="0.5" transform="rotate(-14 34 60)" />
        </g>
        <ellipse cx="87" cy="63.12" rx="7.5" ry="8.97" fill="url(#yb-body)" transform="rotate(14 87 63.12)" />
        <ellipse cx="88" cy="60" rx="3" ry="3.9" fill="var(--yb-body-core-shadow)" opacity="0.32" transform="rotate(14 88 60)" />
        <!-- 领巾（天青） -->
        <path d="M46 45.96 Q60 52.2 74 45.96" fill="none" stroke="var(--yb-accent)" stroke-width="2.9" stroke-linecap="round" />
        <path d="M46 45.18 Q60 51.42 74 45.18" fill="none" stroke="#ffffff" stroke-opacity="0.5" stroke-width="0.9" stroke-linecap="round" />

        <!-- 脸：坐标即最终坐标（原反向 matrix 净效果为恒等，已移除） -->
        <g class="face">
        <!-- 腮红（轻） -->
        <ellipse cx="47" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />
        <ellipse cx="73" cy="66" rx="4.2" ry="2.5" :fill="BLUSH" opacity="0.24" />

        <!-- 眼睛 / 嘴：按状态 -->
        <!-- idle -->
        <g v-if="state === 'idle'">
          <g class="eyes-look">
            <g class="eyes" :class="{ blinking: blinking }">
              <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
              <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
              <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
              <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
            </g>
          </g>
          <path d="M55 69 Q60 72 65 69" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- listen -->
        <g v-else-if="state === 'listen'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="2.8" ry="3.4" :fill="INK" />
        </g>
        <!-- think -->
        <g v-else-if="state === 'think'">
          <ellipse cx="51" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="58.4" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="56.8" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="56.8" r="1.05" fill="#fff" />
          <path d="M56 70 Q60 68.6 64 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- work -->
        <g v-else-if="state === 'work'">
          <line x1="46.5" y1="54.5" x2="54" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <line x1="73.5" y1="54.5" x2="66" y2="56" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <ellipse cx="51" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <ellipse cx="69" cy="60.5" rx="3.2" ry="3.8" :fill="INK" />
          <path d="M55 70 L65 70" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- say -->
        <g v-else-if="state === 'say'">
          <ellipse cx="51" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.2" ry="4.5" :fill="INK" />
          <circle cx="52.2" cy="58.4" r="1.05" fill="#fff" />
          <circle cx="70.2" cy="58.4" r="1.05" fill="#fff" />
          <ellipse cx="60" cy="70" rx="4" ry="4.6" :fill="INK" />
          <ellipse cx="60" cy="72.2" rx="2.2" ry="1.5" fill="#f0b8b8" opacity="0.85" />
        </g>
        <!-- success -->
        <g v-else-if="state === 'success'">
          <path d="M47 60.5 Q51 57.5 55 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 60.5 Q69 57.5 73 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M52 67 Q60 75 68 67" fill="none" :stroke="INK" stroke-width="2.6" stroke-linecap="round" />
        </g>
        <!-- stretch（做操：弯眼笑 + 张嘴 + 双臂上举） -->
        <g v-else-if="state === 'stretch'">
          <path d="M47 60.5 Q51 57.5 55 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 60.5 Q69 57.5 73 60.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <ellipse cx="60" cy="70.5" rx="3.4" ry="4" :fill="INK" />
          <path class="arm-l" d="M40 54 Q33 44 30 35" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path class="arm-r" d="M80 54 Q87 44 90 35" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- notify（有事找你：期待脸——睁大眼+眉毛上扬+笑开一点） -->
        <g v-else-if="state === 'notify'">
          <path d="M45 53 l6.4 -2.6" fill="none" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <path d="M75 53 l-6.4 -2.6" fill="none" :stroke="INK" stroke-width="1.8" stroke-linecap="round" />
          <ellipse cx="51" cy="60" rx="3.4" ry="4.9" :fill="INK" />
          <ellipse cx="69" cy="60" rx="3.4" ry="4.9" :fill="INK" />
          <circle cx="52.3" cy="58.2" r="1.1" fill="#fff" />
          <circle cx="70.3" cy="58.2" r="1.1" fill="#fff" />
          <path d="M54.5 68.5 Q60 73.5 65.5 68.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        <!-- drowsy（发呆：眼皮半垂——眼压扁+眼睑线，嘴放松） -->
        <g v-else-if="state === 'drowsy'">
          <path d="M47.2 57.6 Q51 55.8 54.8 57.6" fill="none" :stroke="INK" stroke-width="1.6" stroke-linecap="round" />
          <path d="M65.2 57.6 Q69 55.8 72.8 57.6" fill="none" :stroke="INK" stroke-width="1.6" stroke-linecap="round" />
          <ellipse cx="51" cy="61" rx="3.2" ry="2.1" :fill="INK" />
          <ellipse cx="69" cy="61" rx="3.2" ry="2.1" :fill="INK" />
          <path d="M56.5 69.5 Q60 71 63.5 69.5" fill="none" :stroke="INK" stroke-width="2.2" stroke-linecap="round" />
        </g>
        <!-- error -->
        <g v-else>
          <path d="M47 61.5 Q51 64 55 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M65 61.5 Q69 64 73 61.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
          <path d="M55 71.5 Q60 69.2 65 71.5" fill="none" :stroke="INK" stroke-width="2.4" stroke-linecap="round" />
        </g>
        </g>
      </g>

      <!-- 小短腿（破上下对称：底部两只小脚，一前一后略错开） -->
      <g class="feet">
        <ellipse cx="47" cy="94.32" rx="7.5" ry="3.9" fill="url(#yb-body)" transform="rotate(-9 47 94.32)" />
        <ellipse cx="73" cy="95.88" rx="7.5" ry="3.9" fill="url(#yb-body)" transform="rotate(11 73 95.88)" />
        <path d="M41 95.88 Q47 98.22 53 95.88" fill="none" stroke="var(--yb-body-core-shadow)" stroke-opacity="0.4" stroke-width="1.35" stroke-linecap="round" />
        <path d="M67 97.44 Q73 99.78 79 97.44" fill="none" stroke="var(--yb-body-core-shadow)" stroke-opacity="0.4" stroke-width="1.35" stroke-linecap="round" />
      </g>

      <!-- 天线：灯与环恢复真圆（原先被 scaleY 压成椭圆） -->
      <line x1="60" y1="28.8" x2="60" y2="21.78" stroke="var(--yb-body-stem)" stroke-width="2" stroke-linecap="round" />
      <circle
        v-if="state === 'think'"
        class="ring"
        cx="60"
        cy="19.44"
        r="6.5"
        fill="none"
        stroke="var(--dot)"
        :stroke-width="compact ? 2.4 : 1.6"
        :stroke-dasharray="compact ? '4 3' : '3 3'"
      />
      <g class="dot-grp">
        <circle v-if="!compact" cx="60" cy="19.44" r="6" fill="url(#yb-dot-glow)" />
        <!-- compact 下加白描边：任意底色（白文档/深色 IDE/照片壁纸）上都拓出轮廓 -->
        <circle
          cx="60"
          cy="19.44"
          :r="dotR"
          fill="var(--dot)"
          :stroke="compact ? '#ffffff' : 'none'"
          :stroke-width="compact ? 1.4 : 0"
        />
      </g>
      <!-- 感知观察中叠加点：独立 prop，不占用 state 状态编码通道 -->
      <circle v-if="observing" cx="86" cy="20" r="3" fill="#cfe8f5" opacity="0.9" />

      <!-- success 星芒（真对称星形） -->
      <path
        v-if="state === 'success'"
        class="spark"
        d="M86 32.16 l1.6 4.4 l4.4 1.6 l-4.4 1.6 l-1.6 4.4 l-1.6 -4.4 l-4.4 -1.6 l4.4 -1.6 Z"
        fill="var(--yb-body-spark)"
      />
      <!-- error 汗滴 -->
      <path v-if="state === 'error'" d="M80 39.72 q2.6 3.4 0 5 q-2.6 -1.6 0 -5" fill="var(--yb-body-sweat)" />

      <!-- notify「!」徽标：右上角小圆，入场 pop 一次 -->
      <g v-if="state === 'notify'" class="attn-badge">
        <circle cx="92" cy="31.92" r="8.5" fill="var(--dot)" />
        <circle cx="92" cy="31.92" r="8.5" fill="none" stroke="#ffffff" stroke-opacity="0.7" stroke-width="1.2" />
        <path d="M92 27.5 v5.2" stroke="#fff" stroke-width="2.4" stroke-linecap="round" />
        <circle cx="92" cy="35.3" r="1.4" fill="#fff" />
      </g>
      <!-- drowsy 飘 Zz -->
      <g v-if="state === 'drowsy'" class="zzz">
        <text x="82" y="44.4">z</text>
        <text x="89" y="38.16">z</text>
      </g>

      <!-- 声波弧（listen 左 / say 右） -->
      <g v-if="state === 'listen'" class="waves">
        <path class="wave" d="M28 58.44 q-4 5 0 10" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M23 54.54 q-7 9.5 0 19" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
      </g>
      <g v-else-if="state === 'say'" class="waves">
        <path class="wave" d="M92 58.44 q4 5 0 10" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
        <path class="wave" d="M97 54.54 q7 9.5 0 19" fill="none" stroke="var(--dot)" stroke-width="2.2" stroke-linecap="round" />
      </g>
    </svg>
  </div>
</template>

<style scoped>
/* 注意：这里不再有 transform: scaleY()，压扁已烘进路径坐标。
 * 布局盒仍是 size × size，渲染尺寸与旧实现一致。 */
.av {
  position: relative;
  width: 64px;
  height: 64px;
  cursor: grab;
  user-select: none;
  touch-action: none;
}
.av:active {
  cursor: grabbing;
}
.yb {
  width: 100%;
  height: 100%;
  display: block;
  overflow: visible;
  transition: transform var(--yb-dur-fast) var(--yb-ease-out);
}

/* ---- 状态灯色（天线 dot）：状态编码通道一 ---- */
.av.idle { --dot: var(--yb-state-idle); }
.av.listen { --dot: var(--yb-state-listen); }
.av.think { --dot: var(--yb-state-think); }
.av.work { --dot: var(--yb-state-work); }
.av.say { --dot: var(--yb-state-say); }
.av.success { --dot: var(--yb-state-success); }
.av.error { --dot: var(--yb-state-error); }
.av.notify { --dot: var(--yb-state-notify); }
.av.drowsy { --dot: var(--yb-state-drowsy); }
.av.stretch { --dot: var(--yb-state-stretch); }

/* ---- 动画基础 ---- */
.body-grp {
  transform-box: fill-box;
  transform-origin: 50% 92%;
  animation: breathe 3.4s infinite ease-in-out;
}
.dot-grp,
.ring,
.spark,
.wave,
.aura {
  transform-box: fill-box;
  transform-origin: center;
}

/* 按住反馈：整体微放大，提示继续按住 = 语音 */
/* yb-hover：鼠标穿透→可交互切换时 WebKit 不自动重算 :hover，App.vue 收到
 * Rust pet-cursor-enter 事件后补挂此 class（配合 :hover 双保险，视觉一致） */
.av:hover .yb,
.av.yb-hover .yb {
  transform: translateY(-3px);
}
.av.holding .yb {
  transform: scale(1.06);
  transition: transform var(--yb-dur-slow) var(--yb-ease-out);
}

@keyframes breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.02, 0.985); }
}

/* ---- 各态灯动效：状态编码通道二（节奏）----
 * idle 呼吸暗淡 / listen 快脉冲 / think 转环 / work 慢脉冲 / say 辉光
 * / success 星芒 / error 抖动 / notify 快脉冲+招手 / drowsy 全体减速 */
.av.idle .dot-grp { animation: dim 3s infinite ease-in-out; }
.av.idle .aura { animation: aura-breathe 4.8s infinite ease-in-out; }
.av.idle .eyes-look { transform-box: fill-box; transform-origin: center; animation: yb-look 13s infinite ease-in-out; }
.eyes { transform-box: fill-box; transform-origin: center; transition: transform 0.09s var(--yb-ease-out); }
.eyes.blinking { transform: scaleY(0.08); }
.av.listen .dot-grp { animation: pulse 1.2s infinite ease-in-out; }
.av.think .ring { animation: spin 2.4s linear infinite; }
.av.work .dot-grp { animation: pulse 1.7s infinite ease-in-out; }
.av.say .dot-grp { animation: glow 1s infinite alternate ease-in-out; }
.av.success .spark { animation: pop 1.2s ease-out infinite; }
.av.error .dot-grp { animation: shake 0.5s infinite ease-in-out; }

/* idle 偶发小动作（JS 随机触发，一次性动画；播完摘 class 回落 breathe）：
   sway 身体轻摇（底部为轴）/ wiggle 天线灯轻晃 */
.av.sway .body-grp { animation: yb-sway 0.7s var(--yb-ease-out) 1; }
.av.wiggle .dot-grp { animation: yb-wiggle 0.8s var(--yb-ease-out) 1; }
@keyframes yb-sway {
  0%, 100% { transform: rotate(0deg); }
  25% { transform: rotate(-2.5deg); }
  75% { transform: rotate(2.5deg); }
}
@keyframes yb-wiggle {
  0%, 100% { transform: rotate(0deg); }
  30% { transform: rotate(-14deg); }
  70% { transform: rotate(14deg); }
}

/* stretch：一次性做操（下蹲蓄力 → 向上伸展 → 回弹），由 flashState 触发，不循环 */
.av.stretch .body-grp { animation: yb-stretch 1.15s var(--yb-ease-spring) 1; }
.av.stretch .dot-grp { animation: pulse 0.9s ease-in-out 1; }
@keyframes yb-stretch {
  0%   { transform: scale(1, 1); }
  30%  { transform: scale(1.06, 0.86); }
  62%  { transform: scale(0.94, 1.12); }
  82%  { transform: scale(1.02, 0.97); }
  100% { transform: scale(1, 1); }
}

/* notify：灯快脉冲 + 左手招手 + 徽标入场 pop 一次 */
.av.notify .dot-grp { animation: pulse 1s infinite ease-in-out; }
.hand-l { transform-box: fill-box; transform-origin: 85% 90%; }
.av.notify .hand-l { animation: wave 1s infinite ease-in-out; }
.attn-badge {
  transform-box: fill-box;
  transform-origin: center;
  animation: badge-in var(--yb-dur-slow) var(--yb-ease-spring) both;
}

/* drowsy：全体减速——呼吸/光晕/灯都拉长，Zz 循环飘出 */
.av.drowsy .body-grp { animation-duration: 6.5s; }
.av.drowsy .aura { animation-duration: 9s; }
.av.drowsy .dot-grp { animation: dim 6s infinite ease-in-out; }
.zzz text {
  font: var(--yb-fw-bold) 9px var(--yb-font);
  fill: var(--yb-body-stem);
  opacity: 0;
}
.av.drowsy .zzz text { animation: zfloat 3.2s infinite ease-in-out; }
.av.drowsy .zzz text:nth-child(2) { animation-delay: 1.6s; font-size: 6.5px; }

/* compact：状态编码通道三——脉冲幅度加大，让 24px 下的节奏差异看得出来 */
.av.compact .dot-grp { animation-name: pulse-strong; }
.av.compact.idle .dot-grp,
.av.compact.drowsy .dot-grp { animation-name: dim; }
.av.compact.say .dot-grp { animation-name: glow; }

@keyframes wave {
  0%, 100% { transform: rotate(0deg); }
  50% { transform: rotate(-18deg); }
}
@keyframes badge-in {
  from { transform: scale(0); }
  to { transform: scale(1); }
}
@keyframes zfloat {
  0% { opacity: 0; transform: translate(0, 4px); }
  30% { opacity: 0.9; }
  100% { opacity: 0; transform: translate(6px, -10px); }
}

/* listen / say 声波渐次闪烁 */
.av.listen .wave,
.av.say .wave { animation: wv 1.1s infinite ease-in-out; }
.av.listen .wave:nth-child(2),
.av.say .wave:nth-child(2) { animation-delay: 0.2s; }

@keyframes dim { 0%, 100% { opacity: 0.5; } 50% { opacity: 0.85; } }
@keyframes aura-breathe {
  0%, 100% { transform: scale(0.92); opacity: 0.55; }
  50% { transform: scale(1.08); opacity: 1; }
}
@keyframes yb-look {
  0%, 16% { transform: translateX(0); }
  20%, 30% { transform: translateX(2.4px); }
  34%, 52% { transform: translateX(0); }
  56%, 66% { transform: translateX(-2.4px); }
  70%, 100% { transform: translateX(0); }
}
@keyframes pulse {
  0%, 100% { transform: scale(0.8); opacity: 0.6; }
  50% { transform: scale(1.2); opacity: 1; }
}
@keyframes pulse-strong {
  0%, 100% { transform: scale(0.72); opacity: 0.55; }
  50% { transform: scale(1.28); opacity: 1; }
}
@keyframes glow { from { opacity: 0.65; } to { opacity: 1; } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes wv { 0%, 100% { opacity: 0.25; } 50% { opacity: 0.9; } }
@keyframes pop {
  0% { transform: scale(0); opacity: 0; }
  55% { transform: scale(1.25); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-1.2px); }
  75% { transform: translateX(1.2px); }
}

/* 常驻桌面软件的底线：系统要求减少动效时所有循环动画必须停 */
@media (prefers-reduced-motion: reduce) {
  .body-grp, .dot-grp, .ring, .spark, .wave, .hand-l, .attn-badge, .zzz text, .aura, .eyes-look {
    animation: none !important;
  }
  .av.stretch .body-grp { animation: none; }  /* 一次性动画同样让位：只留静态脸 */
}
</style>
