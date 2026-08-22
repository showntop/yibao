/**
 * RestoreOrchestrator：启动恢复编排。
 *
 * 拓扑：
 *   Phase 0 打开 DB（引擎实例化即触发；失败 → 全内存降级，不阻塞启动）
 *   Phase 1 并行恢复 conversation │ window │ surface（三域独立容错）
 *   Phase 2 链式收尾：surface 域内 scene → panel → interact 的关联清理在 hydrate 内完成
 *
 * 幂等硬约束：
 *   - 恢复只重建 UI 呈现，不重放工具/模型动作。
 *   - 恢复数据携带 restored 标记，组件 hydrate 时抑制 watch 持久化（防"启动即写"回环）。
 *   - 恢复可安全重入：执行两次结果与一次相同。
 */
import type { SurfacePanel, SurfaceScene } from "./types";
import type { SessionStore } from "./session-store";

export interface RestoreReport {
  engineReady: boolean;
  /** 各域是否成功 hydrate */
  ok: Record<string, boolean>;
  /** surface 域恢复出的场景（供 Home 决定是否展开工作面） */
  scene: SurfaceScene | null;
  /** surface 域恢复出的面板载荷（供 HomePlugins 回填） */
  panel: SurfacePanel | null;
  /** 活动会话 id（conversation 域） */
  activeConversationId: string | null;
}

export async function orchestrateRestore(store: SessionStore): Promise<RestoreReport> {
  const { ok, engineReady } = await store.restore();
  const scene = store.surface.getScene();
  const panel = store.surface.getPanel();
  return {
    engineReady,
    ok,
    scene,
    panel,
    activeConversationId: store.conversation.getActiveConversationId(),
  };
}
