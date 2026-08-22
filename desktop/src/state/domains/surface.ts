/**
 * surface 域：插件能力表面的呈现状态。
 * 全体系唯一一条真嵌套链：scene(壳) ⊃ panel(数据) ⊃ interact(交互态)。
 *
 * - 三条记录分开存（不是一条嵌套记录），恢复时按依赖检查。
 * - scene 损坏/过期 → 跳过整条链；panel 损坏 → scene 降级为"有壳无数据"；interact 损坏 → 静默跳过。
 * - interact 只存面板宿主层通用交互态；插件业务状态归插件 data.db，不进本体系。
 */
import type { KVStore, SurfaceInteract, SurfacePanel, SurfaceScene } from "../types";
import type { WebviewPayload } from "../../lib/webview-source";
import { TABLES } from "../persist-engine";
import { getDescriptor, registerDomain, validateRecord, withSavedAt, withinQuota } from "../schema-registry";

export const SCENE_KEY = "scene";
export const PANEL_KEY = "panel";
export const INTERACT_KEY = "interact";

const SCENE_PRESENTATIONS: readonly string[] = ["inline", "peek", "stage", "focus"];

function isScene(raw: unknown): SurfaceScene | null {
  if (typeof raw !== "object" || raw === null) return null;
  const s = raw as Record<string, unknown>;
  if (typeof s.panel !== "string" || !s.panel) return null;
  return {
    panel: s.panel,
    visible: s.visible === true,
    presentation: typeof s.presentation === "string" && SCENE_PRESENTATIONS.includes(s.presentation)
      ? (s.presentation as SurfaceScene["presentation"])
      : "stage",
    tab: typeof s.tab === "string" ? s.tab : "home",
  };
}

function isPanel(raw: unknown): SurfacePanel | null {
  if (typeof raw !== "object" || raw === null) return null;
  const p = raw as Record<string, unknown>;
  if (typeof p.panel !== "string" || !p.panel) return null;
  return {
    panel: p.panel,
    title: typeof p.title === "string" ? p.title : p.panel,
    schema: p.schema && typeof p.schema === "object" ? (p.schema as Record<string, unknown>) : null,
    data: p.data && typeof p.data === "object" ? (p.data as Record<string, unknown>) : {},
    webview: p.webview && typeof p.webview === "object" ? (p.webview as WebviewPayload | null) : null,
  };
}

function isInteract(raw: unknown): SurfaceInteract | null {
  if (typeof raw !== "object" || raw === null) return null;
  const i = raw as Record<string, unknown>;
  if (typeof i.panel !== "string") return null;
  return {
    panel: i.panel,
    expandedNodes: Array.isArray(i.expandedNodes) ? i.expandedNodes.filter((n): n is string => typeof n === "string") : [],
    searchQuery: typeof i.searchQuery === "string" ? i.searchQuery : "",
    activeTab: typeof i.activeTab === "string" ? i.activeTab : "",
  };
}

registerDomain("surface:scene", { domain: "surface", version: 2, ttl: 24 * 60 * 60 * 1000, validate: isScene });
registerDomain("surface:panel", { domain: "surface", version: 2, ttl: 24 * 60 * 60 * 1000, validate: isPanel });
registerDomain("surface:interact", { domain: "surface", version: 1, ttl: 60 * 60 * 1000, validate: isInteract });

export const PANEL_QUOTA = 500 * 1024; // 500KB：面板载荷单条上限，超限只保壳
export const INTERACT_QUOTA = 50 * 1024;

export interface SurfaceOptions {
  onWriteFailed?: (err: unknown) => void;
}

export interface SurfaceSnapshot {
  scene: SurfaceScene | null;
  panel: SurfacePanel | null;
  interact: SurfaceInteract | null;
}

/** scene 存活检查（TTL 由 registry 描述符决定） */
const SCENE_DESC = () => getDescriptor<SurfaceScene>("surface:scene");
const PANEL_DESC = () => getDescriptor<SurfacePanel>("surface:panel");
const INTERACT_DESC = () => getDescriptor<SurfaceInteract>("surface:interact");

export class SurfaceDomain {
  private readonly store: KVStore;
  private readonly onWriteFailed: (err: unknown) => void;
  private scene: SurfaceScene | null = null;
  private panel: SurfacePanel | null = null;
  private interact: SurfaceInteract | null = null;
  private hydrated = false;
  private readonly pendingWrites = new Set<Promise<void>>();

  constructor(store: KVStore, options: SurfaceOptions = {}) {
    this.store = store;
    this.onWriteFailed = options.onWriteFailed ?? ((err) => console.warn("[surface] persist failed", err));
  }

  async hydrate(now = Date.now()): Promise<void> {
    if (this.hydrated) return;
    const [sceneRaw, panelRaw, interactRaw] = await Promise.all([
      this.store.get<unknown>(TABLES.surface, SCENE_KEY),
      this.store.get<unknown>(TABLES.surface, PANEL_KEY),
      this.store.get<unknown>(TABLES.surface, INTERACT_KEY),
    ]);
    const scene = validateRecord(SCENE_DESC(), sceneRaw, now);
    const panel = validateRecord(PANEL_DESC(), panelRaw, now);
    const interact = validateRecord(INTERACT_DESC(), interactRaw, now);
    this.scene = scene.ok ? scene.value : null;
    this.panel = panel.ok ? panel.value : null;
    this.interact = interact.ok ? interact.value : null;
    // 关联性清理：interact 指向的面板与 panel 不一致时作废
    if (this.panel && this.interact && this.interact.panel !== this.panel.panel) this.interact = null;
    // 过期/损坏记录就地清除（防反复触发）
    const staleKeys: Array<{ table: string; key: string; value: undefined }> = [];
    if (!scene.ok && scene.reason !== "missing") staleKeys.push({ table: TABLES.surface, key: SCENE_KEY, value: undefined });
    if (!panel.ok && panel.reason !== "missing") staleKeys.push({ table: TABLES.surface, key: PANEL_KEY, value: undefined });
    if (!interact.ok && interact.reason !== "missing") staleKeys.push({ table: TABLES.surface, key: INTERACT_KEY, value: undefined });
    if (staleKeys.length > 0) {
      void this.store.batch(staleKeys).catch((err) => this.onWriteFailed(err));
    }
    this.hydrated = true;
  }

  getSnapshot(): SurfaceSnapshot {
    return { scene: this.scene, panel: this.panel, interact: this.interact };
  }

  getScene(): SurfaceScene | null {
    return this.scene;
  }

  setScene(scene: SurfaceScene): void {
    this.scene = scene;
    this.put(SCENE_KEY, withSavedAt(scene));
  }

  clearScene(): void {
    this.scene = null;
    this.panel = null;
    this.interact = null;
    void this.store.batch([
      { table: TABLES.surface, key: SCENE_KEY, value: undefined },
      { table: TABLES.surface, key: PANEL_KEY, value: undefined },
      { table: TABLES.surface, key: INTERACT_KEY, value: undefined },
    ]).catch((err) => this.onWriteFailed(err));
  }

  getPanel(): SurfacePanel | null {
    return this.panel;
  }

  /** 面板载荷：超配额只保壳（panel/title），丢弃 data/webview，防撑爆 */
  setPanel(panel: SurfacePanel): void {
    this.panel = panel;
    const preserved = withinQuota<SurfacePanel>("surface:panel", panel, PANEL_QUOTA);
    if (preserved) {
      this.put(PANEL_KEY, withSavedAt(preserved));
    } else {
      this.put(PANEL_KEY, withSavedAt({ panel: panel.panel, title: panel.title, schema: null, data: {}, webview: null }));
    }
  }

  getInteract(): SurfaceInteract | null {
    return this.interact;
  }

  /** interact 乐观可失效：TTL 1h；超配额直接不落盘 */
  setInteract(interact: SurfaceInteract): void {
    this.interact = interact;
    const preserved = withinQuota<SurfaceInteract>("surface:interact", interact, INTERACT_QUOTA);
    if (preserved) this.put(INTERACT_KEY, withSavedAt(preserved));
  }

  clearInteract(): void {
    this.interact = null;
    void this.store.delete(TABLES.surface, INTERACT_KEY).catch((err) => this.onWriteFailed(err));
  }

  async clearAll(): Promise<void> {
    try {
      await this.store.clear(TABLES.surface);
    } catch (err) {
      this.onWriteFailed(err);
    }
    this.scene = null;
    this.panel = null;
    this.interact = null;
  }

  /** 等待所有在途写入完成（测试/退出前 flush） */
  async flush(): Promise<void> {
    await Promise.allSettled([...this.pendingWrites]);
  }

  private put(key: string, value: unknown): void {
    const p = this.store.put(TABLES.surface, key, value).catch((err) => this.onWriteFailed(err));
    this.pendingWrites.add(p);
    void p.finally(() => this.pendingWrites.delete(p));
  }
}
