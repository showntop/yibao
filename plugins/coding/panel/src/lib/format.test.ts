import { describe, expect, it } from "vitest";
import { doneStatusText, fmtCost, fmtTok, humanFirstLine, relTime } from "./format";

describe("fmtTok", () => {
  it("千以下原样", () => {
    expect(fmtTok(999)).toBe("999");
    expect(fmtTok(0)).toBe("0");
  });
  it("千 → k(一位小数)", () => {
    expect(fmtTok(1500)).toBe("1.5k");
  });
  it("百万 → M(一位小数)", () => {
    expect(fmtTok(2300000)).toBe("2.3M");
  });
  it("非有限数 → 0", () => {
    expect(fmtTok(Infinity)).toBe("0");
    expect(fmtTok(NaN)).toBe("0");
  });
});

describe("fmtCost", () => {
  it("美元四位小数", () => {
    expect(fmtCost(0.123456)).toBe("$0.1235");
    expect(fmtCost(0)).toBe("$0.0000");
  });
  it("非有限数 → $0.0000", () => {
    expect(fmtCost(NaN)).toBe("$0.0000");
  });
});

describe("relTime", () => {
  const now = new Date("2026-08-18T12:00:00Z").getTime();
  it("1 分钟内 → 刚刚", () => {
    expect(relTime(now, now - 30_000)).toBe("刚刚");
  });
  it("分钟", () => {
    expect(relTime(now, now - 5 * 60_000)).toBe("5 分钟前");
  });
  it("小时", () => {
    expect(relTime(now, now - 3 * 3_600_000)).toBe("3 小时前");
  });
  it("天(<7)", () => {
    expect(relTime(now, now - 2 * 86_400_000)).toBe("2 天前");
  });
  it("≥7 天 → 绝对日期 YYYY-MM-DD(本地时区,与 wall subtitle 语义一致)", () => {
    const ts = now - 10 * 86_400_000;
    const d = new Date(ts);
    const pad = (x: number) => String(x).padStart(2, "0");
    expect(relTime(now, ts)).toBe(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
  });
  it("秒级 ts(<1e12)自动 ×1000", () => {
    expect(relTime(now, (now - 5 * 60_000) / 1000)).toBe("5 分钟前");
  });
  it("未来 ts 不负 → 刚刚", () => {
    expect(relTime(now, now + 60_000)).toBe("刚刚");
  });
  it("坏值 → 空串", () => {
    expect(relTime(now, "not-a-date")).toBe("");
    expect(relTime(now, null)).toBe("");
    expect(relTime(now, undefined)).toBe("");
  });
});

describe("humanFirstLine", () => {
  it("普通人话首行直出", () => {
    expect(humanFirstLine("编译失败\n细节忽略")).toBe("编译失败");
  });
  it("跳过空行 / < 开头协议行 / 堆栈帧行", () => {
    const text = [
      "",
      "   ",
      "<tool_use_error>boom</tool_use_error>",
      "  at foo (bar.ts:1:2)",
      'File "x.py", line 3',
      "Traceback (most recent call last):",
      "真正的原因",
    ].join("\n");
    expect(humanFirstLine(text)).toBe("真正的原因");
  });
  it("剥行内残留标签(人话与闭合标签同行)", () => {
    expect(humanFirstLine("失败原因</tool_use_error>")).toBe("失败原因");
  });
  it("全非人话 / 空文本 → 兜底文案", () => {
    expect(humanFirstLine("<err>x</err>\n at y")).toBe("执行出错（点「详情」查看全文）");
    expect(humanFirstLine("")).toBe("执行出错（点「详情」查看全文）");
  });
});

describe("doneStatusText", () => {
  it("全字段:✓ 完成 · Ns · tok · $(cost 三位小数)", () => {
    expect(doneStatusText({ duration_ms: 23400, input_tokens: 1200, output_tokens: 300, cost_usd: 0.12345 }))
      .toBe("✓ 完成 · 23s · 1.5k tok · $0.123");
  });
  it("无 usage / 空 usage → 仅「✓ 完成」", () => {
    expect(doneStatusText(null)).toBe("✓ 完成");
    expect(doneStatusText(undefined)).toBe("✓ 完成");
    expect(doneStatusText({})).toBe("✓ 完成");
  });
  it("codex 容缺:cost_usd null → 无成本段", () => {
    expect(doneStatusText({ duration_ms: 1000, input_tokens: 5, cost_usd: null })).toBe("✓ 完成 · 1s · 5 tok");
  });
  it("坏值各段静默略过,绝不上抛", () => {
    expect(doneStatusText({ duration_ms: NaN, input_tokens: undefined, cost_usd: Infinity })).toBe("✓ 完成");
    expect(doneStatusText({ duration_ms: "x" as unknown as number })).toBe("✓ 完成");
  });
  it("tok 为 0 不显示 token 段", () => {
    expect(doneStatusText({ duration_ms: 500, input_tokens: 0, output_tokens: 0 })).toBe("✓ 完成 · 1s");
  });
});
