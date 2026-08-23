// 大窗对话页气泡流域：气泡状态 + 脑事件 → 气泡流（onEvent）+ 滚动跟随 + 消息操作。
// 依赖经 deps 注入（会话 id、valence 反馈、会话列表 preview、reminder 通知等）。
// 持久化策略：chunk 只改内存，final_reply/收尾时 syncMessage 一次性落盘（消除写放大）。
import { nextTick, ref, watch, type Ref } from "vue";
import { runInput, type BrainEvent, type RunMetrics } from "../lib/brain";
import type { BubbleMsg, ProcInfo, RunRef, HomeAvatarState as AvatarState } from "../lib/home/home-chat-session.ts";
import { newId } from "../state/domains/conversation";
import { sessionStore } from "../state/store";
import type { MessageInput } from "../state/domains/conversation";
import type { Message } from "../state/types";
import { procDetail, procLabel, procResultSuffix, procSkip } from "../lib/proc";
import { squashSpaces, truncate } from "../lib/text";

export interface ChatFlowDeps {
  /** 当前会话 id（气泡持久化/会话归属过滤用） */
  getSessionId: () => string;
  /** 会话列表侧栏当前气泡 preview 更新 */
  sessionRefUpdate: (patch: { preview?: string }) => void;
  /** reminder 事件通知父级切回本页 */
  emitReminder: () => void;
  /** 操作成功的短闪（valence 动画） */
  flashValence: (v: "success" | "error") => void;
  /** 面板协作会话进行中（关联气泡只插一次） */
  panelOpen: Ref<boolean>;
  /** 输入条草稿（编辑重发预填） */
  setDraft: (text: string) => void;
}

export function useChatFlow(deps: ChatFlowDeps) {
  const state = ref<AvatarState>("idle");
  const bubbles = ref<BubbleMsg[]>([]);
  const streamingIdx = ref<number | null>(null); // 正在接收 chunk 的 bubble 下标
  /** 在途流式回复所属会话（null=无流式）：流式中切走再切回时，final_reply 不重复建气泡 */
  let streamingConvId: string | null = null;
  // 过程展示：action.id → 过程行下标，结果回来原地更新 ✅/❌
  const procIdx = new Map<string, number>();
  // 溯源：本次 run 调用的工具引用，挂到下一条 AI 消息（"参考了 ▾"）
  const runRefs: RunRef[] = [];
  // 编辑重发：用户消息下标，发送时从该条起截断替换
  const editTarget = ref<number | null>(null);
  // 气泡流滚动容器
  const bubblesRef = ref<HTMLElement | null>(null);
  const showJump = ref(false);

  // ---- 气泡转换 / 持久化辅助 ----
  function bubbleToInput(b: BubbleMsg, ephemeral = false): MessageInput {
    return {
      id: b.id,
      role: b.role,
      payload: {
        text: b.text,
        panelLink: b.panelLink,
        halted: b.halted,
        icon: b.icon,
        refs: b.refs,
        metrics: b.metrics,
        proc: b.proc
          ? {
              label: b.proc.label,
              done: b.proc.done,
              ok: b.proc.done ? b.proc.result?.success !== false : undefined,
            }
          : undefined,
      },
      ts: b.ts,
      ephemeral,
    };
  }

  function msgToBubble(m: Message): BubbleMsg {
    return {
      id: m.id,
      role: m.role,
      text: m.payload.text,
      panelLink: m.payload.panelLink,
      halted: m.payload.halted,
      icon: m.payload.icon,
      ts: m.ts,
      refs: m.payload.refs,
      metrics: m.payload.metrics,
      proc: m.payload.proc
        ? {
            label: m.payload.proc.label,
            done: m.payload.proc.done,
            expanded: false,
            result: m.payload.proc.ok === undefined ? undefined : { success: m.payload.proc.ok },
          }
        : undefined,
    };
  }

  /** 持久化当前会话的指定气泡（新增/更新），返回带 id 的气泡 */
  function persistBubble(b: BubbleMsg, ephemeral = false): BubbleMsg {
    const sid = deps.getSessionId();
    if (!sid) return b;
    const stored = sessionStore.conversation.appendMessage(sid, bubbleToInput(b, ephemeral));
    if (!b.id) b.id = stored.id;
    else void stored;
    return b;
  }

  /** 流式/过程行结束收尾：按 id 更新已持久化消息 */
  function syncBubble(b: BubbleMsg): BubbleMsg {
    const sid = deps.getSessionId();
    if (!sid || !b.id) return b;
    const stored = sessionStore.conversation.syncMessage(sid, bubbleToInput(b));
    if (!b.id) b.id = stored.id;
    return b;
  }

  /** 气泡尾部插入并持久化（桌面工作戳记/反馈等 HomeChat 侧复用） */
  function pushBubble(b: BubbleMsg): BubbleMsg {
    bubbles.value.push(b);
    return persistBubble(b);
  }

  function pushWarn(text: string) {
    const b: BubbleMsg = { role: "ai", text, icon: "alert", ts: Date.now() };
    bubbles.value.push(b);
    persistBubble(b);
  }

  /** 恢复目标会话气泡（气泡部分）：从 Rust 权威重拉；依赖下标的瞬态全部作废 */
  function restoreBubbles(id: string): Promise<BubbleMsg[]> {
    return sessionStore.conversation.loadMessages(id).then((msgs) => {
      const restored = msgs.map(msgToBubble);
      bubbles.value = restored;
      procIdx.clear();
      runRefs.length = 0;
      return restored;
    });
  }

  // ---- 气泡流滚动：新气泡平滑到底、流式 chunk 即时跟手 ----
  function scrollBubbles(smooth: boolean) {
    void nextTick(() => {
      const el = bubblesRef.value;
      if (!el) return;
      if (smooth) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      else el.scrollTop = el.scrollHeight;
    });
  }

  function onBubblesScroll() {
    const el = bubblesRef.value;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
    showJump.value = !nearBottom && el.scrollHeight > el.clientHeight + 60;
  }

  /** 工具行插入前封口当前流式段，让后续 chunk 在过程卡之后另起一条 AI 消息。 */
  function sealStreaming() {
    if (streamingIdx.value === null) return;
    const b = bubbles.value[streamingIdx.value];
    if (b?.id) syncBubble(b);
    streamingIdx.value = null;
  }

  // ---- 脑事件 → 气泡流 ----
  function onEvent(e: BrainEvent) {
    // 会话分流：面板场景的对话事件只归插件页；panel 事件例外（关联气泡，本页也收）
    if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
    // M3 会话归属过滤：事件带 conversationId 且不属于当前会话 → 跳过渲染
    // （已由 Rust 落库到所属会话，切到该会话即见；流式中切会话不污染当前视图）
    if (e.conversationId && deps.getSessionId() && e.conversationId !== deps.getSessionId()) return;
    switch (e.kind) {
      case "action_proposed":
        state.value = "work";
        // 过程行：🔧 技能短标签（use_plugin 跳过——成功有 notice，不重复）
        if (e.action?.id && !procSkip(e.action)) {
          sealStreaming();
          const procBubble: BubbleMsg = {
            id: newId(),
            role: "sys",
            text: "",
            proc: { label: procLabel(e.action), action: e.action, done: false, expanded: false },
            ts: Date.now(),
          };
          procIdx.set(e.action.id, bubbles.value.length);
          bubbles.value.push(procBubble);
          persistBubble(procBubble);
          runRefs.push({ label: procLabel(e.action), detail: "调用工具中…", ok: false });
        }
        break;
      case "action_result": {
        // 确认可能在别处作答，结果回来即收尾（成功短闪 400ms）
        deps.flashValence("success");
        // 过程行收尾：✅/❌ + 结果存好（点「详情」展开看参数/输出）
        const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
        if (idx !== undefined) {
          const p = bubbles.value[idx].proc;
          if (p) {
            p.done = true;
            p.result = e.result;
          }
          const bubble = bubbles.value[idx];
          if (bubble && bubble.id) syncBubble(bubble);
          procIdx.delete(e.action!.id!);
        }
        // 溯源收尾：把该工具的结果摘要写回最近一条未完成引用
        const ref = runRefs.find((r) => !r.ok && r.label === (e.action ? procLabel(e.action) : ""));
        if (ref) {
          ref.ok = e.result?.success !== false;
          ref.detail = e.result?.success
            ? truncate(String(e.result?.data?.human ?? ""), 60) || "已完成"
            : `失败：${truncate(String(e.result?.error ?? ""), 60)}`;
        }
        break;
      }
      case "final_reply_chunk":
        // 流式增量：拼到当前 streaming bubble（首片时新建；同时挂上本次 run 的溯源引用）
        if (streamingIdx.value === null) {
          const chunkBubble: BubbleMsg = {
            id: newId(),
            role: "ai",
            text: e.text ?? "",
            ts: Date.now(),
            refs: runRefs.length ? [...runRefs] : undefined,
          };
          bubbles.value.push(chunkBubble);
          const sid = deps.getSessionId();
          if (sid) {
            sessionStore.conversation.appendMessage(sid, bubbleToInput(chunkBubble));
          }
          runRefs.length = 0;
          streamingIdx.value = bubbles.value.length - 1;
          streamingConvId = e.conversationId || sid; // 记在途流式归属
        } else {
          bubbles.value[streamingIdx.value].text += e.text ?? "";
        }
        break;
      case "final_reply": {
        // 以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
        const full = e.text ?? "";
        // run 统计（sidecar 聚合进 final_reply 的 payload.metrics）：挂到本条 AI 回复的 indicator bar
        const metrics: RunMetrics | undefined = (e.payload as { metrics?: RunMetrics } | undefined)?.metrics;
        const wasStreamed = streamingConvId !== null && streamingConvId === (e.conversationId || deps.getSessionId());
        streamingConvId = null;
        if (streamingIdx.value !== null) {
          bubbles.value[streamingIdx.value].text = full;
          const streamed = bubbles.value[streamingIdx.value];
          if (metrics) streamed.metrics = metrics;
          if (streamed.id) syncBubble(streamed); // 流式终态落盘
          streamingIdx.value = null;
        } else if (wasStreamed) {
          // 流式期间切走过又切回：Rust 已 update 首片消息为终态，此处从权威重拉
          const convId = deps.getSessionId();
          if (convId) {
            void sessionStore.conversation.loadMessages(convId).then((msgs) => {
              bubbles.value = msgs.map(msgToBubble);
              procIdx.clear();
            });
          }
          runRefs.length = 0;
        } else {
          const finalBubble: BubbleMsg = {
            id: newId(),
            role: "ai",
            text: full,
            ts: Date.now(),
            refs: runRefs.length ? [...runRefs] : undefined,
            metrics,
          };
          bubbles.value.push(finalBubble);
          persistBubble(finalBubble);
          runRefs.length = 0;
        }
        deps.sessionRefUpdate({ preview: truncate(squashSpaces(full), 44) });
        if (state.value !== "say") state.value = "idle";
        break;
      }
      case "interrupted":
        runRefs.length = 0; // 打断：本次 run 的引用作废
        if (streamingIdx.value !== null) {
          const haltedBubble = bubbles.value[streamingIdx.value];
          haltedBubble.halted = true;
          if (haltedBubble.id) syncBubble(haltedBubble);
          streamingIdx.value = null;
        } else {
          const interruptedBubble: BubbleMsg = { role: "ai", text: "已打断", halted: true, ts: Date.now() };
          bubbles.value.push(interruptedBubble);
          persistBubble(interruptedBubble);
        }
        state.value = "idle";
        break;
      case "speaking_done":
        state.value = "idle";
        break;
      case "notice":
        // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断
        {
          const noticeBubble: BubbleMsg = { role: "sys", text: e.text ?? "", ts: Date.now() };
          bubbles.value.push(noticeBubble);
          persistBubble(noticeBubble);
        }
        break;
      case "reminder":
        // 主动提醒：落气泡 + 通知父级切回本页（大窗已可见，宠物窗自己管亮窗，两边互不抢）
        {
          const reminderBubble: BubbleMsg = { role: "ai", text: e.text ?? "到点了", icon: "clock", ts: Date.now() };
          bubbles.value.push(reminderBubble);
          persistBubble(reminderBubble);
        }
        deps.emitReminder();
        break;
      case "error":
        state.value = "idle";
        streamingIdx.value = null;
        runRefs.length = 0;
        pushWarn(e.text ?? "出错了");
        deps.flashValence("error");
        break;
      case "listening":
        state.value = "listen";
        break;
      case "listening_done":
        // 空识别（超时/没说话）：回 idle 并提示——不能进 think，run_done 不复位状态，会永远卡「思考中」
        if (e.text) {
          state.value = "think";
          const userBubble: BubbleMsg = { role: "user", text: e.text, ts: Date.now() };
          bubbles.value.push(userBubble);
          persistBubble(userBubble);
        } else {
          state.value = "idle";
          const missBubble: BubbleMsg = { role: "ai", text: "没听清，再试一次？", ts: Date.now() };
          bubbles.value.push(missBubble);
          persistBubble(missBubble);
        }
        break;
      case "speaking":
        state.value = "say";
        break;
      case "panel": {
        // 面板已弹出：置标记即可。对话流不再产生「⇢ 正在和 X 协作」气泡（机制已去除，
        // 与 Rust 落库一致）；面板入口由工作面本身承担。
        deps.panelOpen.value = true;
        break;
      }
    }
  }

  // ---- 消息操作：复制 / 编辑重发 / 反馈 / 重新生成 ----
  function copyText(t: string) {
    void navigator.clipboard?.writeText(t).catch(() => {});
  }

  function onEditMessage(i: number) {
    const b = bubbles.value[i];
    if (!b || b.role !== "user") return;
    deps.setDraft("");
    void nextTick(() => {
      deps.setDraft(b.text);
      editTarget.value = i;
    });
  }

  function onFeedback(ok: boolean) {
    const b: BubbleMsg = { role: "sys", text: ok ? "已收到正面反馈，会继续保持" : "已收到反馈，会调整回答方式", ts: Date.now() };
    bubbles.value.push(b);
    persistBubble(b);
  }

  /** 重新生成/重试：找到该 AI 消息前最近一条用户消息，截断到它（含）重新 runInput */
  async function regenerate(i: number) {
    const target = bubbles.value[i];
    if (!target || target.role !== "ai") return;
    let j = i - 1;
    while (j >= 0 && bubbles.value[j].role !== "user") j -= 1;
    if (j < 0) return;
    const text = bubbles.value[j].text;
    bubbles.value = bubbles.value.slice(0, j + 1);
    const sid = deps.getSessionId();
    if (sid) sessionStore.conversation.truncateMessages(sid, j + 1);
    streamingIdx.value = null;
    state.value = "think";
    try {
      await Promise.race([
        runInput(text, "pet", sid),
        new Promise<never>((_, rej) => setTimeout(() => rej(new Error("大脑响应超时")), 15000)),
      ]);
    } catch (err) {
      pushWarn("重新生成失败：" + String(err));
      state.value = "idle";
    }
  }

  // 滚动跟随订阅（HomeChat 侧继续 watch showTyping / emit state）
  watch(() => bubbles.value.length, () => scrollBubbles(true));
  watch(() => bubbles.value[bubbles.value.length - 1]?.text, () => scrollBubbles(false));

  // ---- 过程展示辅助（模板用）----
  function procOk(p: ProcInfo): boolean {
    return p.result?.success !== false;
  }
  function procErrSuffix(p: ProcInfo): string {
    return procResultSuffix(p.result);
  }
  function procText(p: ProcInfo): string {
    return procDetail(p.action, p.result);
  }
  function paperShowProc(p: ProcInfo): boolean {
    return !p.done || !procOk(p);
  }

  return {
    state,
    bubbles,
    streamingIdx,
    procIdx,
    runRefs,
    editTarget,
    bubblesRef,
    showJump,
    onEvent,
    restoreBubbles,
    pushBubble,
    pushWarn,
    scrollBubbles,
    onBubblesScroll,
    onEditMessage,
    copyText,
    onFeedback,
    regenerate,
    procOk,
    procErrSuffix,
    procText,
    paperShowProc,
  };
}
