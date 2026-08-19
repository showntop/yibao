// window.yibao 桥的 TS 封装。桥本身由宿主注入(srcdoc 内联或 yibao-plugin:// 协议层),
// 这里只做类型化与空值守卫;无桥时 hasBridge=false(设计预览模式),invoke 一律 reject。
declare global {
  interface Window {
    yibao?: {
      invoke(method: string, params?: Record<string, unknown>): Promise<unknown>;
      onInit(cb: (data: unknown, msg?: Record<string, unknown>) => void): void;
      onMessage?(cb: (msg: Record<string, unknown>) => void): void;
      emitEvent(name: string, payload: unknown): void;
    };
    YIBAO_BRIDGE_VERSION?: number;
  }
}

export const hasBridge = !!(
  typeof window !== "undefined" &&
  typeof window.yibao?.invoke === "function" &&
  typeof window.yibao?.onInit === "function"
);

// 桥 resolve 的是 result.data 本体——直读返回,不再有 .data 层
export async function invoke<T = Record<string, unknown>>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (!hasBridge) return Promise.reject(new Error("桥不可用"));
  return window.yibao!.invoke(method, params) as Promise<T>;
}

export function onInit(cb: (data: unknown, msg?: Record<string, unknown>) => void): void {
  if (hasBridge) window.yibao!.onInit(cb);
}

export function onHostMessage(cb: (msg: Record<string, unknown>) => void): void {
  if (hasBridge && window.yibao!.onMessage) window.yibao!.onMessage(cb);
}

export function emitPanelEvent(name: string, payload: unknown): void {
  if (hasBridge) window.yibao!.emitEvent(name, payload);
}
