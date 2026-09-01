// Workspace façade 的响应式投影。Workspace 列表可跨会话共享，但 current 必须按
// conversation_id 读取；切会话时先清当前绑定，再拉该 Session 的 SessionContext。
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from "vue";
import {
  fetchProjects,
  getProjectsOnce,
  onBrainStatus,
  onProjects,
  projectAttach,
  projectCreate,
  projectDetach,
  projectSwitch,
  type ProjectAck,
  type ProjectInfo,
  type ProjectsResponse,
} from "../lib/brain";

const projects = ref<ProjectInfo[]>([]);
const currentId = ref("");
const activeSessionId = ref("");
let booted = false;

function applyView(view: ProjectsResponse): void {
  if ((view.conversation_id ?? "") !== activeSessionId.value) return;
  projects.value = view.projects ?? [];
  currentId.value = view.current ?? "";
}

/** 挂广播订阅（幂等）；请求由 setSession 发，确保带当前 conversation_id。 */
async function boot(): Promise<void> {
  if (booted) return;
  booted = true;
  try {
    await onProjects(applyView);
  } catch {
    /* 无事件通道时保留一次拉取 */
  }
  try {
    await onBrainStatus((message) => {
      if (message.status !== "up") return;
      const cid = activeSessionId.value;
      void getProjectsOnce(cid).then(applyView).catch(() => { /* 仍离线时保持现有投影 */ });
    });
  } catch {
    /* 无状态事件时由首次查询与用户 refresh 兜底 */
  }
}

/** 新建/切换回包（ok 且带视图）就地应用，不等下一轮 projects 广播。 */
function applyAck(ack: ProjectAck): void {
  if (ack.ok && ack.projects) applyView({
    current: ack.current ?? "",
    projects: ack.projects,
    conversation_id: ack.conversation_id ?? "",
  });
}

async function setSession(conversationId: string): Promise<void> {
  if (conversationId === activeSessionId.value && booted) return;
  activeSessionId.value = conversationId;
  currentId.value = ""; // 禁止新会话在回包前闪现上一会话的 Workspace
  await boot();
  // 直接等同 conversation_id 的回包；若冷启动时 Sidecar 尚未就绪，brain-up 会重拉。
  void getProjectsOnce(conversationId).then(applyView).catch(() => {
    void fetchProjects(conversationId).catch(() => { /* 离线时保持无绑定，列表沿用缓存 */ });
  });
}

export function useProject(sessionId: MaybeRefOrGetter<string | undefined> = "") {
  const scopedSessionId = computed(() => String(toValue(sessionId) ?? ""));
  watch(scopedSessionId, (id) => void setSession(id), { immediate: true });
  /** 当前项目对象（无项目为 null）。 */
  const current = computed(() => projects.value.find((p) => p.id === currentId.value) ?? null);
  return {
    current,
    /** 全部项目（sidecar 按 touched_at 倒序）。 */
    projects,
    sessionId: scopedSessionId,
    /** 新建项目；返回回包（ok=false 时带 error）。 */
    async create(name: string): Promise<ProjectAck> {
      const ack = await projectCreate(name, scopedSessionId.value);
      applyAck(ack);
      return ack;
    },
    /** 切换项目（id 或名字）；返回回包。 */
    async switchTo(idOrName: string): Promise<ProjectAck> {
      const ack = await projectSwitch(idOrName, scopedSessionId.value);
      applyAck(ack);
      return ack;
    },
    /** 挂对象进项目（id 省略=当前项目）；fire-and-forget，视图跟广播走。 */
    attach: (objType: string, ref: string, id?: string) =>
      projectAttach(objType, ref, id, scopedSessionId.value),
    /** 从项目摘除对象（id 省略=当前项目）；fire-and-forget，视图跟广播走。 */
    detach: (objType: string, ref: string, id?: string) =>
      projectDetach(objType, ref, id, scopedSessionId.value),
    /** 重新拉取（回包经 brain-projects 广播回来，无需等待）。 */
    refresh: () => getProjectsOnce(scopedSessionId.value).then(applyView),
  };
}
