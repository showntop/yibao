import { describe, expect, it } from "vitest";
import { router } from "./router";

describe("router", () => {
  it("根路径 / 重定向到 /chat（hash 路由的默认落点）", () => {
    const root = router.getRoutes().find((r) => r.path === "/");
    expect(root?.redirect).toBe("/chat");
  });

  it("配对页与对话页在册：/pairing 与 /chat", () => {
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

  it("记忆库子页 /memories 在册（Settings 入口的跳转目标，非 Tab 项）", () => {
    expect(router.getRoutes().map((r) => r.path)).toContain("/memories");
  });
});
