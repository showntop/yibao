import { parsePairUrl, testConn as realTestConn, type ConnConfig } from "./api/connection";

export interface DeeplinkIO {
  save: (c: ConnConfig) => Promise<void>;
  push: (to: string) => void;
  testConn?: typeof realTestConn; // 可注入（测试）；默认真实现
}

/** 深链路径：pair=配对（带连接参数）；approvals=审批页（P4 推送深链的坑位） */
export type DeepPath =
  | { kind: "pair"; host: string; token: string }
  | { kind: "approvals" };

/** 路径分流：pair 复用 parsePairUrl 的参数校验；approvals 判 yibao://approvals；其余 null */
export function parseDeepPath(u: string): DeepPath | null {
  const c = parsePairUrl(u);
  if (c) return { kind: "pair", ...c };
  try {
    const url = new URL(u);
    if (url.protocol === "yibao:" && url.host === "approvals") return { kind: "approvals" };
  } catch {
    return null;
  }
  return null;
}

/** 解析 yibao://pair 深链：先验证连通，通过才保存配置并跳转 /chat，返回是否处理 */
export async function handlePairUrl(url: string, io: DeeplinkIO): Promise<boolean> {
  const c = parsePairUrl(url);
  if (!c) return false;
  const check = await (io.testConn ?? realTestConn)(c);
  if (!check.ok) return false; // 连不上：不落盘不跳转，留在原页（用户可手动配对）
  await io.save(c);
  io.push("/chat");
  return true;
}

/** 深链总入口（App.vue appUrlOpen 调）：pair 走配对校验流程；approvals 直接进审批页 */
export async function handleDeepUrl(url: string, io: DeeplinkIO): Promise<boolean> {
  const p = parseDeepPath(url);
  if (!p) return false;
  if (p.kind === "approvals") {
    io.push("/approvals");
    return true;
  }
  return handlePairUrl(url, io);
}
