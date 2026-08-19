// stations 布局 store：工位增删/绑定/聚焦/路由表/左栏派生状态。
import { describe, expect, it } from "vitest";
import { createStationsStore, MAX_STATIONS } from "./stations";

describe("stations store", () => {
  it("初始 1 个空工位,聚焦 1 号(验收样式收敛:默认单工位,多工位按需加)", () => {
    const s = createStationsStore();
    expect(s.state.stations.map((x) => x.id)).toEqual([1]);
    expect(s.state.stations.every((x) => x.boundSid === null)).toBe(true);
    expect(s.state.focusId).toBe(1);
  });

  it("addStation 聚焦新工位;满 3 返回 null(第 4 栏不可加)", () => {
    const s = createStationsStore();
    expect(s.addStation()).toBe(2);
    expect(s.state.focusId).toBe(2);
    expect(s.addStation()).toBe(3);
    expect(s.addStation()).toBe(null);
    expect(s.state.stations).toHaveLength(MAX_STATIONS);
  });

  it("removeStation 仅剩 1 个拒绝;删聚焦工位后聚焦落到首个", () => {
    const s = createStationsStore();
    expect(s.removeStation(1)).toBe(false); // 仅 1 个,拒绝
    s.addStation(); // 1,2
    expect(s.removeStation(2)).toBe(true);
    expect(s.state.stations.map((x) => x.id)).toEqual([1]);
    s.addStation(); s.addStation(); // 1,3,4(id 单调不复用)
    s.focus(4);
    expect(s.removeStation(4)).toBe(true);
    expect(s.state.focusId).toBe(1);
  });

  it("bind/unbind 维护路由表;同 sid 换工位先解旧绑", () => {
    const s = createStationsStore();
    s.bind(1, "a", "codex");
    expect(s.stationForSid("a")).toBe(1);
    expect(s.state.stations[0]!.boundAgent).toBe("codex");
    s.addStation(); // 2
    s.bind(2, "a", "codex"); // 同 sid 绑到 2 号
    expect(s.stationForSid("a")).toBe(2);
    expect(s.state.stations[0]!.boundSid).toBe(null);
    s.unbind(2);
    expect(s.stationForSid("a")).toBe(null);
  });

  it("syncStationSid:站内换 sid 更新路由表;null 等价 unbind", () => {
    const s = createStationsStore();
    s.bind(1, "a", "claude-code");
    s.syncStationSid(1, "b", "codex");
    expect(s.stationForSid("a")).toBe(null);
    expect(s.stationForSid("b")).toBe(1);
    expect(s.state.stations[0]!.boundAgent).toBe("codex");
    s.syncStationSid(1, null);
    expect(s.stationForSid("b")).toBe(null);
    expect(s.state.stations[0]!.boundSid).toBe(null);
  });

  it("bumpRail 仅未绑 sid 生效:perm→waiting,终态→idle,其余流事件→running", () => {
    const s = createStationsStore();
    s.bumpRail("x", "text_delta");
    expect(s.state.railLive["x"]).toBe("running");
    s.bumpRail("x", "permission_request");
    expect(s.state.railLive["x"]).toBe("waiting");
    s.bumpRail("x", "permission_done");
    expect(s.state.railLive["x"]).toBe("running");
    s.bumpRail("x", "done");
    expect(s.state.railLive["x"]).toBe("idle");
    s.bind(1, "x", "claude-code"); // 绑定后派生状态清除且不再受理
    expect(s.state.railLive["x"]).toBeUndefined();
    s.bumpRail("x", "text_delta");
    expect(s.state.railLive["x"]).toBeUndefined();
  });

  it("pickBindTarget:聚焦空→聚焦;否则首个空;都无→聚焦(换绑)", () => {
    const s = createStationsStore();
    expect(s.pickBindTarget()).toBe(1); // 聚焦 1 空
    s.bind(1, "a", "claude-code");
    const id2 = s.addStation(); // 聚焦移到新空工位 2
    expect(s.pickBindTarget()).toBe(id2); // 聚焦空
    s.focus(1);
    expect(s.pickBindTarget()).toBe(id2); // 聚焦非空 → 首个空
    s.bind(id2!, "b", "claude-code");
    expect(s.pickBindTarget()).toBe(1); // 满员 → 聚焦工位换绑
  });
});
