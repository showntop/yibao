// @vitest-environment happy-dom
// 能力边界卡 / 停止分离 / 过程行收尾（拒绝·禁止·打断后不停转）。
import { describe, expect, it, vi } from "vitest";
import { ref } from "vue";
import { useChatFlow } from "./useChatFlow";
import { groupThread } from "../lib/work-thread";
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

describe("useChatFlow 过程行收尾", () => {
  it("用户拒绝（error 带 action）→ 对应过程行原地失败收尾，告警行留在同一 run", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "action_proposed", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" } } as BrainEvent);
    expect(flow.bubbles.value[0].proc?.done).toBe(false);

    flow.onEvent({ kind: "error", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" }, text: "用户拒绝执行 agents.code_exec" } as BrainEvent);

    const procBubble = flow.bubbles.value[0];
    expect(procBubble.proc?.done).toBe(true);
    expect(procBubble.proc?.result).toEqual({ success: false, error: "已拒绝" });
    expect(flow.procIdx.size).toBe(0);

    // 告警行照常出现，且与过程行同属一轮工作（不劈开头像组）
    expect(flow.bubbles.value[1]).toMatchObject({ role: "ai", icon: "alert", text: "用户拒绝执行 agents.code_exec" });
    expect(groupThread(flow.bubbles.value, () => false)).toEqual([{ type: "run", start: 0, indices: [0, 1] }]);
  });

  it("策略禁止（error 带 action）→ 过程行缀「已拦截」", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "action_proposed", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" } } as BrainEvent);
    flow.onEvent({ kind: "error", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" }, text: "策略禁止执行 agents.code_exec（风险过高）" } as BrainEvent);
    expect(flow.bubbles.value[0].proc?.result).toEqual({ success: false, error: "已拦截" });
  });

  it("打断 → 全部在途过程行缀「已打断」收尾，进度条停转", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "action_proposed", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" } } as BrainEvent);
    flow.onEvent({ kind: "action_proposed", action: { id: "a2", label: "找文件", tool_id: "agents.find_file" } } as BrainEvent);
    flow.onEvent({ kind: "interrupted" } as BrainEvent);

    const procs = flow.bubbles.value.filter((b) => b.proc);
    expect(procs).toHaveLength(2);
    for (const b of procs) {
      expect(b.proc?.done).toBe(true);
      expect(b.proc?.result).toEqual({ success: false, error: "已打断" });
    }
    expect(flow.procIdx.size).toBe(0);
    expect(flow.bubbles.value[flow.bubbles.value.length - 1]).toMatchObject({ role: "ai", text: "已打断", halted: true });
  });

  it("无 action 的普通 error（大脑出错）→ 不动在途过程行", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "action_proposed", action: { id: "a1", label: "运行沙箱脚本", tool_id: "agents.code_exec" } } as BrainEvent);
    flow.onEvent({ kind: "error", text: "大脑出错：网络中断" } as BrainEvent);
    expect(flow.bubbles.value[0].proc?.done).toBe(false);
    expect(flow.procIdx.size).toBe(1);
  });
});

describe("useChatFlow 提醒降噪（N8）", () => {
  it("同一提醒文本连续触发：并进上一条带 ×N，不刷屏", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "reminder", text: "坐久了，起来活动一下吧 🧘" } as BrainEvent);
    flow.onEvent({ kind: "reminder", text: "坐久了，起来活动一下吧 🧘" } as BrainEvent);
    flow.onEvent({ kind: "reminder", text: "坐久了，起来活动一下吧 🧘" } as BrainEvent);
    const reminders = flow.bubbles.value.filter((b) => b.icon === "clock");
    expect(reminders).toHaveLength(1);
    expect(reminders[0].text).toBe("坐久了，起来活动一下吧 🧘 ×3");
  });

  it("不同提醒文本各落各的，不合并", () => {
    const flow = makeFlow();
    flow.onEvent({ kind: "reminder", text: "坐久了，起来活动一下吧 🧘" } as BrainEvent);
    flow.onEvent({ kind: "reminder", text: "到点跑内容流水线啦 📋" } as BrainEvent);
    expect(flow.bubbles.value.filter((b) => b.icon === "clock")).toHaveLength(2);
  });
});
