// @vitest-environment happy-dom
// 运行中 steer(后端 spec §A):streaming 会话的发送直送 coding.send——后端入督导队列
// 返回 {queued, position} 并发 marker 进流;发送后输入框清空、queued 不视为错误。
// steer 失败(收尾缝「请稍候」)保留 prompt 可重试 + 状态行提示。
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn((m: string) => {
    if (m === "coding.start") return Promise.resolve({ session_id: "s1" });
    if (m === "coding.send") return Promise.resolve({ session_id: "s1", queued: true, position: 1 });
    return Promise.resolve({});
  }),
  onInit: vi.fn(),
  onHostMessage: vi.fn(),
  emitPanelEvent: vi.fn(),
}));

import { invoke } from "../lib/bridge";
import StationView from "./StationView.vue";

const mountStation = () =>
  mount(StationView, { props: { focused: true, autoplay: false, defaultCwd: "/tmp/x" } });

async function sendText(w: ReturnType<typeof mountStation>, text: string) {
  const ta = w.find("textarea#prompt");
  (ta.element as HTMLTextAreaElement).value = text;
  await w.find("#send").trigger("click");
  await flushPromises();
}

describe("StationView 运行中 steer", () => {
  it("running 时可发:coding.send 排队(queued 不报错),发送后输入框清空,后端 marker 进流可见", async () => {
    const w = mountStation();
    await flushPromises();
    await sendText(w, "首轮任务"); // 空闲直发:coding.start 起会话 → streaming
    expect((w.vm as any).state.streaming).toBe(true);
    expect((w.vm as any).state.currentSession).toBe("s1");

    await sendText(w, "督导补充一句"); // running 发送 → steer
    expect(invoke).toHaveBeenLastCalledWith("coding.send", {
      id: "s1", prompt: "督导补充一句", mode: "acceptEdits",
    });
    const ta = w.find("textarea#prompt").element as HTMLTextAreaElement;
    expect(ta.value).toBe(""); // 受理(入队)即清空
    expect((w.vm as any).state.error).toBeNull(); // queued 不当错误

    // 后端入队 marker 经 onData 进流 → 既有 marker 渲染路径可见
    (w.vm as any).onData({
      session_id: "s1",
      event: { kind: "marker", text: "督导补充已排队（第 1 条），本轮结束后自动接续" },
    });
    await flushPromises();
    expect(w.text()).toContain("督导补充已排队（第 1 条）");
    expect((w.vm as any).state.error).toBeNull();
  });

  it("steer 失败(收尾缝拒理):prompt 保留可重试 + 状态行提示,不进 errbar", async () => {
    const w = mountStation();
    await flushPromises();
    await sendText(w, "首轮任务");
    (invoke as ReturnType<typeof vi.fn>).mockImplementation((m: string) =>
      m === "coding.send"
        ? Promise.reject(new Error("会话正在收尾中，请稍候"))
        : Promise.resolve({ session_id: "s1" }));
    await sendText(w, "督导补充一句");
    const ta = w.find("textarea#prompt").element as HTMLTextAreaElement;
    expect(ta.value).toBe("督导补充一句"); // 失败保留 prompt
    expect(w.text()).toContain("排队失败");
    expect((w.vm as any).state.error).toBeNull(); // 状态行 tip,不进 errbar
    (invoke as ReturnType<typeof vi.fn>).mockImplementation((m: string) =>
      Promise.resolve(m === "coding.send" ? { session_id: "s1", queued: true, position: 1 } : { session_id: "s1" }));
  });
});
