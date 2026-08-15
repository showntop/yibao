import { describe, expect, it } from "vitest";
import { router } from "./router";

describe("router", () => {
  it("有 /pairing 与 /chat 两条路由且根重定向到 /chat", () => {
    const paths = router.getRoutes().map((r) => r.path);
    expect(paths).toContain("/pairing");
    expect(paths).toContain("/chat");
  });
});
