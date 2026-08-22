/** domains 子模块出口 */
export { ConversationDomain, newId } from "./conversation";
export type { ConversationOptions, MessageInput } from "./conversation";
export { SurfaceDomain, SCENE_KEY, PANEL_KEY, INTERACT_KEY, PANEL_QUOTA, INTERACT_QUOTA } from "./surface";
export type { SurfaceOptions, SurfaceSnapshot } from "./surface";
export { WindowDomain, clampBoundsToScreen } from "./window";
export type { WindowOptions } from "./window";
