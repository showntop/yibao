// @vitest-environment happy-dom
// 停止分离（P0）：final_reply 之后按停止只停 TTS 播报——sidecar 补发 speech_stopped，
// 前端只停播报 UI（回 idle），不得把已完成的对话 run 标「已打断」。
import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { useChatFlow } from "./useChatFlow";
import type { BrainEvent } from "../lib/brain";

// 单例 store 在模块加载即建 IndexedDB 引擎（happy-dom 无 indexedDB，未处理拒绝污染结果）；
// 本测试 getSessionId 恒空、持久化路径不触发，mock 成空壳即可（唯一不可避免的 mock）。
vi.mock("../state/store", () => ({ sessionStore: { conversation: {} } }));

function makeFlow() {
  return useChatFlow({
    getSessionId: () => "", // 空会话 id：跳过持久化，纯内存断言事件→气泡/状态映射
    sessionRefUpdate: () => {},
    emitReminder: () => {},
    flashValence: () => {},
    panelOpen: ref(false),
    setDraft: () => {},
  });
}

describe("useChatFlow 能力边界卡", () => {
  const proposed = { kind: "action_proposed", action: { id: "a1", tool_id: "project.create", label: "立项" } } as BrainEvent;

  it("project.create 结果带 enforced 能力缺口 → 推 gap 气泡（含标题与缺段）", () => {
    const flow = makeFlow();
    flow.onEvent(proposed);
    flow.onEvent({
      kind: "action_result",
      action: { id: "a1", tool_id: "project.create" },
      result: {
        success: true,
        data: {
          capability: {
            ready: false,
            enforced: true,
            available_stages: ["选题", "脚本"],
            missing_stages: ["分镜", "配音"],
            degradation: "可做到脚本；分镜起缺能力，安装对应 provider 后可继续",
          },
        },
      },
    } as BrainEvent);

    const gapBubble = flow.bubbles.value.find((b) => b.gap);
    expect(gapBubble).toBeTruthy();
    expect(gapBubble?.text).toBe("能力边界 · 可做到脚本");
    expect(gapBubble?.gap?.missing).toEqual(["分镜", "配音"]);
    expect(gapBubble?.gap?.note).toContain("可做到脚本");
    // 过程行照常收尾，不被能力卡顶掉
    expect(flow.bubbles.value.find((b) => b.proc)?.proc?.done).toBe(true);
  });

  it("info 策略（enforced=false）不出卡", () => {
    const flow = makeFlow();
    flow.onEvent(proposed);
    flow.onEvent({
      kind: "action_result",
      action: { id: "a1", tool_id: "project.create" },
      result: {
        success: true,
        data: {
          capability: { ready: false, enforced: false, available_stages: [], missing_stages: ["推进"] },
        },
      },
    } as BrainEvent);
    expect(flow.bubbles.value.some((b) => b.gap)).toBe(false);
  });
});

describe("useChatFlow 停止分离", () => {
  it("speech_stopped：final_reply 已落气泡后按停——回 idle，气泡完整不标「已打断」", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "final_reply", text: "完整答复" } as BrainEvent);
    flow.onEvent({ kind: "speaking" } as BrainEvent);
    expect(flow.state.value).toBe("say");

    flow.onEvent({ kind: "speech_stopped" } as BrainEvent);

    expect(flow.state.value).toBe("idle"); // 停止按钮不停在「说话中」
    expect(flow.bubbles.value).toHaveLength(1);
    expect(flow.bubbles.value[0].text).toBe("完整答复");
    expect(flow.bubbles.value[0].halted).toBeFalsy(); // run 已完成，不是打断
  });

  it("interrupted（对照）：执行中打断仍标「已打断」", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "interrupted" } as BrainEvent);
    expect(flow.state.value).toBe("idle");
    expect(flow.bubbles.value.some((b) => b.halted)).toBe(true);
  });
});
