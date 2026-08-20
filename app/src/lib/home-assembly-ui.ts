/** 零件目录挂的视图。换摊法换组件，不在业务里写 if (preset)。 */
import type { Component } from "vue";
import HomeChatThread from "../components/HomeChatThread.vue";
import HomeChatPaper from "../components/HomeChatPaper.vue";
import SessionList from "../components/SessionList.vue";
import HomeContextPanel from "../components/HomeContextPanel.vue";
import InputBar from "../components/InputBar.vue";
import AgentBrain from "../components/AgentBrain.vue";
import HomeGlance from "../components/HomeGlance.vue";
import HomePluginGlance from "../components/HomePluginGlance.vue";

export const PART_VIEWS: Record<string, Record<string, Component>> = {
  chat: { thread: HomeChatThread, paper: HomeChatPaper },
  sessions: { list: SessionList, spine: SessionList },
  now: { inspector: HomeContextPanel, note: HomeContextPanel },
  composer: { bar: InputBar },
  mind: { map: AgentBrain, tile: AgentBrain },
  identity: { tile: AgentBrain },
  today: { tile: AgentBrain },
  need: { tile: HomeGlance },
  tasks: { tile: HomeGlance },
  remind: { tile: HomeGlance },
};

export function viewOf(partId: string, presentation: string): Component | undefined {
  if (partId.startsWith("plugin:")) return HomePluginGlance;
  const faces = PART_VIEWS[partId];
  if (!faces) return undefined;
  return faces[presentation] ?? Object.values(faces)[0];
}
