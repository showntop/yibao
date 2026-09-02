// 桌宠窗事件流处理：onEvent（brain 事件分发 224 行）/ onStatus（大脑状态）/ onPerms（权限状态）。
// R-08 从 PetWindow.vue 抽出（2026-08-22）：事件处理是纯函数式依赖注入——
// 本文件不 import 组件，闭包依赖（refs/动作）经 ctx 透传，PetWindow.vue 只保留装配层。
// 类型安全：函数依赖用 typeof 继承真实签名，refs 用精确 Ref 类型，漏传/错传由 vue-tsc 拦截。
import type { Ref } from "vue";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import type { BrainEvent, BrainPermissions, BrainStatusMsg } from "../lib/brain";
import type { Attention, Presentation } from "../lib/surface/surface-policy";
import {
  decideSurface,
} from "../lib/surface/surface-policy";
import {
  deactivateAll,
  petFormOf,
  surfaceCount,
  type SurfaceAttr,
} from "../lib/surface/pet-surface";
import { procLabel, procResultSuffix, procSkip, settleProcOnError, settleProcsOnInterrupt } from "../lib/proc";
import { stripTaskStatusEmoji } from "../lib/text";
import { isTaskLogEvent } from "../lib/work-thread";
import type { usePetState } from "./usePetState";
import type { usePetSpeech } from "./usePetSpeech";
import type { usePetBubbles, BubbleMsg } from "./usePetBubbles";

type PetStateApi = ReturnType<typeof usePetState>;
type PetBubblesApi = ReturnType<typeof usePetBubbles>;
type PetSpeechApi = ReturnType<typeof usePetSpeech>;

/** onEvent/onStatus/onPerms 的闭包依赖面（PetWindow.vue 装配时透传） */
export interface PetEventsCtx {
  // —— 状态（refs / 可变容器）——
  petConvId: Ref<string>;
  state: PetStateApi["state"];
  bubbles: Ref<BubbleMsg[]>;
  streamingIdx: PetBubblesApi["streamingIdx"];
  procIdx: Map<string, number>;
  surfaceAnchor: Map<string, number>;
  expanded: Ref<boolean>;
  speech: PetSpeechApi["speech"];
  speechStreaming: PetSpeechApi["speechStreaming"];
  attentionNeeded: PetStateApi["attentionNeeded"];
  brainDown: Ref<boolean>;
  perms: Ref<BrainPermissions | null>;
  missingPerms: Ref<boolean>;
  /** explicit run 标记（只读：事件侧只判「是否是发起插件」，写侧在组件） */
  requestedPlugin: string;
  // —— 动作（函数依赖，typeof 继承签名）——
  procSkip: typeof procSkip;
  procLabel: typeof procLabel;
  procResultSuffix: typeof procResultSuffix;
  flashValence: PetStateApi["flashValence"];
  flashState: PetStateApi["flashState"];
  showSpeechBubble: PetSpeechApi["showSpeechBubble"];
  speechScheduleHide: PetSpeechApi["scheduleAutoHide"];
  clearExplicit: () => void;
  deactivateAll: typeof deactivateAll;
  surfaceCount: typeof surfaceCount;
  petFormOf: typeof petFormOf;
  decideSurface: typeof decideSurface;
  openPanelWindow: () => void;
  pushWarn: PetBubblesApi["pushWarn"];
  openBubbleSticky: PetBubblesApi["openBubbleSticky"];
  expand: () => Promise<void>;
}

/**
 * 事件处理（小窗固定会话，方案 A）：会话分流（非 pet surface 跳过，panel 例外）、
 * M3 归属过滤（只渲染 petConvId 归属）、状态机驱动与气泡流更新。
 * 返回三个回调，PetWindow 在 onMounted 里交给 onBrainEvent/onBrainStatus/onBrainPermissions。
 */
export function usePetEvents(ctx: PetEventsCtx) {
  const {
    petConvId, state, bubbles, streamingIdx, procIdx, surfaceAnchor,
    expanded, speech, speechStreaming, attentionNeeded, brainDown, perms,
    missingPerms, requestedPlugin,
    procSkip, procLabel, procResultSuffix, flashValence, flashState,
    showSpeechBubble, speechScheduleHide, clearExplicit, deactivateAll,
    surfaceCount, petFormOf, decideSurface, openPanelWindow, pushWarn,
    openBubbleSticky, expand,
  } = ctx;

  function onEvent(e: BrainEvent) {
    // 会话分流：面板场景的对话事件只归面板窗；panel 事件例外（管开窗 + 关联气泡，两窗都收）
    if (e.surface && e.surface !== "pet" && e.kind !== "panel") return;
    // M3 归属过滤：事件属于其他会话（大窗的 run / 别的会话）→ 跳过渲染。
    // 小窗固定会话：只渲染 petConvId 归属的事件，其余已落库到各自会话（切过去可见）。
    if (e.conversationId && petConvId.value && e.conversationId !== petConvId.value) return;
    switch (e.kind) {
      case "action_proposed":
        state.value = "work";
        // 过程行：技能短标签 + pstate 驱动图标（use_plugin 跳过——成功有 notice，不重复）
        if (e.action?.id && !procSkip(e.action)) {
          if (streamingIdx.value !== null) streamingIdx.value = null;
          procIdx.set(e.action.id, bubbles.value.length);
          surfaceAnchor.set(e.action.id, bubbles.value.length);
          bubbles.value.push({ role: "sys", text: procLabel(e.action), pstate: "run" });
        }
        break;
      case "action_result": {
        // 双窗口：确认可能在面板窗作答，结果回来即收尾（成功短闪 400ms，spec 选项 ①）
        flashValence("success");
        // 过程行收尾：pstate 换图标（失败带 error 摘要）；无匹配行（面板直调等）不动
        const idx = e.action?.id !== undefined ? procIdx.get(e.action.id) : undefined;
        if (idx !== undefined) {
          const ok = e.result?.success !== false;
          bubbles.value[idx].pstate = ok ? "ok" : "fail";
          bubbles.value[idx].text = procLabel(e.action) + procResultSuffix(e.result);
          procIdx.delete(e.action!.id!);
        }
        // 唤起条/扩展存素材回执：LLM 摘要打标完成后到标题（quiet 不弹面板，气泡即凭证）
        if (e.action?.tool_id === "zimeiti.mat_save" && e.action?.id?.startsWith("pa_") && e.result?.success) {
          const title = (e.result as { data?: { title?: string } }).data?.title;
          const receipt = title ? `已存素材：《${title}》` : "已存素材";
          if (!expanded.value) {
            // 收起态（存素材时窗多半收着）：说话气泡直接告知 + 定时收起，不依赖系统通知权限
            speech.value = receipt;
            speechStreaming.value = false;
            showSpeechBubble();
            speechScheduleHide(4000);
          } else {
            bubbles.value.push({ role: "sys", text: receipt, icon: "doc" });
          }
        }
        break;
      }
      case "final_reply_chunk": {
        // 收起态：回复进气泡（默认不展开对话窗）
        if (!expanded.value) {
          speech.value = (speech.value ?? "") + (e.text ?? "");
          speechStreaming.value = true;
          showSpeechBubble();
          break;
        }
        // 展开态：流式增量拼到当前 streaming bubble（首片时新建）
        if (streamingIdx.value === null) {
          bubbles.value.push({ role: "ai", text: e.text ?? "" });
          streamingIdx.value = bubbles.value.length - 1;
        } else {
          bubbles.value[streamingIdx.value].text += e.text ?? "";
        }
        break;
      }
      case "final_reply": {
        // 收起态：完整文本收尾进气泡 + 定时自动收起
        if (!expanded.value) {
          speech.value = e.text ?? "";
          speechStreaming.value = false;
          showSpeechBubble();
          speechScheduleHide(8000);
          clearExplicit();
          break;
        }
        // 展开态：以完整文本为准收尾（兜底 chunk 丢失）；语音中保持 say 等 speaking_done
        const full = e.text ?? "";
        if (streamingIdx.value !== null) {
          bubbles.value[streamingIdx.value].text = full;
          streamingIdx.value = null;
        } else {
          bubbles.value.push({ role: "ai", text: full });
        }
        if (state.value !== "say") state.value = "idle";
        clearExplicit();
        break;
      }
      case "interrupted":
        // 在途过程行全部收尾：取消时排队中/待确认的动作不会有 action_result，不收尾会一直转圈
        settleProcsOnInterrupt(bubbles.value, procIdx);
        if (streamingIdx.value !== null) {
          bubbles.value[streamingIdx.value].halted = true;
          streamingIdx.value = null;
        } else {
          // 「已打断」是事件标记，紧贴被打断的那条 AI 消息，不要 push 到末尾
          // （否则用户后续再发消息时标记会跑到两轮之间，位置割裂）
          const mark = { role: "sys" as const, text: "已打断" };
          let lastAiIdx = -1;
          for (let i = bubbles.value.length - 1; i >= 0; i--) {
            if (bubbles.value[i].role === "ai") {
              lastAiIdx = i;
              break;
            }
          }
          if (lastAiIdx >= 0) bubbles.value.splice(lastAiIdx + 1, 0, mark);
          else bubbles.value.push(mark);
        }
        state.value = "idle";
        clearExplicit();
        break;
      case "speech_stopped":
        // 只停播报：final_reply 已完整落气泡，run 不算打断（停止语音 ≠ 取消任务）；
        // 与 speaking_done 同效——回 idle，不标 halted、不插「已打断」标记
        state.value = "idle";
        break;
      case "speaking_done":
        state.value = "idle";
        break;
      case "notice":
        // 轻提示（插件展开等，§12-2 要知情）：居中淡色小字，不弹窗不打断；
        // 收起态看不到气泡流 → 标「有事找你」，点团子即见
        bubbles.value.push({ role: "sys", text: e.text ?? "" });
        if (!expanded.value) attentionNeeded.value = true;
        break;
      case "reminder": {
        // 主动提醒：轻提示而非弹窗——亮窗（若隐藏）+ notify 态 + 常驻气泡，等用户点团子来看；
        // 确认闸门（confirmation_needed）不在此列，仍是强制展开。
        // 自主权「气泡」档（e.level）：不主动亮窗，只标「有事找你」；缺省 level 按完整档（兼容旧 sidecar）。
        // morning_recap：气泡可点击 → deep-link 进 home 回顾视图（Task 12）
        const text = e.text ?? "到点了";
        const isRecap = e.type === "morning_recap";
        const recapDay = e.day;
        // —— 反应式渲染（C 最小版）：确定性信号 → 一次性闪现；提醒纪律（档位/互斥/TTS）不变 ——
        const taskDone = e.task?.status === "done" || (e.type === "watch_command" && e.status === "completed");
        const taskFail = e.task?.status === "failed" || (e.type === "watch_command" && e.status === "failed");
        if (e.type === "health_nudge") {
          flashState("stretch", 1500); // 久坐 → 一套伸展操
        } else if (e.type === "late_night") {
          flashState("drowsy", 3000); // 深夜 → 打哈欠（Zz）
        } else if (e.type === "ambient") {
          // 在场陪伴（C 反应式同款思路：确定性信号 → 一次性闪现）：
          // 专注里程碑 → success 星芒；每日首活跃/回归问候 → notify 招手。气泡走下方通用路径。
          flashState(e.signal === "milestone" ? "success" : "notify", e.signal === "milestone" ? 1500 : 1200);
        } else if (taskDone || taskFail || isTaskLogEvent(e)) {
          // 任务结果 = 轻反应：闪现 + 4s 自收气泡。不插对话胶囊——蓝色提醒会打断当轮输出。
          // 文案剥 emoji 状态前缀：成败已由 flashState 的 success/error 形象反应表达。
          flashState(taskDone ? "success" : taskFail ? "error" : "success", taskDone ? 1200 : 900);
          speech.value = stripTaskStatusEmoji(text);
          speechStreaming.value = false;
          showSpeechBubble();
          speechScheduleHide(4000);
          break;
        }
        bubbles.value.push({ role: "ai", text, icon: "clock", recap: isRecap ? recapDay : undefined });
        void (async () => {
          try {
            // 大小窗互斥：大窗开着时提醒由大窗呈现，别把宠物窗再弹出来
            const home = await WebviewWindow.getByLabel("home");
            if (home && (await home.isVisible())) return;
            const win = getCurrentWindow();
            const visible = await win.isVisible();
            if (!visible && e.level === "bubble") {
              attentionNeeded.value = true; // 气泡档：窗藏着就不打扰，点团子即见
              return;
            }
            if (!visible) await win.show();
            if (!expanded.value) {
              attentionNeeded.value = true;
              openBubbleSticky(text);
            }
          } catch { /* 亮窗失败也至少留了气泡 */ }
        })();
        break;
      }
      case "error":
        state.value = "idle";
        streamingIdx.value = null;
        // 拒绝/禁止执行走 error 而非 action_result：对应过程行原地收尾，否则一直转圈
        settleProcOnError(bubbles.value, procIdx, e);
        pushWarn(e.text ?? "出错了");
        flashValence("error");
        break;
      case "listening":
        state.value = "listen";
        break;
      case "listening_done":
        // 空识别（超时/没说话）：回 idle 并提示——不能进 think，run_done 不复位状态，会永远卡「思考中」
        // 用户句必须无条件入列：长按团子发生在收起态，等 expanded 再 push 会把识别结果丢掉。
        if (e.text) {
          state.value = "think";
          bubbles.value.push({ role: "user", text: e.text });
        } else {
          state.value = "idle";
          bubbles.value.push({ role: "ai", text: "没听清，再试一次？" });
          if (!expanded.value) {
            speech.value = "没听清，再试一次？";
            speechStreaming.value = false;
            showSpeechBubble();
            speechScheduleHide(8000);
          }
        }
        break;
      case "speaking":
        state.value = "say";
        break;
      case "panel": {
        // 面板不再无条件弹独立浮窗（调研 §16 反模式）：先把表面属性补到发起它的
        // 那一行上；无 origin 的刷新事件复用同 panel 最近行，否则新建。stage/focus 只可能在
        // explicit 时出现——裁决器非 explicit 本就封顶 peek。
        const panel = e.payload?.panel ?? "";
        const title = e.payload?.title || panel || "插件面板";
        const titleParts = title.split(" · ").map((part) => part.trim()).filter(Boolean);
        const surfaceTitle = titleParts[titleParts.length - 1] || title;
        const plugin = panel.split(":", 1)[0] || panel;
        // explicit：点过插件启动器/行（requestedPlugin）或后端声明的用户点名信号（payload.explicit，
        // 如对话里「听 XX/看 XX」→ fun 直达方法置 True）——两者都代表用户明确意图，允许开浮窗
        const explicit = requestedPlugin === plugin || e.payload?.explicit === true;

        // cast 与 HomePlugins.vue:391-393 同款：PanelPayload 里这些字段是宽类型，
        // 而裁决器要窄联合。安全性由 sidecar 保证——_load_panels 已按
        // _SURFACE_LEVELS 过滤过非法值，ActionResult 的 Literal 类型同理。
        const decision = decideSurface({
          suggested: (e.payload?.presentation as Presentation | null | undefined) ?? null,
          attention: (e.payload?.attention as Attention | undefined) ?? "suggest",
          explicit,
          current: null,
          supported: e.payload?.surfaces as Presentation[] | undefined,
        });

        // 先记痕：开窗与否都留一条可点行，用户关窗后仍能一键回去
        deactivateAll(bubbles.value);
        const attr: SurfaceAttr = { panel, title: surfaceTitle, count: surfaceCount(e.payload?.data), live: true };
        const at = e.payload?.origin ? surfaceAnchor.get(e.payload.origin) : undefined;
        const row = at !== undefined ? bubbles.value[at] : undefined;
        if (row) {
          row.surface = attr;
        } else {
          let surfaceRow = -1;
          for (let i = bubbles.value.length - 1; i >= 0; i--) {
            if (bubbles.value[i].surface?.panel === panel) {
              surfaceRow = i;
              break;
            }
          }
          if (surfaceRow >= 0) bubbles.value[surfaceRow].surface = attr;
          else bubbles.value.push({ role: "sys", text: "", surface: attr });
        }
        if (e.payload?.origin) surfaceAnchor.delete(e.payload.origin);

        if (petFormOf(decision) === "window") openPanelWindow();
        break;
      }
    }
  }

  function onStatus(m: BrainStatusMsg) {
    if (m.status === "up") {
      if (brainDown.value) {
        brainDown.value = false;
        bubbles.value.push({ role: "ai", text: "✓ 大脑已恢复" });
      }
      return;
    }
    // down / restarting：复位界面状态（进行中的 run/确认已随进程丢失）
    state.value = "idle";
    streamingIdx.value = null;
    if (!brainDown.value) {
      brainDown.value = true;
      const why = m.detail ? `（${m.detail}）` : "";
      pushWarn(`大脑掉线${why}，正在自动重启…`);
    }
  }

  function onPerms(p: BrainPermissions) {
    const wasMissing = missingPerms.value;
    perms.value = p;
    if (missingPerms.value) {
      if (!expanded.value) void expand(); // 权限引导必须可见
    } else if (wasMissing) {
      bubbles.value.push({ role: "ai", text: "✓ 权限就绪" });
    }
  }

  return { onEvent, onStatus, onPerms };
}
