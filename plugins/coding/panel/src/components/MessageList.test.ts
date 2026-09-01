// @vitest-environment happy-dom
// MessageList 交接摘要折叠(2026-09 走查修复):【交接上下文】开头的用户气泡渲染为
// 摘要行 + 点开全文,不再全文刷墙;普通气泡与 ⏪ 锚行为不变。
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import MessageList from "./MessageList.vue";
import type { RenderItem } from "../stores/session";

const briefText = "【交接上下文】\n" + "一段很长的交接摘要。".repeat(50) + "\n\n【用户继续】\nhi";

function mountList(items: RenderItem[]) {
  return mount(MessageList, {
    props: { items, streaming: false, rewindPending: new Set<string>() },
  });
}

describe("交接摘要折叠", () => {
  it("brief 气泡渲染为折叠摘要行,全文收在 details 内不直出", () => {
    const w = mountList([{ type: "user", text: briefText, uuid: "u1" }]);
    expect(w.find(".user-brief").exists()).toBe(true);
    expect(w.find(".user-brief summary").text()).toContain("交接摘要");
    expect(w.find(".user-brief summary").text()).toContain(String(briefText.length) + " 字");
    // 全文不在 summary 直出(折叠),在 body 内
    expect(w.find(".user-brief summary").text()).not.toContain("一段很长的交接摘要");
    expect(w.find(".user-brief-body").text()).toContain("【用户继续】");
    // ⏪ 锚照挂
    expect(w.find(".user-brief-wrap .rewind-btn").exists()).toBe(true);
  });

  it("普通用户气泡不受影响;无 uuid 不挂 ⏪", () => {
    const w = mountList([{ type: "user", text: "普通一句话" }]);
    expect(w.find(".user-brief").exists()).toBe(false);
    expect(w.find(".bubble").text()).toBe("普通一句话");
    expect(w.find(".rewind-btn").exists()).toBe(false);
  });
});
