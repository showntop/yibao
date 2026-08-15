import { describe, expect, it, vi } from "vitest";
import { buildEventsUrl, useEventStream, type EventSourceLike } from "./events";

function fakeES() {
  const listeners = new Map<string, (e: { data: string }) => void>();
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
    emit: (k: string, data: unknown) => listeners.get(k)?.({ data: JSON.stringify(data) }),
  };
}

describe("buildEventsUrl", () => {
  it("token 走 query 参数", () => {
    expect(buildEventsUrl({ host: "http://127.0.0.1:19527", token: "a b&c" }))
      .toBe("http://127.0.0.1:19527/v1/events?token=a%20b%26c");
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
    expect(chunk).toHaveBeenCalledWith({ kind: "final_reply_chunk", text: "你好", surface: "mobile" });
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
});
