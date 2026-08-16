/** 手机浏览器配对 URL（预填 host/token；原生 App 上线后此处换 yibao://pair 深链）。
 * dev 面走 vite-plugin-mkcert 出的 https（安全上下文才有 clipboard/crypto.randomUUID）；
 * host 参数是 sidecar 地址（aiohttp 无 TLS），保持 http。 */
export function buildPairUrl(lanIp: string, port: number, token: string): string {
  if (!lanIp) return "";
  const host = encodeURIComponent(`http://${lanIp}:${port}`);
  return `https://${lanIp}:5173/?host=${host}&token=${encodeURIComponent(token)}#/pairing`;
}
