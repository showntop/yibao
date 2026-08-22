// /命令（斜杠命令）：输入框打 / 弹出命令菜单。三种形态：
//  - template：选中后把提示词模板展开进输入框，光标落参数位，用户补参后照常发送（走 AI）；
//  - local：本地动作（截图/新建会话/打开插件…），InputBar 上抛给父窗口处理；
//  - plugin：插件动作（api.toml 里 command=true 的 direct 方法），上抛给父窗口 panelAction。
// 交互范式对齐 @ 文件引用（at-mention.ts）：解析 / 触发片段 + 浮层菜单 + ↑↓/Enter/Esc。

import type { IconName } from "../components/common/YbIcon.vue";

export interface SlashTrigger {
  /** "/" 在文本中的下标 */
  start: number;
  /** / 后的查询词（[\w-]*） */
  query: string;
}

/** 解析光标前的 / 触发片段，无则 null。要求 / 位于词首（前面是空白或开头），避免误匹配 URL 等。
 *  query 允许 Unicode 字母（中文命令词 /总结、/截 可即时过滤）。 */
export function parseSlashTrigger(text: string, caret: number): SlashTrigger | null {
  const c = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, c);
  const m = /(?:^|\s)\/([\p{L}\w-]*)/u.exec(before);
  if (!m) return null;
  return { start: m.index + m[0].indexOf("/"), query: m[1] };
}

/** 选中命令后移除触发片段（/query），返回剩余文本。 */
export function stripSlashTrigger(text: string, caret: number, start: number): string {
  const c = Math.max(0, Math.min(caret, text.length));
  const s = Math.max(0, Math.min(start, c));
  return text.slice(0, s) + text.slice(c);
}

export type SlashKind = "template" | "local" | "plugin";

export interface SlashCmd {
  id: string;
  /** 匹配词（不含 /），如 "summary" */
  keyword: string;
  label: string;
  desc: string;
  icon: IconName;
  group: "沟通" | "工具" | "插件";
  kind: SlashKind;
  /** kind=template：提示词模板，`{p}` 是参数位（展开时删掉占位、光标落此）；无 `{p}` 光标落末尾 */
  template?: string;
  /** 模板参数位的提示文案（菜单小字） */
  paramHint?: string;
  /** kind=local：动作 id（父窗口分发） */
  local?: string;
  /** kind=plugin：所属插件 id / 直调方法 */
  pluginId?: string;
  pluginMethod?: string;
}

/** 内置命令（沟通=模板、工具=本地动作）。插件命令由 pluginSlashCommands 动态生成。 */
export const BUILTIN_SLASH_COMMANDS: SlashCmd[] = [
  { id: "summary", keyword: "summary", label: "总结", desc: "把内容总结成三句话", icon: "sparkle", group: "沟通", kind: "template",
    template: "请用三句话总结这段内容：{p}", paramHint: "要总结的内容" },
  { id: "translate", keyword: "translate", label: "翻译", desc: "翻译成中文（可指定语言）", icon: "chat", group: "沟通", kind: "template",
    template: "把下面内容翻译成中文，保留原意和语气：{p}", paramHint: "要翻译的文本" },
  { id: "polish", keyword: "polish", label: "润色", desc: "让表达更自然流畅", icon: "sparkle", group: "沟通", kind: "template",
    template: "请润色下面的文本，让表达更自然流畅：{p}", paramHint: "要润色的文本" },
  { id: "shorten", keyword: "shorten", label: "简写", desc: "压缩成一句话", icon: "doc", group: "沟通", kind: "template",
    template: "请把下面的内容压缩成一句话：{p}", paramHint: "要压缩的内容" },
  { id: "snip", keyword: "snip", label: "截图", desc: "框选屏幕，截图即问", icon: "search", group: "工具", kind: "local", local: "snip" },
  { id: "plugins", keyword: "plugins", label: "打开插件", desc: "打开插件启动器", icon: "plug", group: "工具", kind: "local", local: "plugins" },
  { id: "new", keyword: "new", label: "新建会话", desc: "另起一个对话", icon: "chat", group: "工具", kind: "local", local: "new-conversation" },
  { id: "help", keyword: "help", label: "帮助", desc: "让译宝介绍能力与命令", icon: "alert", group: "工具", kind: "local", local: "help" },
];

export interface PluginInfoLite {
  id: string;
  name: string;
  commands?: { name: string; handler: string }[];
}

/** 需要用户输入的插件方法 → 走 AI 模板（选中后展开请求文本，用户补输入后发送，AI 调对应 skill 并回显结果）。
 *  未命中映射的方法保留直调（panelAction，适合真正无参、可立即执行的 action）。 */
const PLUGIN_TEMPLATES: Record<
  string,
  { label: string; desc: string; template: string; paramHint: string }
> = {
  "toolbox.json_format": {
    label: "格式化JSON",
    desc: "美化 / 压缩 / 转义 JSON",
    template: "请帮我格式化/美化下面的 JSON：\n{p}",
    paramHint: "粘贴要处理的 JSON",
  },
  "toolbox.text_diff": {
    label: "对比文本",
    desc: "两段文本逐行对比",
    template: "请帮我对比下面两段文本的差异（逐行 diff）：\n{p}",
    paramHint: "两段文本，可用 === 分隔",
  },
  "toolbox.timestamp": {
    label: "时间戳转换",
    desc: "Unix 时间戳 ↔ 可读时间",
    template: "请帮我转换这个时间戳：{p}",
    paramHint: "Unix 时间戳或日期",
  },
  "toolbox.img2pdf": {
    label: "图片转PDF",
    desc: "图片合并转 PDF",
    template: "请把这张图片转成 PDF：{p}",
    paramHint: "图片（可附件）",
  },
};

/** 插件命令：由 list_plugins 返回的 commands 动态生成。
 *  需输入的插件方法 → template 命令（走 AI，结果可见）；其余 → plugin 直调。 */
export function pluginSlashCommands(plugins: PluginInfoLite[]): SlashCmd[] {
  const out: SlashCmd[] = [];
  for (const p of plugins) {
    for (const c of p.commands ?? []) {
      const preset = PLUGIN_TEMPLATES[c.handler];
      if (preset) {
        out.push({
          id: `pl:${p.id}:${c.name}`,
          keyword: c.name,
          label: preset.label,
          desc: preset.desc,
          icon: "plug",
          group: "插件",
          kind: "template",
          template: preset.template,
          paramHint: preset.paramHint,
        });
      } else {
        out.push({
          id: `pl:${p.id}:${c.name}`,
          keyword: c.name,
          label: `${p.name} · ${c.name}`,
          desc: `调用 ${c.handler}`,
          icon: "plug",
          group: "插件",
          kind: "plugin",
          pluginId: p.id,
          pluginMethod: c.handler,
        });
      }
    }
  }
  return out;
}

/** 按查询词过滤命令（label/keyword 命中）。空查询返回全部。 */
export function filterSlashCommands(cmds: SlashCmd[], q: string): SlashCmd[] {
  const s = q.trim().toLowerCase();
  if (!s) return cmds;
  return cmds.filter((c) => c.keyword.toLowerCase().includes(s) || c.label.toLowerCase().includes(s));
}
