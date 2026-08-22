/** 零件目录挂的视图。换摊法换组件，不在业务里写 if (preset)。 */
import type { Component } from "vue";
import HomeChatThread from "../views/chat/HomeChatThread.vue";
import HomeChatPaper from "../views/chat/HomeChatPaper.vue";
import HomeChatTalk from "../views/chat/HomeChatTalk.vue";
import SessionList from "../views/chat/SessionList.vue";
import HomeContextPanel from "../views/HomeContextPanel.vue";
import InputBar from "../components/common/InputBar.vue";
import AgentBrain from "../views/brain/AgentBrain.vue";
import HomeGlance from "../views/HomeGlance.vue";
import HomeLife from "../views/HomeLife.vue";
import HomePluginGlance from "../views/plugins/HomePluginGlance.vue";

export const PART_VIEWS: Record<string, Record<string, Component>> = {
  chat: { thread: HomeChatThread, paper: HomeChatPaper, talk: HomeChatTalk },
  sessions: { list: SessionList, spine: SessionList, cards: SessionList },
  now: { inspector: HomeContextPanel, note: HomeContextPanel },
  composer: { bar: InputBar },
  mind: { map: AgentBrain, tile: AgentBrain },
  identity: { tile: AgentBrain, seat: AgentBrain },
  today: { tile: AgentBrain },
  need: { tile: HomeGlance },
  tasks: { tile: HomeGlance },
  remind: { tile: HomeGlance },
  spark: { tile: HomeLife },
  glimpse: { tile: HomeLife },
  catch: { tile: HomeLife },
  scratch: { tile: HomeLife },
};

export function viewOf(partId: string, presentation: string): Component | undefined {
  if (partId.startsWith("plugin:")) return HomePluginGlance;
  const faces = PART_VIEWS[partId];
  if (!faces) return undefined;
  return faces[presentation] ?? Object.values(faces)[0];
}
