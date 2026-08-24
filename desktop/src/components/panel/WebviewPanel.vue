<script setup lang="ts">
// webview 面板宿主（v2 §8 第三层）：插件 HTML 跑在 <iframe sandbox="allow-scripts"> + srcdoc，
// iframe 内无 Tauri IPC；能力调用全部走 postMessage 桥 → panelAction → sidecar api.toml 白名单裁决。
// 桥协议：
//   iframe → 父：{src:"yibao-webview", id, method, params}   请求调方法
//   iframe → 父：{src:"yibao-webview", event, payload}       事件上报（无 id 无回包，父侧 emit "panel-event"）
//   父 → iframe：{src:"yibao-host", id, ok, result|error}    回包
//   父 → iframe：{src:"yibao-host", type:"init", data}            面板事件 data（iframe 加载完成 & data 变更时推）
//   父 → iframe：{src:"yibao-host", type:"theme", theme}          宿主有效主题（"light"|"dark"，加载完成 & 变更时推）
//   父 → iframe：{src:"yibao-host", ...任意消息}                  postToIframe（如 {type:"ping"}，iframe 经 yibao.onMessage 收）
// 父侧只做命名空间粗筛（method 须以当前面板插件 id 开头）+ event.source 校验；L2 确认条由 PanelApp 闭环。
// module 面板(R4):props.url 非空时走 iframe src(yibao-plugin://),桥由协议层注入;srcdoc 路径行为不变。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { onBrainEvent, panelAction, type BrainEvent } from "../../lib/brain";
import { resolveWebviewSource } from "../../lib/webview-source";

const props = defineProps<{
  panel: string; // 面板引用（plugin_id:name），推导可调方法的命名空间前缀
  html?: string; // 旧 srcdoc 面板 HTML（桥 JS 由本组件注入）
  url?: string; // module 面板 URL（yibao-plugin://，桥由协议层注入，CSP 见 plugin_proto.rs）
  v?: number; // module 面板内容版本（入口 mtime）：变 → :key 变 → iframe 重载（热加载）
  data: Record<string, unknown>; // panel 事件注入的数据（init 推给 iframe）
}>();

const emit = defineEmits<{
  (e: "panel-event", name: string, payload: any): void; // iframe 经 yibao.emitEvent 上报的事件
}>();

const iframeEl = ref<HTMLIFrameElement | null>(null);

// 注入 iframe 的桥 JS(?raw 读 app/src/shared/bridge.js,与 Rust 协议层 include_bytes! 同一文件)。
// 必须出现在插件自有脚本之前——注入到 <head> 之后(无 <head> 则放最前),见 srcdoc computed。
import bridgeJs from "../../shared/bridge.js?raw";

const SCRIPT_OPEN = "<scr" + "ipt>";
const SCRIPT_CLOSE = "</scr" + "ipt>";

// CSP 沙箱兜底（gen 面板等动态 HTML）：禁一切网络/外链，只放行内联脚本样式与 data: 图片字体。
// 与桥 JS 同位置注入（<head> 之后），对插件面板同样生效——插件面板本就要求无网络。
const CSP_META = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:">`;

// module 面板源:有 url 即走真实 src(桥/CSP/importmap 由 yibao-plugin:// 协议层注入,本组件不再注)
const urlSource = computed(() => {
  const s = resolveWebviewSource({ url: props.url, v: props.v });
  return s && s.kind === "url" ? s : null;
});

/** 插件 HTML + 桥 JS 合成 srcdoc（桥注入到插件脚本之前；无 <head> 时整体放最前）。 */
const srcdoc = computed(() => {
  const html = props.html ?? "";
  // 桥体内出现 script 闭合标签字面量会提前终止注入的脚本块（残文渲染成可见文本、
  // window.yibao 未定义——2026-08-24 编辑器「正在等译宝把选题送过来」的根因）；
  // 注入前统一转义（JS 字符串/注释里 \/ === /，无语义影响）。
  const safeBridge = bridgeJs.replace(/<\/script/gi, "<\\/script");
  const tag = CSP_META + SCRIPT_OPEN + safeBridge + SCRIPT_CLOSE;
  const headAt = html.toLowerCase().indexOf("<head>");
  return headAt >= 0
    ? html.slice(0, headAt + 6) + tag + html.slice(headAt + 6)
    : tag + html;
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
  // postMessage 走结构化克隆：msg 里混着 Vue 响应式 Proxy（如 props.data）时
  // WebKit 抛 DataCloneError——消息静默丢失，编辑器永远「等待面板数据…」。
  // 桥消息本就源自 JSON IPC，先 JSON 往返退化成纯数据再发。
  // 注意 src 必须在往返后补上（iframe 桥按 src==="yibao-host" 过滤，丢了全被吃掉）。
  const plain = JSON.parse(JSON.stringify(msg)) as Record<string, unknown>;
  iframeEl.value?.contentWindow?.postMessage({ src: "yibao-host", ...plain }, "*");
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
  const d = ev.data as { src?: string; id?: unknown; event?: unknown; payload?: unknown; method?: unknown; params?: unknown };
  // 事件分流（无 id 无回包）：yibao.emitEvent 上报，转成组件 panel-event 抛给父组件
  if (d && d.src === "yibao-webview" && typeof d.event === "string") {
    emit("panel-event", d.event, d.payload);
    return;
  }
  if (!d || d.src !== "yibao-webview" || typeof d.id !== "number") return;
  const bid = d.id;
  const method = typeof d.method === "string" ? d.method : "";
  // native: 旁路：iframe 无 Tauri IPC，白名单内的原生能力（pick_folder 文件夹选择器 /
  // save_attachment 粘贴图片落盘 / save_file 保存对话框落盘 / open_url 打开系统浏览器）
  // 不经 sidecar 直调 Tauri 命令。白名单严格限定——不放开任意原生命令透传。
  const NATIVE = new Set(["native:pick_folder", "native:save_attachment", "native:save_file", "native:open_url"]);
  if (NATIVE.has(method)) {
    const cmd = method.slice("native:".length); // "pick_folder"
    invoke(cmd, (d.params as Record<string, unknown>) ?? {})
      .then((r) => replyToIframe({ id: bid, ok: true, result: r }))
      .catch((err) => replyToIframe({ id: bid, ok: false, error: String(err) }));
    return;
  }
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

// iframe 就绪闸门(input-handoff spec §C):load 前 postToIframe 暂存(只存最后一条),
// load 时随 init 之后补发;iframe 重建(:key/srcdoc 变)即重置
let loaded = false;
let stashed: Record<string, unknown> | null = null;
watch(() => [props.url, props.v, props.html], () => { loaded = false; });

function onIframeLoad() {
  loaded = true;
  postInit();
  postTheme();
  if (stashed) {
    const m = stashed;
    stashed = null;
    postToIframe(m);
  }
}

/** 父 → iframe 任意消息:iframe 经 yibao.onMessage 收。未就绪暂存;去 Proxy 范式同 replyToIframe。 */
function postToIframe(msg: Record<string, unknown>) {
  if (!loaded) {
    stashed = msg;
    return;
  }
  const plain = JSON.parse(JSON.stringify(msg)) as Record<string, unknown>;
  iframeEl.value?.contentWindow?.postMessage({ src: "yibao-host", ...plain }, "*");
}
defineExpose({ postToIframe });

// ---- 主题通道：module 面板是独立文档，吃不到宿主 tokens.css 的 data-theme 显式通道，
// 由宿主算好有效主题（data-theme 显式值 > 系统媒体查询）推给 iframe（面板侧写自己的 data-theme）。
// 覆盖 HomePlugins（home 窗）与 PanelWindow（浮窗）两宿主——本组件读的是自己所在文档。 ----
const themeMedia = window.matchMedia?.("(prefers-color-scheme: dark)");
function effectiveTheme(): "light" | "dark" {
  const dt = document.documentElement.dataset.theme;
  if (dt === "light" || dt === "dark") return dt;
  return themeMedia?.matches ? "dark" : "light";
}
function postTheme() {
  if (!loaded) return; // 首帧主题由 onIframeLoad 补推；不走 stash（stash 只有一格，会顶掉 handoff 草稿）
  postToIframe({ type: "theme", theme: effectiveTheme() });
}
let themeObserver: MutationObserver | null = null;

let unlisten: (() => void) | null = null;
onMounted(async () => {
  window.addEventListener("message", onMessage);
  unlisten = await onBrainEvent(onEvent);
  // 主题变更监听：data-theme 属性（显式三态切换）+ 系统媒体查询（system 档随 OS 翻转）
  themeObserver = new MutationObserver(postTheme);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  themeMedia?.addEventListener?.("change", postTheme);
});
onBeforeUnmount(() => {
  window.removeEventListener("message", onMessage);
  unlisten?.();
  themeObserver?.disconnect();
  themeMedia?.removeEventListener?.("change", postTheme);
  for (const bid of [...pending.keys()]) settle(bid, undefined, new Error("面板已关闭"));
});
</script>

<template>
  <!-- module 面板(urlSource)必须给 allow-same-origin：面板以 yibao-plugin://<pid> 真实 origin
       运行（非 opaque）——否则面板内嵌的第三方 iframe（B站/网易云播放器）继承 opaque 后
       getCookie 抛 SecurityError 黑屏（子 iframe 自身声明 allow-same-origin 在 WebKit 无效）。
       allow 授权 fullscreen/autoplay 给内嵌播放器（权限逐层授权，父不给子拿不到）。
       安全：该 origin 是自定义协议域而非主窗 origin，跨 window 有进程隔离；
       CSP connect-src 'none' 仍禁插件脚本外联。srcdoc 分支保持 allow-scripts（不给
       allow-same-origin——srcdoc 会继承主窗 origin，给等同放行主窗 DOM）。 -->
  <iframe
    v-if="urlSource"
    :key="urlSource.key"
    ref="iframeEl"
    class="webview"
    sandbox="allow-scripts allow-same-origin"
    allow="fullscreen; autoplay"
    :src="urlSource.url"
    @load="onIframeLoad"
  />
  <iframe
    v-else
    ref="iframeEl"
    class="webview"
    sandbox="allow-scripts"
    :srcdoc="srcdoc"
    @load="onIframeLoad"
  />
</template>

<style scoped>
.webview {
  display: block;
  width: 100%;
  height: 100%;
  border: none;
  border-radius: var(--yb-card-radius);
  /* iframe 背景必须透明：border-radius 只裁 iframe 元素本身、不裁内部文档，
     不透明背景会在圆角外（内部文档矩形方角）漏出灰块——四角「灰」的根源 */
  background: transparent;
}
</style>
