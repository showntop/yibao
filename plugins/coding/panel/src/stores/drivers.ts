// 引擎驱动 store(R4 阶段二 T6):coding.drivers 探测 + 新会话引擎选择(curAgent)。
// 行为对齐 chat.html:1750-1756(normAgent/agentLabel)、:1838-1850(probeDrivers)、
// :2852-2862(refreshCwdState 的默认引擎记忆段)——
//   codexAvailable: null=未探测或探测失败(按可用呈现,不影响 CC 路径)/false/true;
//   curAgent 落 codex 但已探测不可用 → 强制回 claude-code(灰显禁用态下不给选);
//   默认引擎记忆 = 当前 cwd 最近会话的 agent(applyCwdDefault,codex 不可用强制 CC)。
// switchAgent(有会话跨引擎待定)属会话选择态,留在 App(随 currentSession 变化清空)。
import { reactive } from "vue";
import type { DriverInfo } from "../lib/types";

export function normAgent(a: string): string { return a === "codex" ? "codex" : "claude-code"; } // "cc" 等历史值一律归 CC
export function agentLabel(a: string): string { return a === "codex" ? "codex" : "CC"; }

export interface DriversState {
  codexAvailable: boolean | null;
  curAgent: string; // 新会话引擎选择(无会话态 picker 的真值;缺省 CC)
}

export interface DriversDeps {
  invoke: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
}

export function createDriversStore(deps: DriversDeps) {
  const state = reactive<DriversState>({ codexAvailable: null, curAgent: "claude-code" });

  function enforceFallback() {
    if (state.codexAvailable === false && state.curAgent === "codex") state.curAgent = "claude-code";
  }

  /** L0 quiet 探测;桥 resolve result.data 本体 → r.drivers(与 coding.list 的 r.sessions 同形态)。
      失败保持 null 按可用呈现;恒 resolve,调用方无需再兜。 */
  async function probe(): Promise<void> {
    try {
      const r = (await deps.invoke("coding.drivers", {})) as { drivers?: DriverInfo[] };
      const list = (r && r.drivers) || [];
      let found = false;
      for (const d of list) {
        if (d && d.id === "codex") { state.codexAvailable = !!d.available; found = true; break; }
      }
      if (!found) state.codexAvailable = false; // 应答里查无 codex 项同样视为不可用
      enforceFallback();
    } catch { /* 探测失败静默:保持 null,chip 按可用呈现 */ }
  }

  /** 无会话态 picker 选中(仅归一化;codex 不可用在 picker 层已禁选,不在此重复拦截) */
  function setCurAgent(a: string) { state.curAgent = normAgent(a); }

  /** chip 默认引擎 = 该 cwd 最近会话的 agent(list 按时间倒序首个命中行,调用方负责找);
      codex 记忆在已探测不可用时强制回 CC;未探测(null)按可用呈现,待 probe 结论强制回退。 */
  function applyCwdDefault(agent: string) {
    const a = normAgent(agent);
    state.curAgent = a === "codex" && state.codexAvailable === false ? "claude-code" : a;
  }

  return { state, probe, setCurAgent, applyCwdDefault };
}
