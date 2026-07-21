<script setup lang="ts">
// webview 面板宿主（v2 §8 第三层）：插件 HTML 跑在 <iframe sandbox="allow-scripts"> + srcdoc，
// iframe 内无 Tauri IPC；能力调用全部走 postMessage 桥 → panelAction → sidecar api.toml 白名单裁决。
// 桥协议：
//   iframe → 父：{src:"yibao-webview", id, method, params}   请求调方法
//   父 → iframe：{src:"yibao-host", id, ok, result|error}    回包
//   父 → iframe：{src:"yibao-host", type:"init", data}       面板事件 data（iframe 加载完成 & data 变更时推）
// 父侧只做命名空间粗筛（method 须以当前面板插件 id 开头）+ event.source 校验；L2 确认条由 PanelApp 闭环。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBrainEvent, panelAction, type BrainEvent } from "../lib/brain";

const props = defineProps<{
  panel: string; // 面板引用（plugin_id:name），推导可调方法的命名空间前缀
  html: string; // 插件 webview HTML（桥 JS 由本组件注入）
  data: Record<string, unknown>; // panel 事件注入的数据（init 推给 iframe）
}>();

const iframeEl = ref<HTMLIFrameElement | null>(null);

// 注入 iframe 的桥 JS：提供 window.yibao.invoke()/onInit()。必须出现在插件自有脚本之前，
// 否则插件脚本执行时 window.yibao 尚未定义——故注入到 <head> 之后（无 <head> 则放最前）。
// 注：本字符串里不能出现字面 "</scr" + "ipt>"（会被 Vue SFC 解析器当成脚本块结束）。
const BRIDGE_JS = `
(function () {
  var seq = 0;
  var pending = new Map();
  var initCbs = [];
  window.yibao = {
    invoke: function (method, params) {
      return new Promise(function (resolve, reject) {
        var id = ++seq;
        pending.set(id, { resolve: resolve, reject: reject });
        parent.postMessage({ src: "yibao-webview", id: id, method: method, params: params || {} }, "*");
      });
    },
    onInit: function (cb) { initCbs.push(cb); }
  };
  window.addEventListener("message", function (ev) {
    var d = ev.data;
    if (!d || d.src !== "yibao-host") return;
    if (d.type === "init") {
      initCbs.forEach(function (cb) { try { cb(d.data); } catch (e) { console.error(e); } });
      return;
    }
    var p = pending.get(d.id);
    if (!p) return;
    pending.delete(d.id);
    if (d.ok) p.resolve(d.result);
    else p.reject(new Error(d.error || "调用失败"));
  });
})();
`;

const SCRIPT_OPEN = "<scr" + "ipt>";
const SCRIPT_CLOSE = "</scr" + "ipt>";

/** 插件 HTML + 桥 JS 合成 srcdoc（桥注入到插件脚本之前）。 */
const srcdoc = computed(() => {
  const tag = SCRIPT_OPEN + BRIDGE_JS + SCRIPT_CLOSE;
  const headAt = props.html.toLowerCase().indexOf("<head>");
  return headAt >= 0
    ? props.html.slice(0, headAt + 6) + tag + props.html.slice(headAt + 6)
    : tag + props.html;
});

// ---- 在途桥调用：桥 id → 回包函数；rid 关联 action_result（sidecar 动作 id 为 "pa_<rid>"）----
interface Pending {
  rid: number;
  timer: ReturnType<typeof setTimeout>;
  resolve: (v: unknown) => void;
  reject: (e: Error) => void;
}
const pending = new Map<number, Pending>();
let ridBase = Math.floor(Math.random() * 1e9);

function replyToIframe(msg: Record<string, unknown>) {
  iframeEl.value?.contentWindow?.postMessage({ src: "yibao-host", ...msg }, "*");
}

function settle(bid: number, result?: unknown, error?: Error) {
  const p = pending.get(bid);
  if (!p) return;
  clearTimeout(p.timer);
  pending.delete(bid);
  if (error) p.reject(error);
  else p.resolve(result);
}

function onMessage(ev: MessageEvent) {
  const iframe = iframeEl.value;
  if (!iframe || ev.source !== iframe.contentWindow) return; // 只收本 iframe 的消息
  const d = ev.data as { src?: string; id?: unknown; method?: unknown; params?: unknown };
  if (!d || d.src !== "yibao-webview" || typeof d.id !== "number") return;
  const bid = d.id;
  const method = typeof d.method === "string" ? d.method : "";
  const prefix = props.panel.split(":")[0] + ".";
  if (!method.startsWith(prefix)) {
    replyToIframe({ id: bid, ok: false, error: `方法须以 ${prefix} 开头：${method || "(空)"}` });
    return;
  }
  const rid = (ridBase = (ridBase + 1) % 2 ** 31);
  const timer = setTimeout(() => settle(bid, undefined, new Error("调用超时")), 120_000); // L2 确认等用户点头，超时给足
  pending.set(bid, {
    rid,
    timer,
    resolve: (v) => replyToIframe({ id: bid, ok: true, result: v }),
    reject: (e) => replyToIframe({ id: bid, ok: false, error: e.message }),
  });
  panelAction(method, (d.params as Record<string, unknown>) ?? {}, rid).catch((err) => {
    settle(bid, undefined, new Error("面板通道失败：" + String(err)));
  });
}

function onEvent(e: BrainEvent) {
  if (e.kind === "action_result") {
    const aid = e.action?.id ?? "";
    for (const [bid, p] of [...pending]) {
      if (aid === `pa_${p.rid}`) {
        if (e.result?.success) settle(bid, e.result.data ?? {});
        else settle(bid, undefined, new Error(e.result?.error || "执行失败"));
      }
    }
  } else if (e.kind === "error") {
    // 只结算带本桥 rid 标签的错误（sidecar 给面板直调的错误带 action.id = pa_<rid>）；
    // 无关错误（TTS/记忆降级/对话 run 出错等）不许杀 pending——否则编辑器加载被误清
    const aid = e.action?.id ?? "";
    if (!aid) return;
    for (const [bid, p] of [...pending]) {
      if (aid === `pa_${p.rid}`) settle(bid, undefined, new Error(e.text || "出错了"));
    }
  }
}

/** 把面板事件 data 推给 iframe（加载完成时 + data 变更时；同面板重发不重建 iframe）。 */
function postInit() {
  replyToIframe({ type: "init", data: props.data });
}
watch(() => props.data, postInit);

let unlisten: (() => void) | null = null;
onMounted(async () => {
  window.addEventListener("message", onMessage);
  unlisten = await onBrainEvent(onEvent);
});
onBeforeUnmount(() => {
  window.removeEventListener("message", onMessage);
  unlisten?.();
  for (const bid of [...pending.keys()]) settle(bid, undefined, new Error("面板已关闭"));
});
</script>

<template>
  <iframe
    ref="iframeEl"
    class="webview"
    sandbox="allow-scripts"
    :srcdoc="srcdoc"
    @load="postInit"
  />
</template>

<style scoped>
.webview {
  display: block;
  width: 100%;
  height: 100%;
  border: none;
  border-radius: var(--yb-radius-md);
  background: #fffaf4;
}
</style>
