// 项目实体（sidecar projects IPC）响应式单例：家态项目卡与地平线 ctx 共享的真源。
// 首次使用时发 projects 查询拉取，之后订阅 brain-projects 广播自动更新；
// 写操作（新建/切换）的回包自带最新视图，直接应用不等广播。
import { computed, ref } from "vue";
import {
  getProjectsOnce,
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
let booted = false;

function applyView(view: ProjectsResponse): void {
  projects.value = view.projects ?? [];
  currentId.value = view.current ?? "";
}

/** 首查 + 挂广播订阅（幂等；大脑离线时首查拿空态，订阅仍挂上跟后续广播走）。 */
async function boot(): Promise<void> {
  if (booted) return;
  booted = true;
  applyView(await getProjectsOnce());
  try {
    await onProjects(applyView);
  } catch {
    /* 无事件通道时保留一次拉取 */
  }
}

/** 新建/切换回包（ok 且带视图）就地应用，不等下一轮 projects 广播。 */
function applyAck(ack: ProjectAck): void {
  if (ack.ok && ack.projects) applyView({ current: ack.current ?? "", projects: ack.projects });
}

export function useProject() {
  void boot();
  /** 当前项目对象（无项目为 null）。 */
  const current = computed(() => projects.value.find((p) => p.id === currentId.value) ?? null);
  return {
    current,
    /** 全部项目（sidecar 按 touched_at 倒序）。 */
    projects,
    /** 新建项目；返回回包（ok=false 时带 error）。 */
    async create(name: string): Promise<ProjectAck> {
      const ack = await projectCreate(name);
      applyAck(ack);
      return ack;
    },
    /** 切换项目（id 或名字）；返回回包。 */
    async switchTo(idOrName: string): Promise<ProjectAck> {
      const ack = await projectSwitch(idOrName);
      applyAck(ack);
      return ack;
    },
    /** 挂对象进项目（id 省略=当前项目）；fire-and-forget，视图跟广播走。 */
    attach: (objType: string, ref: string, id?: string) => projectAttach(objType, ref, id),
    /** 从项目摘除对象（id 省略=当前项目）；fire-and-forget，视图跟广播走。 */
    detach: (objType: string, ref: string, id?: string) => projectDetach(objType, ref, id),
    /** 重新拉取（回包经 brain-projects 广播回来，无需等待）。 */
    refresh: () => getProjectsOnce().then(applyView),
  };
}
