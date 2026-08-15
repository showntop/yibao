import { describe, expect, it } from "vitest";
import { buildPairUrl } from "./pair";

describe("buildPairUrl", () => {
  it("拼出带预填参数的配对 URL", () => {
    expect(buildPairUrl("192.168.31.52", 19527, "a b&c"))
      .toBe("http://192.168.31.52:5173/?host=http%3A%2F%2F192.168.31.52%3A19527&token=a%20b%26c#/pairing");
  });
  it("无内网 IP（离线/仅公网）返回空串（UI 隐藏二维码）", () => {
    expect(buildPairUrl("", 19527, "t")).toBe("");
  });
});
