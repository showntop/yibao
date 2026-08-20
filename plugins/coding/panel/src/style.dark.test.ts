// 深色模式守卫:style.css 浅色色板是「对齐 tokens.css 但字面值写死」(沙箱吃不到 tokens.css),
// @media (prefers-color-scheme: dark) 块必须逐一覆盖浅色语义色——漏覆盖 = 深色下白底/深字翻车。
// 新增浅色语义变量时同步在深色块补映射(本测试按名单卡全量)。
// fs 读文件本体:?raw 导入会被 vitest CSS 管线截成空串;node:* 类型垫片见 node-shims.d.ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./style.css", import.meta.url)), "utf8");

describe("style.css 深色模式", () => {
  it("prefers-color-scheme: dark 块存在,且逐一覆盖浅色语义色变量", () => {
    const m = css.match(/@media \(prefers-color-scheme: dark\) \{([\s\S]*?)\n\}/);
    expect(m).toBeTruthy();
    const dark = m![1]!;
    expect(dark).toContain("color-scheme: dark");
    // 浅色 :root 里的全部颜色语义(字体/圆角/时长等非色变量不要求覆盖;
    // --code-bg/--code-text 深底浅字两色通用,也不在名单)
    for (const v of [
      "--bg", "--card", "--surface-2", "--surface-2h",
      "--text", "--muted", "--faint",
      "--accent", "--accent-deep", "--accent-soft", "--accent-line",
      "--border", "--border-strong", "--border-soft", "--line",
      "--green-bg", "--green", "--green-bar", "--green-line",
      "--red-bg", "--red", "--red-bar",
      "--amber", "--amber-bg", "--amber-deep",
      "--ai-bg", "--user-bg",
      "--shadow-card", "--shadow-pop", "--focus-ring",
    ]) {
      expect(dark).toContain(v + ":");
    }
  });

  it("字面值玻璃面(header/composer/err-detail)有深色覆盖", () => {
    const m = css.match(/@media \(prefers-color-scheme: dark\) \{([\s\S]*?)\n\}/);
    const dark = m![1]!;
    for (const sel of ["header", ".composer-bar", "#errbar .err-detail", ".rail-stop"]) {
      expect(dark).toContain(sel);
    }
  });
});
