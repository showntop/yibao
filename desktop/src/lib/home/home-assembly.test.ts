import { describe, expect, it, afterEach } from "vitest";
import { defaultLayout } from "./home-widgets.ts";
import {
  HOME_PARTS,
  HOME_PRESET_DEFAULT,
  HOME_PRESETS,
  isHomePresetId,
  presentationOf,
  pluginPartId,
  resetPluginParts,
  foldHandleStyle,
  FOLD_HANDLE,
  resolveAssembly,
  resolveFrame,
  faceOf,
  spineLimitOf,
  snapBox,
  snapToGuides,
  snapValue,
  settleSnap,
  SNAP_GAP,
  syncPluginParts,
  defaultPeek,
  collapsibleOf,
  collapsibleSidesOf,
  gridStageStyle,
} from "./home-assembly.ts";

describe("home-assembly catalog", () => {
  it("registers chat and composer as parts, not only glance tiles", () => {
    const ids = HOME_PARTS.map((p) => p.id);
    expect(ids).toContain("chat");
    expect(ids).toContain("composer");
    expect(ids).toContain("sessions");
    expect(ids).toContain("spark");
    expect(ids).toContain("glimpse");
    expect(ids).toContain("catch");
    expect(ids).toContain("scratch");
    expect(ids).toContain("when");
    expect(ids).toContain("line");
    expect(ids).toContain("jot");
    expect(ids).toContain("bench");
    expect(HOME_PARTS.find((p) => p.id === "chat")?.kind).toBe("work");
    expect(HOME_PARTS.find((p) => p.id === "composer")?.kind).toBe("input");
    expect(HOME_PARTS.find((p) => p.id === "chat")?.presentations).toEqual(["thread", "paper", "talk"]);
    expect(HOME_PARTS.find((p) => p.id === "spark")?.kind).toBe("glance");
  });
});

describe("home-assembly presets", () => {
  it("defaults to rails and keeps desk, salon, and canvas", () => {
    expect(HOME_PRESET_DEFAULT).toBe("rails");
    expect(isHomePresetId("rails")).toBe(true);
    expect(isHomePresetId("desk")).toBe(true);
    expect(isHomePresetId("salon")).toBe(true);
    expect(isHomePresetId("canvas")).toBe(true);
    expect(HOME_PRESETS.rails.place).toBe("grid");
    expect(HOME_PRESETS.desk.place).toBe("grid");
    expect(HOME_PRESETS.salon.place).toBe("grid");
    expect(HOME_PRESETS.canvas.place).toBe("canvas");
    expect(HOME_PRESETS.salon.presentations.chat).toBe("talk");
  });

  it("gives rails/desk/salon a grid template and canvas a scatter of frames", () => {
    expect(HOME_PRESETS.rails.grid?.tracks?.map((t) => t.area)).toEqual(["left", "main", "right"]);
    expect(HOME_PRESETS.desk.grid?.areas).toMatch(/paper/);
    expect(HOME_PRESETS.salon.grid?.justify).toBe("center");
    expect(HOME_PRESETS.canvas.frames?.chat).toBeTruthy();
    expect("frames" in HOME_PRESETS.rails).toBe(false);
    expect("grid" in HOME_PRESETS.canvas).toBe(false);
  });
});

describe("resolveFrame", () => {
  it("resolves right/bottom pins against the stage", () => {
    const box = resolveFrame({ right: 280, top: 0, width: 280, bottom: 0, z: 1 }, { width: 1280, height: 800 });
    expect(box.left).toBe(720);
    expect(box.width).toBe(280);
    expect(box.height).toBe(800);
  });
});

describe("resolveAssembly", () => {
  it("picks presentations and grid areas from structure presets", () => {
    const rails = resolveAssembly("rails", defaultLayout());
    expect(rails.place).toBe("grid");
    expect(presentationOf(rails, "chat")).toBe("thread");
    expect(presentationOf(rails, "sessions")).toBe("list");
    expect(presentationOf(rails, "now")).toBe("inspector");
    expect(presentationOf(rails, "mind")).toBe("map");
    expect(rails.items.find((i) => i.id === "chat")?.area).toBe("main");
    expect(rails.items.find((i) => i.id === "chat")?.frame).toBeUndefined();
    // 三栏左栏精简：信息卡（need/tasks/remind/glimpse/catch）不在 rails 渲染，会话列表才有空间
    expect(rails.items.find((i) => i.id === "need")).toBeUndefined();
    expect(rails.items.find((i) => i.id === "tasks")).toBeUndefined();
    expect(rails.items.find((i) => i.id === "remind")).toBeUndefined();
    expect(rails.items.find((i) => i.id === "glimpse")).toBeUndefined();
    expect(rails.items.find((i) => i.id === "catch")).toBeUndefined();
    expect(rails.items.find((i) => i.id === "spark")?.area).toBe("left");
    expect(rails.items.find((i) => i.id === "mind")?.area).toBe("left");
    expect(rails.items.find((i) => i.id === "today")?.area).toBe("left");
    expect(rails.items.find((i) => i.id === "scratch")).toBeUndefined();
    expect(collapsibleOf("rails")).toEqual(["left", "right"]);

    const desk = resolveAssembly("desk", defaultLayout());
    expect(desk.place).toBe("grid");
    expect(presentationOf(desk, "chat")).toBe("paper");
    expect(presentationOf(desk, "sessions")).toBe("spine");
    expect(presentationOf(desk, "now")).toBe("note");
    expect(presentationOf(desk, "mind")).toBe("tile");
    expect(desk.items.find((i) => i.id === "sessions")?.area).toBe("spine");
    expect(desk.items.find((i) => i.id === "now")?.area).toBe("note");
    expect(desk.items.find((i) => i.id === "now")?.attach).toBeUndefined();
    expect(desk.items.find((i) => i.id === "when")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "line")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "jot")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "bench")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "spark")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "need")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "tasks")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "catch")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "glimpse")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "today")?.area).toBe("end");
    expect(desk.items.find((i) => i.id === "scratch")?.area).toBe("end");
    expect(desk.items.find((i) => i.id === "remind")?.area).toBe("end");
    expect(desk.items.find((i) => i.id === "identity")?.area).toBe("ident");
    expect(desk.items.find((i) => i.id === "identity")?.pinEnd).toBeFalsy();
    expect(desk.items.find((i) => i.id === "mind")?.grow).toBeFalsy();
    expect(desk.items.find((i) => i.id === "scratch")?.grow).toBe(true);
    expect(desk.items.find((i) => i.id === "chat")?.grow).toBe(true);
    expect(desk.items.find((i) => i.id === "now")?.grow).toBe(true);
    expect(desk.items.find((i) => i.id === "sessions")?.grow).toBe(true);
    expect(collapsibleOf("desk")).toEqual(["note"]);

    const salon = resolveAssembly("salon", defaultLayout());
    expect(salon.place).toBe("grid");
    expect(presentationOf(salon, "chat")).toBe("talk");
    expect(presentationOf(salon, "sessions")).toBe("cards");
    expect(presentationOf(salon, "identity")).toBe("seat");
    expect(salon.items.find((i) => i.id === "chat")?.area).toBe("chat");
    expect(salon.items.find((i) => i.id === "now")).toBeUndefined();
    expect(salon.items.find((i) => i.id === "tasks")).toBeUndefined();
    expect(salon.items.find((i) => i.id === "spark")?.area).toBe("identity");
    expect(salon.items.find((i) => i.id === "glimpse")).toBeUndefined();
    expect(salon.grid?.justify).toBe("center");
  });

  it("keeps rails columns stacked in named areas, with chat and sessions growing", () => {
    const rails = resolveAssembly("rails", defaultLayout());
    expect(rails.grid?.stacks.left).toEqual([
      "identity", "spark", "mind", "today", "sessions",
    ]);
    expect(rails.grid?.stacks.main).toEqual(["chat", "composer"]);
    expect(rails.grid?.stacks.right).toEqual(["now"]);
    expect(rails.items.find((i) => i.id === "sessions")?.grow).toBe(true);
    expect(rails.items.find((i) => i.id === "chat")?.grow).toBe(true);
    expect(gridStageStyle(rails.grid!).display).toBe("grid");
    expect(gridStageStyle(rails.grid!).gridTemplateColumns).toBe("300px minmax(0,1fr) 264px");
    expect(gridStageStyle(rails.grid!).gridTemplateAreas).toBe('"left main right"');
  });

  it("folds a rails left track and lets main take the leftover column", () => {
    const open = resolveAssembly("rails", defaultLayout());
    const folded = resolveAssembly("rails", defaultLayout(), { collapsed: ["left"] });
    expect(folded.items.find((i) => i.id === "identity")).toBeUndefined();
    expect(folded.items.find((i) => i.id === "sessions")).toBeUndefined();
    expect(folded.items.find((i) => i.id === "now")).toBeTruthy();
    expect(folded.items.find((i) => i.id === "chat")?.area).toBe("main");
    expect(folded.grid?.stacks.left).toBeUndefined();
    expect(folded.grid?.columns.startsWith("minmax(0,1fr)")).toBe(true);
    expect(folded.grid?.areas).toBe('"main right"');
    expect(folded.grid?.fold.find((g) => g.name === "left")?.folded).toBe(true);
    expect(folded.grid?.fold.find((g) => g.name === "left")?.side).toBe("start");
    expect(open.grid?.areas).toBe('"left main right"');
  });

  it("folds a rails right track and keeps the session column", () => {
    const folded = resolveAssembly("rails", defaultLayout(), { collapsed: ["right"] });
    expect(folded.items.find((i) => i.id === "now")).toBeUndefined();
    expect(folded.items.find((i) => i.id === "sessions")).toBeTruthy();
    expect(folded.grid?.stacks.right).toBeUndefined();
    expect(folded.grid?.areas).toBe('"left main"');
    expect(folded.grid?.fold.find((g) => g.name === "right")?.side).toBe("end");
  });

  it("folds the desk note column and lets the paper take its space", () => {
    const open = resolveAssembly("desk", defaultLayout());
    const folded = resolveAssembly("desk", defaultLayout(), { collapsed: ["note"] });
    expect(open.items.find((i) => i.id === "now")?.area).toBe("note");
    expect(folded.items.find((i) => i.id === "now")).toBeUndefined();
    expect(folded.items.find((i) => i.id === "chat")?.area).toBe("paper");
    expect(folded.grid?.stacks.note).toBeUndefined();
    expect(folded.grid?.columns).toBe("164px 12px 40px minmax(0,1fr) 12px 164px");
    expect(folded.grid?.areas).toBe('"start . spine paper . end" "ident . . compose . ."');
    expect(open.grid?.areas).toContain("note");
  });

  it("maps collapsible areas to start/end sides for fold handles and peek", () => {
    expect(collapsibleSidesOf("rails")).toEqual({ left: "start", right: "end" });
    expect(collapsibleSidesOf("desk")).toEqual({ note: "end" });
    expect(collapsibleSidesOf("salon")).toEqual({});
    expect(collapsibleSidesOf("canvas")).toEqual({});
  });

  it("counts minmax tracks as one column when assigning fold sides", () => {
    // 回归：field 的 columns 全是带空格的 minmax——split(" ") 会把 3 列数成 6，
    // 侧别判定错乱，「今日」入口切错折叠模型
    expect(collapsibleSidesOf("field")).toEqual({ axis: "start" });
  });

  it("parks fold handles in the stage corners", () => {
    const stage = { width: 1280, height: 800 };
    const rails = resolveAssembly("rails", defaultLayout(), { stage });
    const left = rails.grid?.fold.find((g) => g.side === "start")!;
    const right = rails.grid?.fold.find((g) => g.side === "end")!;
    expect(foldHandleStyle(left, stage).left).toBe("8px");
    expect(foldHandleStyle(left, stage).top).toBe("8px");
    expect(foldHandleStyle(right, stage).left).toBe(`${stage.width - 8 - FOLD_HANDLE.width}px`);
    expect(foldHandleStyle(right, stage).top).toBe("8px");
  });

  it("skips hidden parts without collapsing the rest of a grid", () => {
    const prefs = { ...defaultLayout(), hidden: ["chat" as const, "remind" as const] };
    const desk = resolveAssembly("desk", prefs);
    expect(desk.items.find((i) => i.id === "chat")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "sessions")?.area).toBe("spine");
    expect(desk.items.find((i) => i.id === "now")?.area).toBe("note");
    expect(desk.items.find((i) => i.id === "remind")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "mind")?.area).toBe("start");
    expect(desk.items.find((i) => i.id === "composer")?.area).toBe("compose");
  });

  it("skips unknown ids and keeps factory grid cells for known parts", () => {
    const desk = resolveAssembly("desk", defaultLayout(), {
      extra: [
        { id: "ghost" as never, frame: { left: 0, top: 0, width: 10, height: 10 } },
      ],
    });
    expect(desk.items.find((i) => i.id === "ghost" as never)).toBeUndefined();
    expect(desk.items.find((i) => i.id === "today")?.area).toBe("end");
  });

  it("lets a canvas overlay move a part, and ignores overlay frames on a grid preset", () => {
    const prefs = defaultLayout();
    prefs.layouts = {
      canvas: { frames: { mind: { left: 40, top: 80, width: 180, height: 200, z: 4 } } },
      salon: { frames: { mind: { left: 40, top: 80, width: 180, height: 200, z: 4 } } },
    };
    const canvas = resolveAssembly("canvas", prefs, { stage: { width: 800, height: 600 } });
    const mind = canvas.items.find((i) => i.id === "mind")!;
    expect(canvas.place).toBe("canvas");
    expect(mind.frame?.left).toBe(40);
    expect(mind.frame?.top).toBe(80);
    expect(mind.frame?.width).toBe(180);
    expect(mind.frame?.height).toBe(200);
    expect(mind.frame?.z).toBe(4);

    const salon = resolveAssembly("salon", prefs, { stage: { width: 800, height: 600 } });
    expect(salon.items.find((i) => i.id === "mind")?.area).toBe("mind");
    expect(salon.items.find((i) => i.id === "mind")?.frame).toBeUndefined();
  });

  it("binds the desk spine to the paper and parks compose under the sheet", () => {
    const desk = resolveAssembly("desk", defaultLayout());
    expect(desk.grid?.ground).toBe("desk");
    expect(desk.grid?.columnGap).toBe(0);
    expect(desk.grid?.rows).toBe("minmax(0,1fr) minmax(min-content, auto)");
    expect(desk.grid?.areas).toBe('"start . spine paper . note . end" "ident . . compose . . . ."');
    expect(desk.grid?.columns).toContain("40px");
    expect(desk.grid?.columns).toContain("164px");
    expect(desk.grid?.columns).toContain("188px");
    expect(desk.grid?.columns.split("164px")).toHaveLength(3);
  });

  it("drops glance tiles in compact desk but keeps paper, compose, and spine", () => {
    const desk = resolveAssembly("desk", defaultLayout(), { compact: true });
    expect(desk.items.find((i) => i.id === "mind")).toBeUndefined();
    expect(desk.items.find((i) => i.id === "chat")?.area).toBe("paper");
    expect(desk.items.find((i) => i.id === "sessions")?.area).toBe("spine");
    expect(desk.items.find((i) => i.id === "composer")?.area).toBe("compose");
    expect(desk.grid?.areas).toBe('"spine paper" ". compose"');
  });

  it("drops the room objects in compact salon but keeps talk and the speaking bar", () => {
    const salon = resolveAssembly("salon", defaultLayout(), { compact: true });
    expect(salon.items.find((i) => i.id === "identity")).toBeUndefined();
    expect(salon.items.find((i) => i.id === "chat")?.area).toBe("chat");
    expect(salon.items.find((i) => i.id === "composer")?.area).toBe("composer");
  });

  it("scatters canvas factory frames instead of lining them up as three rails", () => {
    const canvas = resolveAssembly("canvas", defaultLayout(), { stage: { width: 1280, height: 800 } });
    const identity = canvas.items.find((i) => i.id === "identity")!;
    const chat = canvas.items.find((i) => i.id === "chat")!;
    const now = canvas.items.find((i) => i.id === "now")!;
    expect(canvas.place).toBe("canvas");
    expect(chat.frame).toBeTruthy();
    expect(now.frame).toBeTruthy();
    expect(chat.frame!.left).not.toBe(identity.frame!.left + identity.frame!.width + 8);
    expect(canvas.grid).toBeUndefined();
    expect(collapsibleOf("canvas")).toEqual([]);
  });

  it("reads a part face with fallback when the part is not placed", () => {
    const rails = resolveAssembly("rails", defaultLayout());
    expect(faceOf(rails, "chat")).toBe("thread");
    expect(faceOf(rails, "need", "tile")).toBe("tile");
    expect(spineLimitOf(rails)).toBe(0);
    expect(spineLimitOf(resolveAssembly("desk", defaultLayout()))).toBe(0);
  });

  it("opens peek when now is independently present", () => {
    expect(defaultPeek(resolveAssembly("rails", defaultLayout()))).toBe(true);
    expect(defaultPeek(resolveAssembly("desk", defaultLayout()))).toBe(true);
    expect(defaultPeek(resolveAssembly("canvas", defaultLayout()))).toBe(true);
    expect(defaultPeek({
      preset: "desk",
      place: "grid",
      items: [
        { id: "chat", kind: "work", presentation: "paper", area: "paper" },
        { id: "now", kind: "context", presentation: "inspector", area: "note" },
      ],
    })).toBe(true);
    expect(defaultPeek({
      preset: "rails",
      place: "canvas",
      items: [
        { id: "chat", kind: "work", presentation: "thread", frame: { left: 0, top: 0, width: 100, height: 100, z: 1 } },
        {
          id: "now",
          kind: "context",
          presentation: "note",
          frame: { left: 0, top: 0, width: 100, height: 100, z: 1 },
          attach: { to: "chat", edge: "end" },
        },
      ],
    })).toBe(false);
  });
});

describe("plugin glances", () => {
  afterEach(() => resetPluginParts());

  it("grid presets only carry plugin cards explicitly listed in stacks; sidecar alone never forces them in", () => {
    const ids = syncPluginParts([{ panel: "notes:widget" }, { panel: "weather:now" }]);
    // 预设 stacks 没声明 → 不上桌面
    const rails = resolveAssembly("rails", defaultLayout(), { pluginIds: ids });
    expect(rails.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();
    expect(rails.grid?.stacks.left).not.toContain(pluginPartId("notes:widget"));

    const desk = resolveAssembly("desk", defaultLayout(), { pluginIds: ids });
    expect(desk.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();

    const compact = resolveAssembly("desk", defaultLayout(), { compact: true, pluginIds: ids });
    expect(compact.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();

    const salon = resolveAssembly("salon", defaultLayout(), { pluginIds: ids });
    expect(salon.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();

    const canvas = resolveAssembly("canvas", defaultLayout(), {
      stage: { width: 1280, height: 800 },
      pluginIds: ids,
    });
    expect(canvas.items.find((i) => i.id === pluginPartId("notes:widget"))?.frame).toBeTruthy();
  });

  it("grid presets never force plugin cards in, folded or not", () => {
    const ids = syncPluginParts([{ panel: "notes:widget" }]);
    const folded = resolveAssembly("rails", defaultLayout(), {
      stage: { width: 1280, height: 800 },
      collapsed: ["left"],
      pluginIds: ids,
    });
    expect(folded.items.find((i) => i.id === pluginPartId("notes:widget"))).toBeUndefined();
  });
});

describe("canvas snap", () => {
  it("snaps a free coordinate onto the 8px grid", () => {
    expect(snapValue(0)).toBe(0);
    expect(snapValue(13)).toBe(16);
    expect(snapValue(11)).toBe(8);
    expect(snapValue(-3)).toBe(0);
  });

  it("only magnetizes onto a nearby guide, otherwise follows the pointer", () => {
    expect(snapToGuides(13, [12])).toBe(12);
    expect(snapToGuides(13, [40])).toBe(13);
  });

  it("follows the pointer when nothing is in magnet range", () => {
    const moved = snapBox({ left: 13, top: 21, width: 160, height: 88 }, []);
    expect(moved.left).toBe(13);
    expect(moved.top).toBe(21);
    expect(moved.xs).toEqual([]);
    expect(moved.ys).toEqual([]);
  });

  it("snaps onto a part edge from well outside the grid pitch", () => {
    const align = snapBox(
      { left: 140, top: 160, width: 148, height: 88 },
      [{ left: 160, top: 180, width: 468, height: 148 }],
    );
    expect(align.left).toBe(160);
    expect(align.top).toBe(180);
    expect(align.xs).toContain(160);
    expect(align.ys).toContain(180);
  });

  it("does not snap to a part that is still far away", () => {
    const free = snapBox(
      { left: 80, top: 80, width: 148, height: 88 },
      [{ left: 400, top: 400, width: 468, height: 148 }],
    );
    expect(free.left).toBe(80);
    expect(free.top).toBe(80);
  });

  it("snaps the trailing edge so two tiles share a line", () => {
    const box = { left: 330, top: 8, width: 148, height: 88 };
    const other = { left: 160, top: 0, width: 148, height: 168 };
    const snapped = snapBox(box, [other]);
    expect(snapped.left).toBe(316);
    expect(snapped.xs).toContain(308);
  });

  it("leaves a gap when parts abut, and none when they share an edge", () => {
    const other = { left: 160, top: 0, width: 148, height: 168 };
    const beside = snapBox({ left: 318, top: 8, width: 148, height: 88 }, [other]);
    expect(beside.left).toBe(other.left + other.width + SNAP_GAP);
    const aligned = snapBox({ left: 155, top: 180, width: 148, height: 88 }, [other]);
    expect(aligned.left).toBe(160);
  });

  it("snaps to the stage origin when close, not when far", () => {
    const close = snapBox(
      { left: 10, top: 8, width: 80, height: 80 },
      [],
      { width: 1280, height: 800 },
    );
    expect(close.left).toBe(0);
    expect(close.top).toBe(0);

    const far = snapBox(
      { left: 80, top: 80, width: 80, height: 80 },
      [],
      { width: 1280, height: 800 },
    );
    expect(far.left).toBe(80);
    expect(far.top).toBe(80);
  });

  it("holds a snapped edge until the pointer pulls past the sticky range", () => {
    const other = [{ left: 160, top: 180, width: 468, height: 148 }];
    const hit = snapBox({ left: 150, top: 170, width: 148, height: 88 }, other);
    expect(hit.left).toBe(160);
    const held = snapBox({ left: 138, top: 170, width: 148, height: 88 }, other, undefined, hit);
    expect(held.left).toBe(160);
    const released = snapBox({ left: 100, top: 170, width: 148, height: 88 }, other, undefined, hit);
    expect(released.left).toBe(100);
  });

  it("settles onto the grid on release when not magnetized", () => {
    expect(settleSnap({ left: 13, top: 21, xs: [], ys: [] })).toEqual({ left: 16, top: 24, xs: [], ys: [] });
    expect(settleSnap({ left: 160, top: 180, xs: [160], ys: [180] }).left).toBe(160);
  });
});
