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
import HomeWhen from "../views/HomeWhen.vue";
import HomeLine from "../views/HomeLine.vue";
import HomeJot from "../views/HomeJot.vue";
import HomeBench from "../views/HomeBench.vue";
import HomeDayTitle from "../views/HomeDayTitle.vue";
import HomeTodayPanel from "../views/HomeTodayPanel.vue";
import HomeRemindCard from "../views/HomeRemindCard.vue";
import HomeMaterialsStat from "../views/HomeMaterialsStat.vue";
import HomeFlashesStat from "../views/HomeFlashesStat.vue";
import HomePluginGlance from "../views/plugins/HomePluginGlance.vue";

export const PART_VIEWS: Record<string, Record<string, Component>> = {
  chat: { thread: HomeChatThread, paper: HomeChatPaper, talk: HomeChatTalk },
  dayTitle: { title: HomeDayTitle },
  today: { tile: AgentBrain, panel: HomeTodayPanel },
  sessions: { list: SessionList, spine: SessionList, cards: SessionList },
  now: { inspector: HomeContextPanel, note: HomeContextPanel },
  composer: { bar: InputBar },
  mind: { map: AgentBrain, tile: AgentBrain },
  identity: { tile: AgentBrain, seat: AgentBrain },
  when: { tile: HomeWhen },
  line: { tile: HomeLine },
  jot: { tile: HomeJot },
  bench: { tile: HomeBench },
  materials: { stat: HomeMaterialsStat },
  flashes: { stat: HomeFlashesStat },
  need: { tile: HomeGlance },
  remind: { tile: HomeGlance, card: HomeRemindCard },
  tasks: { tile: HomeGlance },
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
