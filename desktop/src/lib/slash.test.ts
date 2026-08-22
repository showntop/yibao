import { describe, expect, it } from "vitest";
import {
  parseSlashTrigger,
  stripSlashTrigger,
  filterSlashCommands,
  BUILTIN_SLASH_COMMANDS,
  pluginSlashCommands,
} from "./slash";

describe("parseSlashTrigger", () => {
  it("解析光标前的 / 命令词", () => {
    expect(parseSlashTrigger("/sum", 4)).toEqual({ start: 0, query: "sum" });
    expect(parseSlashTrigger("说一句 /help", 11)).toEqual({ start: 4, query: "help" });
  });

  it("无触发返回 null（普通文本 / URL / 空串）", () => {
    expect(parseSlashTrigger("你好", 2)).toBeNull();
    expect(parseSlashTrigger("https://x.com", 12)).toBeNull(); // / 前不是词首
    expect(parseSlashTrigger("", 0)).toBeNull();
  });

  it("中文命令词可触发并即时过滤", () => {
    expect(parseSlashTrigger("/截图", 3)).toEqual({ start: 0, query: "截图" });
    expect(parseSlashTrigger("/截", 2)).toEqual({ start: 0, query: "截" });
  });
});

describe("stripSlashTrigger", () => {
  it("移除触发片段", () => {
    expect(stripSlashTrigger("/sum 内容", 4, 0)).toBe(" 内容");
  });
});

describe("filterSlashCommands", () => {
  it("按 label 命中中文", () => {
    const r = filterSlashCommands(BUILTIN_SLASH_COMMANDS, "总结");
    expect(r.length).toBe(1);
    expect(r[0].keyword).toBe("summary");
  });

  it("按 keyword 命中英文", () => {
    const r = filterSlashCommands(BUILTIN_SLASH_COMMANDS, "snip");
    expect(r.length).toBe(1);
    expect(r[0].local).toBe("snip");
  });

  it("空查询返回全部", () => {
    expect(filterSlashCommands(BUILTIN_SLASH_COMMANDS, "").length).toBe(BUILTIN_SLASH_COMMANDS.length);
  });
});

describe("pluginSlashCommands", () => {
  it("需输入的工具方法生成 template 命令（走 AI 回显结果）", () => {
    const cmds = pluginSlashCommands([
      { id: "toolbox", name: "工具箱", commands: [{ name: "json_format", handler: "toolbox.json_format" }] },
    ]);
    expect(cmds.length).toBe(1);
    expect(cmds[0].kind).toBe("template");
    expect(cmds[0].template).toContain("JSON");
    expect(cmds[0].paramHint).toBeTruthy();
  });

  it("无预设模板的插件方法保留直调", () => {
    const cmds = pluginSlashCommands([
      { id: "z", name: "Z", commands: [{ name: "ping", handler: "z.ping" }] },
    ]);
    expect(cmds[0].kind).toBe("plugin");
    expect(cmds[0].pluginId).toBe("z");
    expect(cmds[0].pluginMethod).toBe("z.ping");
  });

  it("无 commands 的插件不生成命令", () => {
    expect(pluginSlashCommands([{ id: "coding", name: "编码" }])).toEqual([]);
  });
});
