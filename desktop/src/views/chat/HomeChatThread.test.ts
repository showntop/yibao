// @vitest-environment happy-dom
// 回归（截图走查）：一轮工作里「过程行 + 拒绝告警 + 后续正文」渲染为一个头像组；
// 告警行是 run 内轻状态行（plain + icon-alert），不是独立白卡；已收尾过程行不再有进度条/转圈。
import { mount } from "@vue/test-utils";
import { computed, ref } from "vue";
import { describe, expect, it, vi } from "vitest";
import HomeChatThread from "./HomeChatThread.vue";
import { HOME_CHAT_SESSION, type BubbleMsg, type HomeChatSession } from "../../lib/home/home-chat-session";
import { groupThread } from "../../lib/work-thread";
import { procDetail, procResultSuffix } from "../../lib/proc";

// 截图场景（修复后的终态）：拒绝的过程行已收尾，告警行在 run 内，打断的过程行已收尾
const bubbles = ref<BubbleMsg[]>([
  { role: "user", text: "给自己做个插件" },
  { role: "ai", text: "好，先看看插件目录结构和现有插件的写法。" },
  { role: "sys", text: "", proc: { label: "运行沙箱脚本", done: true, expanded: false, result: { success: true } } },
  { role: "ai", text: "这些目录只是运行时数据，真正的插件定义应该在别处。" },
  { role: "sys", text: "", proc: { label: "运行沙箱脚本", done: true, expanded: false, result: { success: false, error: "已拒绝" } } },
  { role: "ai", text: "用户拒绝执行 agents.code_exec", icon: "alert" },
  { role: "ai", text: "换个方式，直接搜插件清单文件在哪。" },
  { role: "sys", text: "", proc: { label: "找文件", done: true, expanded: false, result: { success: true } } },
  { role: "sys", text: "", proc: { label: "运行沙箱脚本", done: true, expanded: false, result: { success: false, error: "已打断" } } },
  { role: "ai", text: "已打断", halted: true },
]);

const session: HomeChatSession = {
  bubbles,
  thread: computed(() => groupThread(bubbles.value, () => false)),
  state: ref("idle"),
  sessionTitle: ref("新对话"),
  processes: computed(() => []),
  greeting: computed(() => "你好"),
  suggestChips: [],
  showTyping: computed(() => false),
  streamingIdx: ref(null),
  thinkNote: ref(""),
  showJump: ref(false),
  bubblesRef: ref(null),
  pages: computed(() => []),
  pageIndex: ref(0),
  page: computed(() => null),
  paperEmpty: computed(() => true),
  paperDuty: computed(() => false),
  paperTitle: computed(() => ""),
  paperLabel: computed(() => ""),
  stampLabels: computed(() => []),
  peekOpen: ref(false),
  livePathLine: computed(() => null),
  threadKey: (item) => (item.type === "run" ? `run-${item.start}` : `${item.type}-${item.index}`),
  submit: vi.fn(),
  fmtDay: () => "",
  openPanel: vi.fn(),
  procOk: (p) => p.result?.success !== false,
  procErrSuffix: (p) => procResultSuffix(p.result),
  procText: (p) => procDetail(p.action, p.result),
  paperShowProc: () => true,
  runRefsOf: () => undefined,
  toggleRunRefs: vi.fn(),
  runShowFooter: () => false,
  runMetricsOf: () => undefined,
  runHalted: (indices) => indices.some((i) => bubbles.value[i].halted),
  copyRun: vi.fn(),
  copyText: vi.fn(),
  onFeedback: vi.fn(),
  regenerate: vi.fn(),
  onEditMessage: vi.fn(),
  onBubblesScroll: vi.fn(),
  scrollBubbles: vi.fn(),
  flipPage: vi.fn(),
  noticeFor: () => undefined,
};

describe("HomeChatThread 拒绝/打断渲染", () => {
  it("一轮工作一个头像组：告警行收进 run，已收尾过程行不转圈", () => {
    const w = mount(HomeChatThread, {
      global: {
        provide: { [HOME_CHAT_SESSION as symbol]: session },
        stubs: {
          Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
          YbIcon: { template: "<i />" },
          UsageBar: { template: "<div />" },
        },
      },
    });

    // 一整轮工作（含拒绝告警 + 打断标记）只有一个 run → 一个头像
    expect(w.findAll(".work-run")).toHaveLength(1);
    expect(w.findAll(".work-run-ava")).toHaveLength(1);

    // 告警行：run 内的轻状态行（plain + icon-alert），不是独立白卡
    const alertLine = w.find(".work-run-body .bubble.plain.icon-alert");
    expect(alertLine.exists()).toBe(true);
    expect(alertLine.text()).toContain("用户拒绝执行 agents.code_exec");

    // 已收尾过程行：没有进度条（proc-track）——进度条只挂在未完成行上
    expect(w.find(".proc-track").exists()).toBe(false);
    const rows = w.findAll(".work-proc-row");
    expect(rows).toHaveLength(4);
    expect(w.findAll(".work-proc-row.done")).toHaveLength(2);
    expect(w.findAll(".work-proc-row.fail")).toHaveLength(2);

    // 顺序连贯：拒绝缀文 → 告警行 → 后续正文，都在同一 run 体内
    const html = w.find(".work-run-body").html();
    const atRejected = html.indexOf("已拒绝");
    const atAlert = html.indexOf("用户拒绝执行 agents.code_exec");
    const atNext = html.indexOf("换个方式，直接搜插件清单文件在哪。");
    const atHalted = html.indexOf("已打断");
    expect(atRejected).toBeGreaterThan(-1);
    expect(atRejected).toBeLessThan(atAlert);
    expect(atAlert).toBeLessThan(atNext);
    expect(atNext).toBeLessThan(atHalted);
  });
});
