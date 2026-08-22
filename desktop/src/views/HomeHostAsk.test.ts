// @vitest-environment happy-dom
import "fake-indexeddb/auto";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import HomeHostAsk from "./HomeHostAsk.vue";

describe("HomeHostAsk", () => {
  it("is talking to 译宝, not the plugin", async () => {
    const w = mount(HomeHostAsk, {
      global: {
        stubs: {
          InputBar: {
            template: `<button class="go" type="button" @click="$emit('submit', '它卡在哪')">发</button>`,
          },
        },
      },
    });
    expect(w.text()).toContain("跟译宝说");
    expect(w.text()).toContain("这块活仍在工位上");
    await w.get(".go").trigger("click");
    expect(w.emitted("submit")?.[0]).toEqual(["它卡在哪"]);
  });
});
