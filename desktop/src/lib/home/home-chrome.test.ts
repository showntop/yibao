// @vitest-environment happy-dom
import { describe, expect, it, vi } from "vitest";
import {
  HOME_CHROME_DEFAULT,
  applyChrome,
  bootChrome,
  chromeOf,
  isHomeChromeId,
  spineCaption,
  spineVisible,
} from "./home-chrome.ts";

describe("home-chrome adapter", () => {
  it("defaults to rails and derives presentations from the preset", () => {
    expect(HOME_CHROME_DEFAULT).toBe("rails");
    expect(isHomeChromeId("rails")).toBe(true);
    expect(isHomeChromeId("desk")).toBe(true);
    expect(isHomeChromeId("canvas")).toBe(true);
    expect(chromeOf("canvas").surface).toBe("thread");
    expect(chromeOf("rails").collapsible).toEqual(["left", "right"]);
    expect(chromeOf("desk").collapsible).toEqual([]);
    expect(chromeOf("canvas").collapsible).toEqual([]);
    expect(chromeOf("rails").peekDensity).toBe("inspector");
    expect(chromeOf("desk").peekDensity).toBe("note");
    expect(chromeOf("rails").mindDensity).toBe("map");
    expect(chromeOf("desk").mindDensity).toBe("tile");
    expect(chromeOf("rails").spineLimit).toBe(0);
    expect(chromeOf("desk").spineLimit).toBe(4);
    expect(chromeOf("rails").surface).toBe("thread");
    expect(chromeOf("desk").surface).toBe("paper");
    expect(isHomeChromeId("salon")).toBe(true);
    expect(chromeOf("salon").surface).toBe("talk");
    expect(chromeOf("salon").sessionVariant).toBe("cards");
  });

  it("shortens spine tabs to two characters", () => {
    expect(spineCaption("调研跟 Nutlope/hal")).toBe("调研");
    expect(spineCaption("关于「译宝」")).toBe("关于");
    expect(spineCaption("新对话")).toBe("页");
    expect(spineCaption("hi")).toBe("hi");
    expect(spineCaption("调研跟 Nutlope/hal", 0)).toBe("调研跟 Nutlope/hal");
    expect(spineCaption("新对话", 0)).toBe("新对话");
  });

  it("keeps the spine to recent pages and the active one", () => {
    const sessions = [
      { id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }, { id: "e" },
    ];
    expect(spineVisible(sessions, "a", 0)).toEqual(sessions);
    expect(spineVisible(sessions, "a", 4).map((s) => s.id)).toEqual(["a", "b", "c", "d"]);
    expect(spineVisible(sessions, "e", 4).map((s) => s.id)).toEqual(["a", "b", "c", "e"]);
  });

  it("boots data-chrome from storage", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); },
    });
    store.set("yibao-chrome", "desk");
    expect(bootChrome()).toBe("desk");
    expect(document.documentElement.dataset.chrome).toBe("desk");
    applyChrome("rails");
    expect(document.documentElement.dataset.chrome).toBe("rails");
    delete document.documentElement.dataset.chrome;
  });
});
