// TODO(状态恢复体系化，待设计)：当前快照仅覆盖「面板载荷 + 场景布局」链路。
// 目标：扩展为分层 SessionState 快照（yb-session-v1），覆盖对话过程状态——
//   scene(布局壳 panel/presentation/object) / panel(面板数据，现状)
//   / chat(输入草稿+滚动+筛选) / interact(面板内交互态，乐观可失效)
// 恢复按依赖拓扑逐层执行、每层独立容错（损坏/过期只跳该层，不阻塞其余）；
// 硬约束：恢复只重建 UI 呈现，不重放工具/模型动作（幂等）。
// 优先级参考：对话当前对象、输入草稿、进行中活动 > 滚动/筛选 > 面板内部状态（归插件自持）。

/**
 * 能力工作面面板载荷快照：重启后恢复「正在和 xxx 插件协作中」的工作面。
 *
 * 背景：Tauri 侧的 `last_panel` 是内存态（收到 panel 事件才写、重启即失），
 * 仅靠 `get_current_panel` 无法在应用重启后恢复面板数据。这里在 localStorage
 * 持久化一份「面板载荷 + 布局」快照，重启后由 HomePlugins.pullCache 回退加载。
 *
 * 容错设计：
 *  - 版本化（version=2），解析失败/结构不对 → load 返回 null（不恢复、不抛错）
 *  - savedAt 超 24h 视为过期 → 不恢复数据（降级为列表页），防旧数据误导
 *  - webview HTML 大于阈值时丢弃数据载荷（localStorage 5MB 上限保护）
 *  - 写入失败静默（快照只是增强，不是关键路径）
 */
export interface CapabilitySnapshotPayload {
  panel: string;
  title?: string;
  schema?: Record<string, unknown> | null;
  data?: Record<string, unknown>;
  webview?: { html?: string } | null;
}

export interface CapabilitySnapshot {
  version: 2;
  savedAt: number;
  panel: string;
  title: string;
  schema: Record<string, unknown> | null;
  data: Record<string, unknown>;
  webview: { html?: string } | null;
}

const KEY = "yb-capability-panel-v2";
const TTL_MS = 24 * 60 * 60 * 1000; // 24h
const MAX_DATA = 400 * 1024; // 数据载荷 400KB 上限（webview HTML 偏大，防止撑爆 localStorage）
const MAX_WEBVIEW = 200 * 1024;

function payloadToSnapshot(p: CapabilitySnapshotPayload, now = Date.now()): CapabilitySnapshot {
  const hasData = p.data && Object.keys(p.data).length > 0;
  const html = p.webview?.html;
  const bigHtml = typeof html === "string" && html.length > MAX_WEBVIEW;
  return {
    version: 2,
    savedAt: now,
    panel: p.panel,
    title: p.title ?? p.panel,
    // 大 payload 只保面板骨架，不存数据（避免撑爆 localStorage）
    schema: p.schema && JSON.stringify(p.schema).length <= MAX_DATA ? p.schema : null,
    data: hasData && !bigHtml && JSON.stringify(p.data).length <= MAX_DATA ? (p.data as Record<string, unknown>) : {},
    webview: bigHtml ? { html: html.slice(0, MAX_WEBVIEW) } : (p.webview ?? null),
  };
}

export function saveCapabilitySnapshot(p: CapabilitySnapshotPayload): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(payloadToSnapshot(p)));
  } catch {
    // 快照写入失败只降级为本次会话状态；不阻塞面板主流程
  }
}

export function loadCapabilitySnapshot(): CapabilitySnapshot | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const snap = JSON.parse(raw) as Partial<CapabilitySnapshot>;
    // 版本/结构校验：不满足即视为损坏，清掉防止反复触发
    if (snap?.version !== 2 || typeof snap.panel !== "string" || !snap.panel) {
      localStorage.removeItem(KEY);
      return null;
    }
    if (Date.now() - snap.savedAt > TTL_MS) {
      localStorage.removeItem(KEY);
      return null;
    }
    return {
      version: 2,
      savedAt: snap.savedAt,
      panel: snap.panel,
      title: typeof snap.title === "string" ? snap.title : snap.panel,
      schema: snap.schema && typeof snap.schema === "object" ? snap.schema : null,
      data: snap.data && typeof snap.data === "object" ? snap.data : {},
      webview: snap.webview && typeof snap.webview === "object" ? snap.webview : null,
    };
  } catch {
    try { localStorage.removeItem(KEY); } catch { /* 静默 */ }
    return null;
  }
}

export function clearCapabilitySnapshot(): void {
  try { localStorage.removeItem(KEY); } catch { /* 静默 */ }
}

/** 恢复后把快照回填给 Rust 侧 last_panel：让面板窗/宠物窗也拿到同一份面板数据（多窗一致）。 */
export async function restoreRustPanelCache(snap: CapabilitySnapshot): Promise<void> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("remember_panel", {
      payload: {
        panel: snap.panel,
        title: snap.title,
        schema: snap.schema,
        data: snap.data,
        webview: snap.webview,
      },
    });
  } catch { /* 非 Tauri 环境（QA/dev）静默 */ }
}
