/**
 * window 域：多窗口的布局与协调状态（现体系缺失的一块）。
 *
 * - 只存引用：focusedConversationId / focusedPanelId 指向其他域的数据，本体不拷贝。
 * - bounds 恢复要过显示器 sanity check：保存时所在显示器已断开 → 回退主屏可见区域。
 * - 每个窗口各自上报自己的 WindowState，恢复时各自认领，互不覆盖。
 */
import type { KVStore, WindowId, WindowState } from "../types";
import { TABLES } from "../persist-engine";
import { registerDomain, validateRecord } from "../schema-registry";

function isWindowState(raw: unknown): WindowState | null {
  if (typeof raw !== "object" || raw === null) return null;
  const w = raw as Record<string, unknown>;
  if (typeof w.windowId !== "string") return null;
  return {
    windowId: w.windowId as WindowId,
    bounds: sanitizeBounds(w.bounds),
    visible: w.visible === true,
    alwaysOnTop: w.alwaysOnTop === true,
    focusedConversationId: typeof w.focusedConversationId === "string" ? w.focusedConversationId : null,
    focusedPanelId: typeof w.focusedPanelId === "string" ? w.focusedPanelId : null,
  };
}

function sanitizeBounds(raw: unknown): WindowState["bounds"] {
  if (typeof raw !== "object" || raw === null) return null;
  const b = raw as Record<string, unknown>;
  const x = Number(b.x);
  const y = Number(b.y);
  const width = Number(b.width);
  const height = Number(b.height);
  if (![x, y, width, height].every((n) => Number.isFinite(n) && n >= 0)) return null;
  return { x, y, width, height };
}

registerDomain("window:state", { domain: "window", version: 1, ttl: null, validate: isWindowState });

export interface WindowOptions {
  onWriteFailed?: (err: unknown) => void;
  /** 当前可用的屏幕边界（sanity check 用；缺省无约束） */
  screen?: () => { x: number; y: number; width: number; height: number };
}

/** 显示器 sanity check：保存的 bounds 不在任何可见区域 → 返回 null（回退默认布局） */
export function clampBoundsToScreen(
  bounds: WindowState["bounds"],
  screen: { x: number; y: number; width: number; height: number },
): WindowState["bounds"] {
  if (!bounds) return null;
  const visibleX = bounds.x >= screen.x && bounds.x < screen.x + screen.width;
  const visibleY = bounds.y >= screen.y && bounds.y < screen.y + screen.height;
  if (!visibleX || !visibleY) return null;
  return bounds;
}

export class WindowDomain {
  private readonly store: KVStore;
  private readonly onWriteFailed: (err: unknown) => void;
  private readonly screen: () => { x: number; y: number; width: number; height: number };
  private readonly states = new Map<WindowId, WindowState>();
  private hydrated = false;
  private readonly pendingWrites = new Set<Promise<void>>();

  constructor(store: KVStore, options: WindowOptions = {}) {
    this.store = store;
    this.onWriteFailed = options.onWriteFailed ?? ((err) => console.warn("[window] persist failed", err));
    this.screen = options.screen ?? (() => ({ x: 0, y: 0, width: 1920, height: 1080 }));
  }

  async hydrate(now = Date.now()): Promise<void> {
    if (this.hydrated) return;
    const entries = await this.store.entries<unknown>(TABLES.windows);
    for (const { value } of entries) {
      const result = validateRecord({ domain: "window", version: 1, ttl: null, validate: isWindowState }, value, now);
      if (!result.ok) continue;
      const state = result.value;
      const screen = this.screen();
      const clamped = clampBoundsToScreen(state.bounds, screen);
      if (clamped !== state.bounds) {
        state.bounds = clamped;
        if (clamped !== null) this.persist(state);
      }
      this.states.set(state.windowId, state);
    }
    this.hydrated = true;
  }

  getState(id: WindowId): WindowState | null {
    return this.states.get(id) ?? null;
  }

  getAllStates(): WindowState[] {
    return [...this.states.values()];
  }

  updateState(id: WindowId, partial: Partial<Omit<WindowState, "windowId">>): WindowState {
    const current = this.states.get(id) ?? {
      windowId: id,
      bounds: null,
      visible: false,
      alwaysOnTop: false,
      focusedConversationId: null,
      focusedPanelId: null,
    };
    const next: WindowState = { ...current, ...partial, windowId: id };
    this.states.set(id, next);
    this.persist(next);
    return next;
  }

  setFocusedConversation(id: WindowId, conversationId: string | null): void {
    this.updateState(id, { focusedConversationId: conversationId });
  }

  setFocusedPanel(id: WindowId, panel: string | null): void {
    this.updateState(id, { focusedPanelId: panel });
  }

  setBounds(id: WindowId, bounds: WindowState["bounds"]): void {
    this.updateState(id, { bounds });
  }

  setVisible(id: WindowId, visible: boolean): void {
    this.updateState(id, { visible });
  }

  async clearAll(): Promise<void> {
    try {
      await this.store.clear(TABLES.windows);
    } catch (err) {
      this.onWriteFailed(err);
    }
    this.states.clear();
  }

  /** 等待所有在途写入完成（测试/退出前 flush） */
  async flush(): Promise<void> {
    await Promise.allSettled([...this.pendingWrites]);
  }

  private persist(state: WindowState): void {
    const p = this.store.put(TABLES.windows, state.windowId, state).catch((err) => this.onWriteFailed(err));
    this.pendingWrites.add(p);
    void p.finally(() => this.pendingWrites.delete(p));
  }
}
