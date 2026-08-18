// webview 面板载荷分流:module 面板(url,R4 插件运行时)优先,旧 html 面板走 srcdoc。
// key 含内容版本 v(入口文件 mtime):v 变 → iframe :key 变 → 重载新代码(sidecar 不重启)。
export interface WebviewPayload {
  html?: string;
  url?: string;
  v?: number;
}

export type WebviewSource =
  | { kind: "url"; url: string; key: string }
  | { kind: "srcdoc"; html: string };

export function resolveWebviewSource(w: WebviewPayload | null | undefined): WebviewSource | null {
  if (!w) return null;
  if (typeof w.url === "string" && w.url) {
    return { kind: "url", url: w.url, key: `${w.url}@${w.v ?? 0}` };
  }
  if (typeof w.html === "string" && w.html) {
    return { kind: "srcdoc", html: w.html };
  }
  return null;
}
