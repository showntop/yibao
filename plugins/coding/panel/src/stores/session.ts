// 会话 store:事件→渲染模型归约器 + 发送状态机。单工位对外暴露一个「当前会话」视图,
// 事件按 sid 过滤(只受理当前会话,陈旧 sid 的流丢弃——无内部分槽);阶段三多工位在
// 壳层多实例化本 store + 壳做事件 demux 分发。
// 行为对齐 chat.html:气泡切断规则、工具卡配对(lastToolCard)、兜底用户气泡原地升级、
// pendingTurnEnded 秒败竞态、resumeSession 的 discarded 解锁、busy 排队泄放(T9:泄放路径
// 补跨引擎交接守卫,对齐原 send() 内联分支)、
// handoffSend 跨引擎交接(:1964-1991)、startHandoffSession Codex→CC(:2268-2319)。
// 阶段三:takeover 退役(spec 输入条节)——report/takeoverStop/clearTakeoverQueue 删除,
// takeoverInput 改名 queueInput(共享输入条 busy 排队复用同一机制)。
import { reactive } from "vue";
import type { CodingEvent, HistoryMessage, PanelData, Usage } from "../lib/types";
import { composeRefs } from "../lib/refs";
import { emsg } from "../lib/format";
import { agentLabel } from "./drivers";

export interface ToolResultInfo { text: string; isError: boolean }

export type RenderItem =
  | { type: "user"; text: string; uuid?: string }
  | { type: "assistant"; raw: string; thinking: string[]; done: boolean }
  | { type: "tool"; tool: string; input: Record<string, unknown>; results: ToolResultInfo[]; hasError: boolean }
  | { type: "fileedit"; tool: string; path: string | null; old: string | null; new: string | null }
  | { type: "marker"; text: string; err: boolean }
  | { type: "error"; text: string }
  // Codex→CC 交接卡(T7):#log chat-flow 元素(session 起点,非 modal);交互态(sealed/编辑文本)
  // 在组件内,store 只持数据;newChat/resumeSession 清 items 时卡随之消失(对齐原 #log 清空)。
  // seq 自增序号:v-for key 用——index-key 在删前卡后会让后卡复用前卡组件实例(text/sealed 串扰),
  // sid 同会话可出两张卡不能作 key
  | { type: "handoff"; seq: number; sid: string; brief: string | null; incomplete: boolean; errMsg: string | null };

export type SessionEnded = "done" | "stopped" | "error" | null;

export interface SessionState {
  items: RenderItem[];
  currentSession: string | null;
  curSessAgent: string;
  sending: boolean;
  streaming: boolean;
  waiting: boolean; // 有待批 permission_request
  ended: SessionEnded;
  usage: { tok: number; cost: number; hasCost: boolean };
  lastUsage: Usage | null; // 最近一个 done 事件的原始 usage(完成状态行「✓ 完成 · Ns · tok · $」用)
  runPrefix: string;       // 运行 pill/状态行前缀「会话 <sid> 启动|接续」(send 进 streaming 时落定)
  error: string | null; // errbar 文本
}

export interface SessionDeps {
  invoke: (method: string, params?: Record<string, unknown>) => Promise<unknown>;
  setTimer: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>;
  clearTimer: (t: ReturnType<typeof setTimeout>) => void;
  userEchoFallbackMs: number; // 生产 1500,测试可 0/手动
  onResumedCwd?: (cwd: string) => void; // resumeSession 成功且 history 带 cwd 时回调(对齐原 setCwd(r.cwd))
  onQueueHandoff?: (agent: string) => void; // 泄放路径跨引擎交接落定(T9):App 清 switchAgent + curAgent 同步
}

export type HandoffSendResult = "sent" | "stale" | "failed";

export interface HandoffSendOpts {
  cwd: string;
  userText: string;
  mode: string;
  refs?: string[];
  isStale: () => boolean;    // 等待期用户在 chip 改选(App 的 switchAgent 偏离目标引擎)
  onHandedOff?: () => void;  // 交接落定回调(App 清 switchAgent、curAgent 同步为新引擎)
}

export function createSessionStore(deps: SessionDeps) {
  const state = reactive<SessionState>({
    items: [], currentSession: null, curSessAgent: "claude-code",
    sending: false, streaming: false, waiting: false, ended: null,
    usage: { tok: 0, cost: 0, hasCost: false }, lastUsage: null, runPrefix: "", error: null,
  });

  // —— 内部簿记(不进响应式状态)——
  const discardedSessions = new Set<string>();
  let sendQueue: Array<{ text: string; refs: string[] }> = [];
  let pendingTurnEnded = false; // 秒败竞态:终态先于 invoke 返回
  let pendingUserEcho: ReturnType<typeof setTimeout> | null = null;
  let fallbackUserIndex = -1;   // 兜底气泡在 items 里的下标(-1 无)
  let handoffSeq = 0;           // 交接卡自增序号(v-for key;newChat 清卡不复位,单调递增即唯一)

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
    state.lastUsage = u; // 完成状态行按最近 done 事件的 usage 渲染(对齐 setStatusDone)
  }

  function onSessionEnded(reason: Exclude<SessionEnded, null>) {
    if (state.sending) pendingTurnEnded = true; // 秒败竞态:终态先于 invoke 返回
    finalizeAssistant();
    state.ended = reason;
    state.streaming = false;
    state.waiting = false;
    if (reason === "stopped") sendQueue = []; // 中断即放弃排队意图
    else drainSendQueue();
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
          state.error = ev.text || "工具执行失败"; // 无卡错误结果退化进 errbar(空文本兜底,对齐原 appendError)
        } else {
          state.items.push({ type: "marker", text: ev.text, err: false });
        }
        return;
      }
      case "permission_request":
        finalizeAssistant();
        state.waiting = true;   // 待批卡不再进消息流;统一落 review 栏(壳层聚合)
        return;
      case "permission_done":
        state.waiting = false;
        return;
      case "marker": // 流内提示入列（coding.py _stream 的 codex resume fallback 直发；空文本兜底）
        state.items.push({ type: "marker", text: ev.text || "", err: false });
        return;
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
        state.error = ev.text || "未知错误"; // 空文本兜底(对齐原 appendError 调用点)
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
    const fullPrompt = prompt + composeRefs(refs); // 引用段组装唯一出处:lib/refs.ts(对齐 chat.html composeRefs)
    // handoff 分支由调用方(App)先判:currentSession && switchAgent !== curSessAgent → handoffSend
    state.sending = true;
    state.error = null;
    pendingTurnEnded = false;
    // 对齐 chat.html:清掉上一轮残留的兜底引用,防跨轮误升级
    fallbackUserIndex = -1;
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
      state.runPrefix = "会话 " + r.session_id + (isStart ? " 启动" : " 接续"); // pill 秒表/状态行前缀
    } catch (e) {
      if (pendingUserEcho) { deps.clearTimer(pendingUserEcho); pendingUserEcho = null; }
      state.error = (isStart ? "启动失败:" : "发送失败:") + String(e);
      throw e;
    } finally {
      state.sending = false;
      drainSendQueue(); // 秒败/失败时 onSessionEnded 泄不动,这里兜底
    }
  }

  // —— 跨引擎交接发送(chat.html:1964-1991 handoffSend):chip 在老会话上选了另一引擎——
  //    coding.session_brief(DB 消息 + git → LLM 摘要,r.brief 恒有值)→ 旧会话进 discarded →
  //    send 走 coding.start 开新引擎会话(isStart 分支带 mode+agent,对齐原 send() ov 路径)。
  //    brief 期间占 sending 窗;等待期用户改主意(新对话/恢复别会话/chip 改选)→ 摘要到响应即弃。
  //    失败 rethrow 给调用方亮状态行(原仅 setStatus,不进 errbar)。
  async function handoffSend(newAgent: string, opts: HandoffSendOpts): Promise<HandoffSendResult> {
    if (state.sending || state.streaming) return "failed"; // 重入守卫(调用方已拦,双保险)
    const oldSid = state.currentSession;
    if (!oldSid) return "failed";
    state.sending = true;
    let brief: string;
    try {
      const r = (await deps.invoke("coding.session_brief", { id: oldSid, target: newAgent })) as { brief?: string } | null;
      brief = (r && r.brief) || "（无历史上下文）";
    } catch (e) {
      state.sending = false;
      drainSendQueue();
      throw e;
    }
    if (state.currentSession !== oldSid || opts.isStale()) { // 等待期改主意 → 丢弃
      state.sending = false;
      drainSendQueue();
      return "stale";
    }
    discardedSessions.add(oldSid);
    state.currentSession = null;
    opts.onHandedOff?.(); // App:清 switchAgent、curAgent 同步为新引擎(防之后新对话落回旧引擎)
    state.items.push({ type: "marker", text: "—— 交接给 " + agentLabel(newAgent) + " 继续（上下文为摘要移植）——", err: false });
    state.sending = false; // 交还 re-entry 窗:正式提交由 send() 自管
    try {
      await send(opts.cwd, "【交接上下文】\n" + brief + "\n\n【用户继续】\n" + opts.userText, opts.mode, newAgent, { refs: opts.refs });
      return "sent";
    } catch {
      return "failed"; // start 失败文本已由 state.error 承载(errbar + 状态行)
    }
  }

  // —— Codex→CC 交接启动(chat.html:2268-2319 startHandoffSession):brief 作为 coding.start
  //    的 prompt,source=codex:<sid> 标记来源(不带 mode/agent——CC 缺省引擎/权限);
  //    受理前即 streaming 态,秒败竞态同 send(pendingTurnEnded 守);brief 已在封存卡内可见,
  //    不再入列用户气泡/兜底回显。恒不 reject:失败落 state.error 返回 false。
  async function startHandoffSession(cwd: string, codexSid: string, brief: string): Promise<boolean> {
    if (state.sending || state.streaming) return false; // 重入守卫(卡上已按 streaming 拦,双保险)
    state.sending = true;
    state.error = null; // handoff 也是新 turn 入口:与 send 对齐清掉旧错误条
    pendingTurnEnded = false;
    state.streaming = true; // 受理前即 streaming(原 :2275)
    state.runPrefix = "Codex 接续启动中…";
    try {
      const r = (await deps.invoke("coding.start", { cwd, prompt: brief, source: "codex:" + codexSid })) as { session_id?: string };
      const csid = r && r.session_id;
      if (!csid) throw new Error("未返回 session_id");
      state.currentSession = csid; // 旧会话 id 被覆盖;其迟到事件被 currentSession 过滤(同原)
      state.curSessAgent = "claude-code"; // handoff 落 CC 会话(coding.start 缺省引擎)
      if (pendingTurnEnded) { pendingTurnEnded = false; return true; } // 秒败:onSessionEnded 已收场
      state.runPrefix = "Codex 接续会话 " + csid;
      return true;
    } catch (e) {
      state.error = "Codex 接续启动失败:" + emsg(e);
      state.streaming = false;
      return false;
    } finally {
      state.sending = false;
      drainSendQueue(); // 同 send.finally:失败/秒败路径泄放队列
    }
  }

  /** 交接卡(Codex→CC)入消息流:可编辑 brief/失败红条由组件渲染,交互态在组件内 */
  function pushHandoffCard(sid: string, brief: string | null, incomplete: boolean, errMsg: string | null) {
    state.items.push({ type: "handoff", seq: ++handoffSeq, sid, brief, incomplete, errMsg });
  }
  function dropHandoffCard(item: RenderItem) {
    const i = state.items.indexOf(item);
    if (i >= 0) state.items.splice(i, 1);
  }

  /** 返回 invoke 是否受理(false=无会话/调用失败)——pill 的 Stop 据此在失败时重新解锁 */
  async function stop(): Promise<boolean> {
    const sid = state.currentSession;
    if (!sid) return false;
    try { await deps.invoke("coding.stop", { id: sid }); return true; }
    catch { return false; } // 流式 stopped 终态负责复位;失败仅解锁由调用方处理
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
    state.lastUsage = null;
    state.runPrefix = "";
    fallbackUserIndex = -1;
    if (pendingUserEcho) { deps.clearTimer(pendingUserEcho); pendingUserEcho = null; }
    sendQueue = [];
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
      state.lastUsage = null;
      state.runPrefix = "";
      fallbackUserIndex = -1;
      if (r.cwd) deps.onResumedCwd?.(String(r.cwd)); // 对齐原 setCwd(r.cwd):恢复跟随会话落盘目录
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

  // —— busy 排队(共享输入条在工位忙时入队,终态泄放;原 takeover 队列机制更名沿用)——
  function queueInput(text: string, refs: string[], cwd: string, mode: string, agent: string) {
    if (state.sending || state.streaming) {
      sendQueue.push({ text, refs });
      return { queued: true };
    }
    // 失败已由 state.error 承载,火忘路径不再外抛
    void send(cwd, text, mode, agent, { refs }).catch(() => {});
    return { queued: false };
  }

  function drainSendQueue() {
    if (!sendQueue.length || state.sending || state.streaming) return;
    // 出队即消费(校验拒发不补发,对齐现状);cwd/mode/agent/switchAgent 由 App 快照提供
    const item = sendQueue.shift()!;
    // 失败已由 state.error 承载,火忘路径不再外抛
    void pendingSendFromQueue(item).catch(() => {});
  }
  // 队列条目发送需要 cwd/mode/agent 快照,由 App 注入(send 的常规参数源);
  // switchAgent 一并快照(T9):泄放路径跨引擎交接守卫的判据(对齐原 send() :1884 内联分支)
  let queueContext: { cwd: string; mode: string; agent: string; switchAgent?: string | null } =
    { cwd: "", mode: "acceptEdits", agent: "claude-code", switchAgent: null };
  function setQueueContext(ctx: { cwd: string; mode: string; agent: string; switchAgent?: string | null }) { queueContext = ctx; }
  async function pendingSendFromQueue(item: { text: string; refs: string[] }) {
    const sw = queueContext.switchAgent;
    // 跨引擎待定守卫(T9 评审修复①,原经 send() 内联分支覆盖泄放路径):老会话 + chip 选了
    // 另一引擎 → handoffSend 摘要移植,与 App onSend 同一判据;等待期快照漂移(chip 改选/清空)即弃;
    // brief 失败:泄放路径无状态行通道,落 errbar 兜底(直发路径是 App 侧状态行 tip)
    if (state.currentSession && sw && sw !== state.curSessAgent) {
      try {
        await handoffSend(sw, {
          cwd: queueContext.cwd, userText: item.text, mode: queueContext.mode, refs: item.refs,
          isStale: () => queueContext.switchAgent !== sw,
          onHandedOff: () => deps.onQueueHandoff?.(sw),
        });
      } catch (e) {
        state.error = "交接摘要生成失败:" + emsg(e);
      }
      return;
    }
    await send(queueContext.cwd, item.text, queueContext.mode, queueContext.agent, { refs: item.refs });
  }

  return {
    state, handleData, applyEvent, send, stop, newChat, resumeSession,
    handoffSend, startHandoffSession, pushHandoffCard, dropHandoffCard,
    queueInput, setQueueContext, historyToItems,
    /** resume 在飞(attach/手动接续/autoReplay)——autoReplay 让位判据:在跑时不得再排候选 */
    isResuming: () => resuming,
    _test: { discardedSessions, getQueue: () => sendQueue, markTurnEnded: () => { pendingTurnEnded = true; } },
  };
}
