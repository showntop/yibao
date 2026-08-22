/** 直调插件方法并等 pa_<rid> 回包。失败或超时回 null。 */

import { onBrainEvent, panelAction } from "../brain";

export async function runPanelAction(
  method: string,
  params: Record<string, unknown> = {},
  surface?: string,
  timeoutMs = 12_000,
): Promise<Record<string, unknown> | null> {
  const rid = Date.now() % 2 ** 31;
  let unlisten: (() => void) | undefined;
  try {
    return await new Promise((resolve) => {
      const finish = (value: Record<string, unknown> | null) => {
        clearTimeout(timer);
        resolve(value);
      };
      const timer = setTimeout(() => finish(null), timeoutMs);
      void onBrainEvent((event) => {
        if ((event.action?.id ?? "") !== `pa_${rid}`) return;
        if (event.kind === "action_result" && event.result?.success) {
          finish(event.result.data ?? {});
          return;
        }
        finish(null);
      }).then((stop) => {
        unlisten = stop;
        void panelAction(method, params, rid, surface).catch(() => finish(null));
      });
    });
  } finally {
    unlisten?.();
  }
}
