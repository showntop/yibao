import { parsePairUrl, testConn as realTestConn, type ConnConfig } from "./api/connection";

export interface DeeplinkIO {
  save: (c: ConnConfig) => Promise<void>;
  push: (to: string) => void;
  testConn?: typeof realTestConn; // 可注入（测试）；默认真实现
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
