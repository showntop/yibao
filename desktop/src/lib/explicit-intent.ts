// app/src/lib/explicit-intent.ts
/**
 * 窄规则：从用户消息里识别「明确要求打开某个插件」。
 *
 * 存在的理由：小窗没有大窗插件库那种点击信号，用户的明确意图只能从话里认。
 * sidecar 认不了——agent loop 视角里「用户要求所以模型调了 list」和「模型
 * 自己想调 list」是同一个形状；让模型自报 explicit 又等于让被守的人当守门人。
 *
 * 匹配条件（三者同时满足）：
 * 1. 整句以强指令动词开头（打开/展开/显示/调出/给我看）——否定、疑问、礼貌前缀
 *    都会把别的词放在动词前面，自然不命中，无需枚举 blocklist；
 * 2. 含插件名或 id；
 * 3. 不以疑问语气词结尾（吗/呢/？/?），兜底「打开日历了吗」这类。
 *
 * 刻意做窄：漏报退化成可点行、用户点一下（语义仍通顺），误报却会抢屏——
 * 成本不对称，所以宁可漏。「帮我打开日历」等礼貌前缀故意不白名单。
 */

/** 只认强指令动词。「看看」「查查」「有没有」这类语气太弱，是查询不是导航。 */
const OPEN_VERBS = ["打开", "展开", "显示", "调出", "给我看"];

/** 句末疑问语气：用户在问，不是在下打开指令。 */
const INTERROGATIVE_END = /[吗呢？?]\s*$/;

export function matchExplicitOpen(text: string, plugins: { id: string; name: string }[]): string | null {
  if (!text || !plugins.length) return null;
  const t = text.trim();
  if (INTERROGATIVE_END.test(t)) return null;
  if (!OPEN_VERBS.some((v) => t.startsWith(v))) return null;
  const hit = plugins.find((p) => (p.name && t.includes(p.name)) || (p.id && t.includes(p.id)));
  return hit ? hit.id : null;
}
