// Focus 现场快照（8-31 审计 P0-6）：进专注收起两侧栏前记下先前状态，
// 退出时恢复用户进 Focus 前的真实现场，而不是简单取反或默认摊开。
// 纯函数便于单测；HomeChat 的 watch(workFocus) 只做 ref 适配。

/** 两侧栏开合现场：左复合栏 + 右侧 peek。 */
export interface RailScene {
  leftOpen: boolean;
  peekOpen: boolean;
}

/** Focus 往返状态：当前现场 + 进 Focus 前的快照（null = 不在 Focus / 无快照）。 */
export interface FocusScene {
  scene: RailScene;
  saved: RailScene | null;
}

/** Focus 切换的纯迁移：进=快照当前现场并收起两栏；出=恢复快照（无快照不动）。 */
export function focusSceneNext(cur: FocusScene, focused: boolean): FocusScene {
  if (focused) {
    // 已在 Focus 中重复进入：保留最初快照（用户进 Focus 前的真实状态），不被收起态覆盖
    if (cur.saved) return cur;
    return { scene: { leftOpen: false, peekOpen: false }, saved: { ...cur.scene } };
  }
  if (!cur.saved) return cur;
  return { scene: { ...cur.saved }, saved: null };
}
