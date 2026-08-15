import { parsePairUrl } from "./api/connection";

export interface DeeplinkIO {
  save: (c: { host: string; token: string }) => Promise<void>;
  push: (to: string) => void;
}

/** 解析 yibao://pair 深链：合法则保存连接配置并跳转 /chat，返回是否处理 */
export async function handlePairUrl(url: string, io: DeeplinkIO): Promise<boolean> {
  const c = parsePairUrl(url);
  if (!c) return false;
  await io.save(c);
  io.push("/chat");
  return true;
}
