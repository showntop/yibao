import { describe, expect, it, vi } from "vitest";
import { buildEventsUrl, useEventStream, type EventSourceLike } from "./events";

function fakeES() {
  // lastEventId 模拟 SSE 帧的 id: 行（服务端每帧必带，EventSource 转成 e.lastEventId）
  const listeners = new Map<string, (e: { data: string; lastEventId: string }) => void>();
  const es: EventSourceLike = {
    addEventListener: (k, cb) => listeners.set(k, cb),
    close: vi.fn(),
    onopen: null,
    onerror: null,
  };
  return {
    es,
    open: () => es.onopen?.(),
    err: () => es.onerror?.(),
    emit: (k: string, data: unknown, lastEventId = "") =>
      listeners.get(k)?.({ data: JSON.stringify(data), lastEventId }),
  };
}

describe("buildEventsUrl", () => {
  it("token 走 query 参数", () => {
    expect(buildEventsUrl({ host: "http://127.0.0.1:19527", token: "a b&c" }))
      .toBe("http://127.0.0.1:19527/v1/events?token=a%20b%26c");
  });

  it("lastEventId>0 拼 last_event_id 断点；0/缺省不拼", () => {
    const c = { host: "http://127.0.0.1:19527", token: "t" } as const;
    // 手动重连新建的 EventSource 带不上 Last-Event-ID header，query 是唯一续传通道
    expect(buildEventsUrl(c, 7)).toBe("http://127.0.0.1:19527/v1/events?token=t&last_event_id=7");
    expect(buildEventsUrl(c)).not.toContain("last_event_id");
    expect(buildEventsUrl(c, 0)).not.toContain("last_event_id");
  });
});

describe("useEventStream", () => {
  it("start→open 状态迁移；帧 JSON 解析后分发；stop 关闭", async () => {
    const f = fakeES();
    const stream = useEventStream(() => "http://x/v1/events?token=t", () => f.es);
    const chunk = vi.fn();
    const off = stream.on("final_reply_chunk", chunk);
    expect(stream.state.value).toBe("idle");
    stream.start();
    expect(stream.state.value).toBe("connecting");
    f.open();
    expect(stream.state.value).toBe("open");
    f.emit("final_reply_chunk", { kind: "final_reply_chunk", text: "你好", surface: "mobile" });
    // 无 id 行的帧 seq=0（无序号帧照常分发，去重交由 chat 层对 seq>0 才生效）
    expect(chunk).toHaveBeenCalledWith({ kind: "final_reply_chunk", text: "你好", surface: "mobile" }, 0);
    off();
    f.emit("final_reply_chunk", { text: "不再收" });
    expect(chunk).toHaveBeenCalledTimes(1);
    stream.stop();
    expect(f.es.close).toHaveBeenCalled();
  });

  it("onerror → error 状态（EventSource 自带重连，状态只反映当前）", () => {
    const f = fakeES();
    const stream = useEventStream(() => "u", () => f.es);
    stream.start();
    f.open();
    f.err();
    expect(stream.state.value).toBe("error");
  });

  it("帧 seq 透传：on 回调收 (data, seq)，lastSeq 只增不减", () => {
    const f = fakeES();
    const stream = useEventStream(() => "u", () => f.es);
    stream.start();
    const chunk = vi.fn();
    stream.on("final_reply_chunk", chunk);
    f.emit("final_reply_chunk", { text: "一" }, "5");
    expect(chunk).toHaveBeenCalledWith({ text: "一" }, 5);
    expect(stream.lastSeq.value).toBe(5);
    f.emit("final_reply_chunk", { text: "二" }, "9");
    expect(stream.lastSeq.value).toBe(9);
    f.emit("final_reply_chunk", { text: "三" }, "3"); // 乱序旧帧：lastSeq 不回退（重连 URL 断点不倒退）
    expect(stream.lastSeq.value).toBe(9);
  });
});
