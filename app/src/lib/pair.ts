/** 手机浏览器配对 URL（预填 host/token；原生 App 上线后此处换 yibao://pair 深链）。 */
export function buildPairUrl(lanIp: string, port: number, token: string): string {
  if (!lanIp) return "";
  const host = encodeURIComponent(`http://${lanIp}:${port}`);
  return `http://${lanIp}:5173/?host=${host}&token=${encodeURIComponent(token)}#/pairing`;
}
