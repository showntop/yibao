/** 大窗材质 finish：与深浅主题正交。
 *  主题管 light/dark，finish 管半径/釉面/阴影几何。组件只消费 --yb-* 语义令牌，
 *  换皮肤 = 改 <html data-finish>，不要改组件。后续加 finish 只扩 FINISHES + tokens.css 覆盖块。 */
export const FINISHES = [
  { id: "porcelain", label: "天青瓷", hint: "釉面高光，默认" },
  { id: "bone", label: "骨瓷", hint: "牙白暖釉，石灰石底" },
  { id: "paper", label: "宣纸", hint: "更平、更少浮起" },
  { id: "metal", label: "冷金工", hint: "更硬的边与按压" },
] as const;

export type FinishId = (typeof FINISHES)[number]["id"];
const KEY = "yibao-finish";

export function isFinishId(v: string | null): v is FinishId {
  return !!v && FINISHES.some((f) => f.id === v);
}

export function readFinish(): FinishId {
  const v = localStorage.getItem(KEY);
  return isFinishId(v) ? v : "porcelain";
}

/** mount 前调用，避免首帧闪默认瓷再切。 */
export function bootFinish(): FinishId {
  const id = readFinish();
  document.documentElement.dataset.finish = id;
  return id;
}

export function applyFinish(id: FinishId): void {
  document.documentElement.dataset.finish = id;
  localStorage.setItem(KEY, id);
}
