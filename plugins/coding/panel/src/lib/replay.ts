// 自动回放纯函数(R4 阶段二 T7;自 App T6 内联 autoReplay 抽取,行为对齐 chat.html:2869-2885):
//   pickReplayCandidate:该 cwd 时间倒序首个可回放会话——排除活体(running/waiting,
//     发送会被拒)、codex 已探测不可用;空会话顺延由调用方循环(resumeSession
//     skipIfEmpty → 0 时剔除已试候选再取)。
//   shouldYieldReplay:让位判据——attach 接管/手动接续抢占(currentSession 出现或
//     resume 在飞)即停,不再排候选。
//   replayStep:resumeSession 返回值 → 顺延决策(0=空会话顺延;-1=被归并停;>0=已回放停)。
import type { SessionRow } from "./types";

export interface ReplayCandidate { sid: string; agent: string }

export function pickReplayCandidate(
  rows: SessionRow[],
  cwd: string,
  codexAvailable: boolean | null,
): ReplayCandidate | null {
  for (const row of rows || []) {
    if (!row || !row.id || row.cwd !== cwd) continue;
    if (row.live === "running" || row.live === "waiting") continue;
    if (row.agent === "codex" && codexAvailable === false) continue; // 已探测不可用;null 按可用呈现
    return { sid: row.id, agent: row.agent };
  }
  return null;
}

export function shouldYieldReplay(hasSession: boolean, resuming: boolean): boolean {
  return hasSession || resuming;
}

export type ReplayStep = "tryNext" | "stop";

export function replayStep(resumed: number): ReplayStep {
  return resumed === 0 ? "tryNext" : "stop";
}
