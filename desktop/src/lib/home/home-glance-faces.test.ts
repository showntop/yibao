import { describe, expect, it } from "vitest";
import {
  catchFace,
  glimpseFace,
  remindCardFaces,
  pickSpark,
  pluginGlanceLine,
  pluginHasGlance,
  readScratch,
  readScratchTint,
  readSparkDismiss,
  remindFaces,
  remindTight,
  taskFaces,
  todayBands,
  writeScratch,
  writeScratchTint,
  writeSparkDismiss,
} from "./home-glance-faces.ts";
import type { FeedItem, MemItem, PerceptionItem, PendingConfirm, RunningTask } from "../brain";

function feed(partial: Partial<FeedItem> & Pick<FeedItem, "id" | "text">): FeedItem {
  return {
    ts: 1,
    kind: "reminder",
    read: 0,
    status: "none",
    ...partial,
  };
}

function mem(id: string, text: string): MemItem {
  return { id, text, ns: "", label: "译宝" };
}

describe("remind faces", () => {
  it("prefers pending widget rows with a clock, and tightens the next 90 minutes", () => {
    const now = new Date("2026-08-21T10:00:00");
    const faces = remindFaces(
      [{ text: "喝水", when: "每天 10:20" }, { text: "开会", when: "每天 18:00" }],
      [feed({ id: 1, text: "旧的已响" })],
      now,
    );
    expect(faces).toEqual([
      { text: "喝水", when: "每天 10:20", tight: true },
      { text: "开会", when: "每天 18:00", tight: false },
    ]);
    expect(remindTight("每天 09:00", now)).toBe(false);
  });

  it("falls back to feed reminders and stays empty when both are vacant", () => {
    expect(remindFaces(undefined, [feed({ id: 2, text: "关火" })])).toEqual([
      { text: "关火", when: "", tight: false },
    ]);
    expect(remindFaces([], [])).toEqual([]);
    expect(remindFaces([{ text: "  " }], [feed({ id: 3, text: "x", status: "ignore" })])).toEqual([]);
  });
});

describe("task faces", () => {
  it("marks the running job as waiting when a confirm is outstanding", () => {
    const tasks = [{ id: "t1", kind: "agent" as const, label: "写摘要", prompt: "", status: "running" as const, created_at: 1 }];
    const approvals = [{ id: "a1", tool_id: "coding", label: "跑命令", desc: "" }] as PendingConfirm[];
    expect(taskFaces(tasks as RunningTask[], approvals)).toEqual([
      { id: "t1", label: "写摘要", stuck: "等你" },
    ]);
    expect(taskFaces(tasks as RunningTask[], [])).toEqual([
      { id: "t1", label: "写摘要", stuck: "在跑" },
    ]);
    expect(taskFaces([], [])).toEqual([]);
  });
});

describe("today stain", () => {
  it("paints an unstained edge when the day is empty", () => {
    expect(todayBands({ done: 0, chats: 0, mems: 0 })).toEqual({
      values: [0, 0, 0, 0, 0, 0, 0, 0],
      empty: true,
    });
  });

  it("uses app seconds when the distiller has a day, else the three counts", () => {
    const fromApps = todayBands({ done: 0, chats: 0, mems: 0, appSeconds: { Cursor: 80, Mail: 20 } });
    expect(fromApps.empty).toBe(false);
    expect(fromApps.values[0]).toBe(1);
    expect(fromApps.values[1]).toBe(0.25);
    const fromCounts = todayBands({ done: 2, chats: 4, mems: 1 });
    expect(fromCounts.empty).toBe(false);
    expect(fromCounts.values[2]).toBe(1);
  });
});

describe("plugin glance line", () => {
  it("resolves the first list row into one sentence and hides empty widgets", () => {
    const schema = {
      type: "list" as const,
      bind: { items: "$data.rows" },
      item: { title: "$item.text", subtitle: "$item.when" },
    };
    expect(pluginGlanceLine(schema, { rows: [{ text: "关火", when: "每天 18:00" }] })).toBe("关火 · 每天 18:00");
    expect(pluginHasGlance({ schema, data: { rows: [] } })).toBe(false);
    expect(pluginGlanceLine({ type: "list" }, { rows: [{ text: "闪念" }] })).toBe("闪念");
  });
});

describe("spark / glimpse / catch", () => {
  it("picks a memory that shares tokens with the current focus", () => {
    const memories = [mem("1", "喜欢冷色界面"), mem("2", "主屏零件要能按")];
    expect(pickSpark(memories, "改主屏零件", null)?.id).toBe("2");
    expect(pickSpark(memories, "天气股票", "1")).toBeNull();
    expect(pickSpark(memories, "", "1")?.id).toBe("2");
  });

  it("only shows a fresh frontmost app as the fogged window", () => {
    const now = 1_000_000;
    const items: PerceptionItem[] = [
      { id: 1, ts: now / 1000 - 60, source: "app", kind: "frontmost", payload: { app: "Cursor", title: "home-assembly.ts" }, sensitivity: "S1" },
    ];
    expect(glimpseFace(items, now)).toEqual({ app: "Cursor", title: "home-assembly.ts" });
    expect(glimpseFace(items, now + 9 * 60 * 1000)).toBeNull();
  });

  it("prefers a fresh clipboard, else the latest note", () => {
    const now = 1_000_000;
    const clip: PerceptionItem[] = [
      { id: 2, ts: now / 1000 - 20, source: "clipboard", kind: "copy", payload: { text: "一段刚复制的话" }, sensitivity: "S2" },
    ];
    expect(catchFace(clip, "旧闪念", now)).toEqual({ kind: "clipboard", text: "一段刚复制的话" });
    expect(catchFace([], "刚存的选题", now)).toEqual({ kind: "note", text: "刚存的选题" });
    expect(catchFace([], "", now)).toBeNull();
  });
});

describe("local scratch and spark dismiss", () => {
  it("round-trips scratch text and forgets a spark only for today", () => {
    const store: Record<string, string> = {};
    const storage = {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value; },
    };
    writeScratch("先扔着", storage);
    expect(readScratch(storage)).toBe("先扔着");
    writeScratchTint("moss", storage);
    expect(readScratchTint(storage)).toBe("moss");
    expect(readScratchTint({ getItem: () => "nope" })).toBe("amber");
    writeSparkDismiss("m2", "2026-08-21", storage);
    expect(readSparkDismiss(storage, "2026-08-21")).toBe("m2");
    expect(readSparkDismiss(storage, "2026-08-22")).toBeNull();
  });
});

describe("remindCardFaces", () => {
  const NOW = new Date("2026-08-30T10:15:00");

  it("derives card state from feed timestamps: past=已响, today-upcoming=待响", () => {
    const cards = remindCardFaces(
      undefined,
      [
        feed({ id: 1, text: "09:00 开战会", ts: new Date("2026-08-30T09:00:00").getTime() }),
        feed({ id: 2, text: "该交周报了", ts: new Date("2026-08-30T14:00:00").getTime() }),
        feed({ id: 3, text: "昨天的事", ts: new Date("2026-08-29T09:00:00").getTime() }),
        feed({ id: 4, text: "已忽略", ts: new Date("2026-08-30T08:00:00").getTime(), status: "ignore" }),
      ],
      NOW,
    );
    expect(cards).toEqual([
      { text: "09:00 开战会", when: "09:00", state: "done" },
      { text: "该交周报了", when: "14:00", state: "due" },
    ]);
  });

  it("derives recurring rows' state from their HH:MM and keeps 每天 in the when line", () => {
    const cards = remindCardFaces(
      [
        { text: "喝水", when: "每天 10:20" }, // 未来 5 分钟 → 待响
        { text: "开战会", when: "每天 09:00" }, // 已过 → 已响
        { text: "没时刻的", when: "工作日" },
      ],
      [],
      NOW,
    );
    expect(cards).toEqual([
      { text: "喝水", when: "每天 10:20", state: "due" },
      { text: "开战会", when: "每天 09:00", state: "done" },
      { text: "没时刻的", when: "工作日", state: "daily" },
    ]);
  });

  it("empty everything renders no rows", () => {
    expect(remindCardFaces(undefined, [], NOW)).toEqual([]);
  });
});
