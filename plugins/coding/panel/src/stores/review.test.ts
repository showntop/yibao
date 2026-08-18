import { describe, expect, it } from "vitest";
import { createReviewStore } from "./review";
import { permSummary } from "../lib/format";

const it1 = { rid: "perm_s1_1", sid: "s1", tool: "Bash", summary: "ls", params: { command: "ls" } };
const it2 = { rid: "perm_s1_2", sid: "s1", tool: "Edit", summary: "a.py", params: {} };
const it3 = { rid: "perm_s2_3", sid: "s2", tool: "Write", summary: "b.py", params: {} };

describe("review store", () => {
  it("upsert 按 rid 幂等;resolve 移除;groups 按 sid 分组保序", () => {
    const s = createReviewStore();
    s.upsert(it1); s.upsert(it2); s.upsert(it3);
    expect(s.state.items).toHaveLength(3);
    s.upsert({ ...it1, summary: "ls -la" }); // 同 rid 覆盖不重复
    expect(s.state.items).toHaveLength(3);
    expect(s.state.items[0]!.summary).toBe("ls -la");
    const g = s.groups.value;
    expect(g.map((x) => x.sid)).toEqual(["s1", "s2"]);
    expect(g[0]!.items.map((x) => x.rid)).toEqual(["perm_s1_1", "perm_s1_2"]);
    s.resolve("perm_s1_1");
    expect(s.state.items.map((x) => x.rid)).toEqual(["perm_s1_2", "perm_s2_3"]);
    s.resolve("perm_nonexistent_9"); // 静默
    expect(s.state.items).toHaveLength(2);
  });

  it("snapshot 全量替换", () => {
    const s = createReviewStore();
    s.upsert(it1);
    s.snapshot([it3]);
    expect(s.state.items.map((x) => x.rid)).toEqual(["perm_s2_3"]);
  });
});

describe("permSummary", () => {
  it("command/file_path/path 取一,否则 json;单行截 80", () => {
    expect(permSummary("Bash", { command: "ls -la" })).toBe("ls -la");
    expect(permSummary("Edit", { file_path: "/tmp/a.py" })).toBe("/tmp/a.py");
    expect(permSummary("Write", { path: "b.py" })).toBe("b.py");
    expect(permSummary("Read", { offset: 1 })).toBe('{"offset":1}');
    expect(permSummary("Bash", { command: "a\nb" })).toBe("a b");
    expect(permSummary("Bash", { command: "x".repeat(100) }).length).toBe(80);
    expect(permSummary("Bash", null)).toBe("{}");
  });
});
