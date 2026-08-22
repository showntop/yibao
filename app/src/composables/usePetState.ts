// 桌宠 Avatar 状态机：运行态（listen/think/work/say）+ 环境态（有事/发呆）+ 短暂闪现（success/error/stretch）。
// 由 App.vue 的 onEvent 等驱动；本 composable 只管状态推导与时长。纯前端状态，不涉及 IPC。
import { computed, ref, watch, type Ref } from "vue";
import type { SettingsValues } from "../protocol/brain-types";

export type PetAvatarState =
  | "idle"
  | "listen"
  | "think"
  | "work"
  | "say"
  | "success"
  | "error"
  | "notify"
  | "drowsy"
  | "stretch";

/**
 * @param expanded 窗口展开态（收起态下发呆相才露脸）
 */
export function usePetState(expanded: Ref<boolean>) {
  const state = ref<PetAvatarState>("idle");
  /** 有事找你（提醒/收起时的播报，展开即消） */
  const attentionNeeded = ref(false);
  /** 发呆（连续纯待命超时） */
  const drowsy = ref(false);

  /** 展示态：运行态优先；idle 时按 有事 > 发呆 > 普通 推导（发呆只在收起态露脸） */
  const petState = computed<PetAvatarState>(() => {
    if (state.value !== "idle") return state.value;
    if (attentionNeeded.value) return "notify";
    if (!expanded.value && drowsy.value) return "drowsy";
    return "idle";
  });

  const statusText = computed(
    () =>
      ({
        idle: "待命中", listen: "聆听中", think: "思考中…", work: "操作中…", say: "说话中…",
        success: "完成", error: "出错了", notify: "有事找你", drowsy: "发呆中", stretch: "伸展中",
      })[petState.value],
  );
  // success/error 是短暂 valence（不可打断），不算 busy
  const busy = computed(
    () =>
      state.value === "listen" || state.value === "think" ||
      state.value === "work" || state.value === "say",
  );

  // 感知观察中叠加点（Avatar observing prop）：总开关 + 任一采集源开启即视为观察中
  const observing = ref(false);
  function syncObserving(s: SettingsValues | null) {
    observing.value = !!(
      s?.["perception.master"] &&
      (s?.["perception.app"] || s?.["perception.activity"] || s?.["perception.screen"])
    );
  }

  // 发呆：连续 5 分钟纯待命则入睡相；任何运行态变化/碰团子即醒并重计时
  let drowsyTimer: ReturnType<typeof setTimeout> | null = null;
  function armDrowsy() {
    if (drowsyTimer) clearTimeout(drowsyTimer);
    drowsyTimer = setTimeout(() => {
      drowsy.value = true;
    }, 5 * 60_000);
  }
  watch(
    state,
    (s) => {
      if (s === "idle") armDrowsy();
      else {
        if (drowsyTimer) {
          clearTimeout(drowsyTimer);
          drowsyTimer = null;
        }
        drowsy.value = false;
      }
    },
    { immediate: true },
  );
  /** 只负责醒团子（发呆重置）。弹工作台交给 pet-cursor-enter（Rust 56×56 内缩热区）。 */
  function onPetHover() {
    if (state.value !== "idle") return;
    drowsy.value = false;
    armDrowsy();
  }

  // 短暂闪现（success/error/stretch/drowsy…）：ms 后回 idle，期间不可打断
  let flashTimer: ReturnType<typeof setTimeout> | null = null;
  function flashState(v: PetAvatarState, ms = 400) {
    if (flashTimer) clearTimeout(flashTimer);
    state.value = v;
    flashTimer = setTimeout(() => {
      if (state.value === v) state.value = "idle";
      flashTimer = null;
    }, ms);
  }
  function flashValence(v: "success" | "error") {
    flashState(v, 400);
  }

  /** 卸载时清理定时器。 */
  function dispose() {
    if (drowsyTimer) clearTimeout(drowsyTimer);
    if (flashTimer) clearTimeout(flashTimer);
  }

  return {
    state,
    petState,
    attentionNeeded,
    drowsy,
    statusText,
    busy,
    observing,
    syncObserving,
    flashState,
    flashValence,
    onPetHover,
    dispose,
  };
}
