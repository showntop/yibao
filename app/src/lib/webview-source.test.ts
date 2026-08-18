import { describe, expect, it } from "vitest";
import { resolveWebviewSource } from "./webview-source";

describe("resolveWebviewSource", () => {
  it("url 优先:module 面板走 iframe src,key 含版本号", () => {
    expect(resolveWebviewSource({ url: "yibao-plugin://coding/panel/dist/index.html", v: 1720000000 }))
      .toEqual({ kind: "url", url: "yibao-plugin://coding/panel/dist/index.html", key: "yibao-plugin://coding/panel/dist/index.html@1720000000" });
  });
  it("url 与 html 同现时 url 胜(新通道优先)", () => {
    const s = resolveWebviewSource({ url: "yibao-plugin://a/panel/dist/index.html", html: "<html/>" });
    expect(s?.kind).toBe("url");
  });
  it("v 缺省 key 落 @0", () => {
    const s = resolveWebviewSource({ url: "yibao-plugin://a/x.html" });
    expect(s?.kind === "url" && s.key.endsWith("@0")).toBe(true);
  });
  it("仅 html:旧 srcdoc 面板原样", () => {
    expect(resolveWebviewSource({ html: "<html>old</html>" }))
      .toEqual({ kind: "srcdoc", html: "<html>old</html>" });
  });
  it("空值与空串都归 null", () => {
    expect(resolveWebviewSource(null)).toBeNull();
    expect(resolveWebviewSource(undefined)).toBeNull();
    expect(resolveWebviewSource({})).toBeNull();
    expect(resolveWebviewSource({ url: "", html: "" })).toBeNull();
  });
});
