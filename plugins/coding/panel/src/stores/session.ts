// 会话 store:事件→渲染模型归约器 + 发送状态机。单工位对外暴露一个「当前会话」视图,
// 内部按 sid 分槽(stage 3 多工位直接复用同一归约器多实例)。
// 行为对齐 chat.html:气泡切断规则、工具卡配对(lastToolCard)、兜底用户气泡原地升级、
// pendingTurnEnded 秒败竞态、resumeSession 的 discarded 解锁、takeover 队列泄放。
import { reactive } from "vue";
import type { CodingEvent, HistoryMessage, PanelData, Usage } from "../lib/types";

export interface ToolResultInfo { text: string; isError: boolean }

export type RenderItem =
  | { type: "user"; text: string; uuid?: string }
  | { type: "assistant"; raw: string; thinking: string[]; done: boolean }
  | { type: "tool"; tool: string; input: Record<string, unknown>; results: ToolResultInfo[]; hasError: boolean }
  | { type: "fileedit"; tool: string; path: string | null; old: string | null; new: string | null }
  | { type: "perm"; rid: string; tool: string; input: Record<string, unknown>; state: "waiting" | "allowed" | "denied" }
  | { type: "marker"; text: string; err: boolean }
  | { type: "error"; text: string };

export type SessionEnded = "done" | "stopped" | "error" | null;
export type ReportState = "idle" | "sending" | "streaming" | "waiting";

export interface SessionState {
  items: RenderItem[];
  currentSession: string | null;
  curSessAgent: string;
  sending: boolean;
  streaming: boolean;
  waiting: boolean; // 有待批 permission_request
  ended: SessionEnded;
  usage: { tok: number; cost: number; hasCost: boolean };
  error: string | null; // errbar 文本
}

export interface SessionDeps {
  invoke: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
  report: (st: ReportState, hasSession: boolean) => void; // takeover-state 上报(仅 takeover 态真正发)
  setTimer: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer: (t: ReturnType<typeof setTimeout>) => void;
  userEchoFallbackMs: number; // 生产 1500,测试可 0/手动
}

export function createSessionStore(deps: SessionDeps) {
  const state = reactive<SessionState>({
    items: [], currentSession: null, curSessAgent: "claude-code",
    sending: false, streaming: false, waiting: false, ended: null,
    usage: { tok: 0, cost: 0, hasCost: false }, error: null,
  });

  // —— 内部簿记(不进响应式状态)——
  const discardedSessions = new Set<string>();
  let takeoverQueue: Array<{ text: string; refs: string[] }> = [];
  let pendingTurnEnded = false; // 秒败竞态:终态先于 invoke 返回
  let pendingUserEcho: ReturnType<typeof setTimeout> | null = null;
  let fallbackUserIndex = -1;   // 兜底气泡在 items 里的下标(-1 无)

  const curAssistant = (): Extract<RenderItem, { type: "assistant" }> | null => {
    const last = state.items[state.items.length - 1];
    return last && last.type === "assistant" && !last.done ? last : null;
  };
  const lastToolCard = (): Extract<RenderItem, { type: "tool" }> | null => {
    for (let i = state.items.length - 1; i >= 0; i--) {
      const it = state.items[i];
      if (it.type === "tool") return it;
      if (it.type === "assistant" || it.type === "user" || it.type === "fileedit") return null; // 切断规则
    }
    return null;
  };
  function finalizeAssistant() {
    const b = curAssistant();
    if (b) b.done = true;
  }

  function addUsage(u?: Usage) {
    if (!u) return;
    state.usage.tok += (u.input_tokens ?? 0) + (u.output_tokens ?? 0);
    if (typeof u.cost_usd === "number" && Number.isFinite(u.cost_usd)) {
      state.usage.cost += u.cost_usd;
      state.usage.hasCost = true;
    }
  }

  function onSessionEnded(reason: Exclude<SessionEnded, null>) {
    if (state.sending) pendingTurnEnded = true; // 秒败竞态:终态先于 invoke 返回
    finalizeAssistant();
    state.ended = reason;
    state.streaming = false;
    state.waiting = false;
    deps.report("idle", !!state.currentSession);
    if (reason === "stopped") takeoverQueue = []; // 中断即放弃排队意图
    else drainTakeoverQueue();
  }

  /** 事件归约:sid 过滤已由调用方(handleData)做完。 */
  function applyEvent(ev: CodingEvent) {
    switch (ev.kind) {
      case "user_msg": {
        state.error = null;
        if (pendingUserEcho) { deps.clearTimer(pendingUserEcho); pendingUserEcho = null; }
        finalizeAssistant();
        // 兜底气泡原地升级(文本匹配才认,防双份)
        if (fallbackUserIndex >= 0) {
          const it = state.items[fallbackUserIndex];
          if (it && it.type === "user" && it.text === ev.text && !it.uuid) {
            it.uuid = ev.uuid;
            fallbackUserIndex = -1;
            return;
          }
          fallbackUserIndex = -1;
        }
        state.items.push({ type: "user", text: ev.text, uuid: ev.uuid });
        return;
      }
      case "text_delta": {
        let b = curAssistant();
        if (!b) { state.items.push({ type: "assistant", raw: "", thinking: [], done: false }); b = curAssistant(); }
        b!.raw += ev.text;
        return;
      }
      case "thinking": {
        let b = curAssistant();
        if (!b) { state.items.push({ type: "assistant", raw: "", thinking: [], done: false }); b = curAssistant(); }
        b!.thinking.push(ev.text);
        return;
      }
      case "tool_use":
        finalizeAssistant();
        state.items.push({ type: "tool", tool: ev.tool, input: ev.input ?? {}, results: [], hasError: false });
        return;
      case "file_edit":
        finalizeAssistant();
        // 切断 lastToolCard:Edit/Write 的 tool_result 不得错挂前卡(由 lastToolCard() 的切断规则兑现)
        state.items.push({ type: "fileedit", tool: ev.tool, path: ev.path ?? null, old: ev.old ?? null, new: ev.new ?? null });
        return;
      case "tool_result": {
        const card = lastToolCard();
        if (card) {
          card.results.push({ text: ev.text, isError: ev.is_error });
          if (ev.is_error) card.hasError = true;
        } else if (ev.is_error) {
          state.error = ev.text; // 无卡错误结果退化进 errbar
        } else {
          state.items.push({ type: "marker", text: ev.text, err: false });
        }
        return;
      }
      case "permission_request":
        finalizeAssistant();
        state.items.push({ type: "perm", rid: ev.rid, tool: ev.tool, input: ev.input ?? {}, state: "waiting" });
        state.waiting = true;
        deps.report("waiting", !!state.currentSession);
        return;
      case "permission_done": {
        const card = state.items.find((it) => it.type === "perm" && it.rid === ev.rid);
        if (card && card.type === "perm") card.state = ev.allow ? "allowed" : "denied";
        state.waiting = false;
        deps.report(state.streaming ? "streaming" : "idle", !!state.currentSession);
        return;
      }
      case "rewind_ok":
        state.items.push({ type: "marker", text: ev.text || "已回滚", err: false });
        return;
      case "stopped":
        state.items.push({ type: "marker", text: ev.text || "已中断", err: true });
        onSessionEnded("stopped");
        return;
      case "done":
        addUsage(ev.usage);
        onSessionEnded("done");
        return;
      case "error":
        state.error = ev.text;
        onSessionEnded("error");
        return;
      default:
        return; // 未知 kind 静默忽略(前向兼容)
    }
  }

  /** init 数据入口:attach 载荷 / 流式事件;返回是否被消费。对齐 handleInitData。 */
  function handleData(data: PanelData): { attached?: string } | null {
    if (data && data.attach === true && !data.event) {
      const sid = String(data.session_id || "");
      if (!sid) return null;
      if (data.agent) state.curSessAgent = normAgent(data.agent);
      if (sid !== state.currentSession) void resumeSession(sid, data.agent);
      return { attached: sid };
    }
    const sid = data.session_id ? String(data.session_id) : "";
    if (!sid || !data.event) return null;
    if (discardedSessions.has(sid)) return null;
    if (state.currentSession && sid !== state.currentSession) return null;
    if (data.agent) state.curSessAgent = normAgent(data.agent);
    applyEvent(data.event);
    return {};
  }

  function normAgent(a: string): string { return a === "codex" ? "codex" : "claude-code"; }

  interface SendOverride { prompt?: string; agent?: string; refs?: string[] }

  async function send(cwd: string, prompt: string, mode: string, agent: string, ov: SendOverride = {}): Promise<void> {
    if (state.sending || state.streaming) return;
    const refs = ov.refs ?? [];
    const fullPrompt = prompt + (refs.length ? `\n\n引用文件:\n${refs.map((r) => "@" + r).join("\n")}` : "");
    // handoff 分支由调用方(App)先判:currentSession && switchAgent !== curSessAgent → handoffSend
    state.sending = true;
    state.error = null;
    deps.report("sending", !!state.currentSession);
    pendingTurnEnded = false;
    // 用户气泡不直接画:等 user_msg 回流(带 uuid rewind 锚);超时兜底画无锚气泡
    if (pendingUserEcho) deps.clearTimer(pendingUserEcho);
    pendingUserEcho = deps.setTimer(() => {
      state.items.push({ type: "user", text: fullPrompt });
      fallbackUserIndex = state.items.length - 1;
      pendingUserEcho = null;
    }, deps.userEchoFallbackMs);

    const isStart = !state.currentSession;
    const method = isStart ? "coding.start" : "coding.send";
    const params = isStart
      ? { cwd, prompt: fullPrompt, mode, agent: normAgent(ov.agent ?? agent) }
      : { id: state.currentSession, prompt: fullPrompt, mode };
    try {
      const r = (await deps.invoke(method, params)) as { session_id?: string };
      if (!r || !r.session_id) throw new Error("未返回 session_id");
      // 兜底定时器不清:等 user_msg 回流时由 applyEvent 清(对齐 chat.html;invoke 返回仅代表受理)
      state.currentSession = r.session_id;
      if (isStart) state.curSessAgent = normAgent(ov.agent ?? agent);
      if (pendingTurnEnded) { pendingTurnEnded = false; return; } // 秒败:不进 streaming
      state.streaming = true;
      deps.report("streaming", true);
    } catch (e) {
      if (pendingUserEcho) { deps.clearTimer(pendingUserEcho); pendingUserEcho = null; }
      deps.report("idle", !!state.currentSession);
      state.error = (isStart ? "启动失败:" : "发送失败:") + String(e);
      throw e;
    } finally {
      state.sending = false;
      drainTakeoverQueue(); // 秒败/失败时 onSessionEnded 泄不动,这里兜底
    }
  }

  async function stop(): Promise<void> {
    const sid = state.currentSession;
    if (!sid) return;
    try { await deps.invoke("coding.stop", { id: sid }); }
    catch { /* 流式 stopped 终态负责复位;失败仅解锁由调用方处理 */ }
  }

  function newChat() {
    if (state.currentSession) discardedSessions.add(state.currentSession);
    state.currentSession = null;
    state.items = [];
    state.streaming = false;
    state.sending = false;
    state.waiting = false;
    state.ended = null;
    state.error = null;
    state.usage = { tok: 0, cost: 0, hasCost: false };
    fallbackUserIndex = -1;
    if (pendingUserEcho) { deps.clearTimer(pendingUserEcho); pendingUserEcho = null; }
    takeoverQueue = [];
    deps.report("idle", false);
  }

  /** history 消息 → 渲染模型(user/assistant/marker;assistant 逐条成气泡且 done)。 */
  function historyToItems(msgs: HistoryMessage[]): RenderItem[] {
    const out: RenderItem[] = [];
    for (const m of msgs) {
      if (m.role === "user") out.push({ type: "user", text: m.text, uuid: m.uuid || undefined });
      else if (m.role === "assistant") out.push({ type: "assistant", raw: m.text, thinking: [], done: true });
      else out.push({ type: "marker", text: m.text, err: false });
    }
    return out;
  }

  let resuming = false;
  let pendingResume: { sid: string; agent?: string } | null = null;

  /** 返回恢复的消息数;skipIfEmpty 且空历史返回 0;防重入归并返回 -1。恒不 reject。 */
  async function resumeSession(sid: string, agent?: string, opts: { skipIfEmpty?: boolean } = {}): Promise<number> {
    if (resuming) { pendingResume = { sid, agent }; return -1; }
    resuming = true;
    try {
      const r = (await deps.invoke("coding.history", { id: sid })) as { messages?: HistoryMessage[]; cwd?: string };
      const msgs = r.messages ?? [];
      if (opts.skipIfEmpty && msgs.length === 0) return 0;
      if (state.currentSession) discardedSessions.add(state.currentSession);
      state.currentSession = sid;
      discardedSessions.delete(sid); // 关键:目标会话必须出黑名单,否则自己的流被过滤吞掉锁死面板
      if (agent) state.curSessAgent = normAgent(agent);
      state.items = historyToItems(msgs);
      if (msgs.length) state.items.push({ type: "marker", text: "—— 以上为历史,继续聊 ↓ ——", err: false });
      state.streaming = false;
      state.ended = null;
      state.error = null;
      state.usage = { tok: 0, cost: 0, hasCost: false };
      fallbackUserIndex = -1;
      deps.report("idle", true);
      return msgs.length;
    } catch (e) {
      state.error = "恢复失败:" + String(e);
      return 0;
    } finally {
      resuming = false;
      const next = pendingResume;
      pendingResume = null;
      if (next && next.sid !== state.currentSession) void resumeSession(next.sid, next.agent);
    }
  }

  // —— takeover(宿主输入条经桥消息驱动)——
  function takeoverInput(text: string, refs: string[], cwd: string, mode: string, agent: string) {
    if (state.sending || state.streaming) {
      takeoverQueue.push({ text, refs });
      return { queued: true };
    }
    void send(cwd, text, mode, agent, { refs });
    return { queued: false };
  }

  function takeoverStop() {
    if (state.streaming || state.sending) void stop();
  }

  function drainTakeoverQueue() {
    if (!takeoverQueue.length || state.sending || state.streaming) return;
    // 出队即消费(校验拒发不补发,对齐现状);cwd/mode/agent 由 App 在 send 时快照提供
    const item = takeoverQueue.shift()!;
    void pendingSendFromQueue(item);
  }
  // 队列条目发送需要 cwd/mode/agent 快照,由 App 注入(send 的常规参数源)
  let queueContext: { cwd: string; mode: string; agent: string } = { cwd: "", mode: "acceptEdits", agent: "claude-code" };
  function setQueueContext(ctx: { cwd: string; mode: string; agent: string }) { queueContext = ctx; }
  async function pendingSendFromQueue(item: { text: string; refs: string[] }) {
    await send(queueContext.cwd, item.text, queueContext.mode, queueContext.agent, { refs: item.refs });
  }

  return {
    state, handleData, applyEvent, send, stop, newChat, resumeSession,
    takeoverInput, takeoverStop, setQueueContext, historyToItems,
    _test: { discardedSessions, getQueue: () => takeoverQueue, markTurnEnded: () => { pendingTurnEnded = true; } },
  };
}
