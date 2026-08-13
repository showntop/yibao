// app/src/lib/explicit-intent.ts
/**
 * 窄规则：从用户消息里识别「明确要求打开某个插件」。
 *
 * 存在的理由：小窗没有大窗插件库那种点击信号，用户的明确意图只能从话里认。
 * sidecar 认不了——agent loop 视角里「用户要求所以模型调了 list」和「模型
 * 自己想调 list」是同一个形状；让模型自报 explicit 又等于让被守的人当守门人。
 *
 * 刻意做窄：动词与宾语都取自有限集合。漏报只是退化成可点行、用户点一下（语义
 * 仍通顺），误报却会抢屏——成本不对称，所以宁可漏。
 */

/** 只认强指令动词。「看看」「查查」「有没有」这类语气太弱，是查询不是导航。 */
const OPEN_VERBS = ["打开", "展开", "显示", "调出", "给我看"];

export function matchExplicitOpen(text: string, plugins: { id: string; name: string }[]): string | null {
  if (!text || !plugins.length) return null;
  if (!OPEN_VERBS.some((v) => text.includes(v))) return null;
  const hit = plugins.find((p) => (p.name && text.includes(p.name)) || (p.id && text.includes(p.id)));
  return hit ? hit.id : null;
}
