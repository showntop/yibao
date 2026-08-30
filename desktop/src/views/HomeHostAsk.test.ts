// @vitest-environment happy-dom
import "fake-indexeddb/auto";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import HomeHostAsk from "./HomeHostAsk.vue";
import { SELECTION_FRESH_MS, liveSelection } from "./../lib/surface/selection-store.ts";

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
    expect(w.emitted("submit")?.[0]).toEqual(["它卡在哪", null]);
  });

  it("points at a fresh instrument selection (边说边指), ignores a stale one", async () => {
    liveSelection.value = { panel: "zimeiti:editor", docId: "7", start: 3, end: 9, quote: "窑变微光", ts: Date.now() };
    const w = mount(HomeHostAsk, {
      global: {
        stubs: {
          InputBar: {
            template: `<button class="go" type="button" @click="$emit('submit', '改写这段')">发</button>`,
          },
        },
      },
    });
    expect(w.text()).toContain("窑变微光");
    await w.get(".go").trigger("click");
    expect(w.emitted("submit")?.[0]?.[0]).toBe("改写这段");
    const sel = w.emitted("submit")?.[0]?.[1] as { quote: string } | null;
    expect(sel?.quote).toBe("窑变微光");

    // 过了新鲜期的选区不再跟随：放手 12s 后说话不携带
    liveSelection.value = { ...liveSelection.value!, ts: Date.now() - SELECTION_FRESH_MS - 1 };
    await w.get(".go").trigger("click");
    expect(w.emitted("submit")?.[1]?.[1]).toBeNull();
    liveSelection.value = null;
  });
});
