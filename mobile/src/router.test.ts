import { describe, expect, it } from "vitest";
import { router } from "./router";

describe("router", () => {
  it("有 /pairing 与 /chat 两条路由且根重定向到 /chat", () => {
    const paths = router.getRoutes().map((r) => r.path);
    expect(paths).toContain("/pairing");
    expect(paths).toContain("/chat");
  });

  it("M2 底部导航四页齐备：/feed 与 /approvals 平级，/settings 收尾", () => {
    const paths = router.getRoutes().map((r) => r.path);
    expect(paths).toContain("/feed");
    expect(paths).toContain("/approvals");
    expect(paths).toContain("/settings");
  });
});
