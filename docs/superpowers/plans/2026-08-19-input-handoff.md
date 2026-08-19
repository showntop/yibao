# 输入条 handoff(Composer 接管)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** coding:studio 面板打开期间,壳译宝条整行让位,iframe 内 Composer 复刻 InputBar 几何落进同一槽位接管输入;逃生口(标题栏团子+浮层 mini 输入)保译宝可达;草稿单向随迁。

**Architecture:** 纯前端、零后端/零新协议。壳侧(PanelApp)`current.panel === "coding:studio"` 判定 handoff → bench-bar `v-if` 移除;随迁复用现有 `postToIframe`(宿主→iframe,WebviewPanel 加就绪暂存);面板侧 Composer/StationView/App 逐级暴露 `fillDraft`。依据 spec:`docs/superpowers/specs/2026-08-19-input-handoff-design.md`。

**Tech Stack:** Vue 3.5 + TypeScript;app 仓 vitest(新增组件测试基建);panel 仓 vitest + @vue/test-utils + happy-dom(已有)。

## Global Constraints

- **零后端改动**:sidecar/Rust/api.toml 一律不碰;不新开桥协议通道(只用现有 `postToIframe` 宿主→iframe)。
- **依赖**:不新增运行时依赖;app 新增 devDeps 仅 `@vue/test-utils@^2.4.11` + `happy-dom@^20.11.2`(与 panel 仓同版本,组件测试基建)。
- **诚实差异**:Composer 不放语音钮;目标工位/引擎/cwd 家具保留可见。
- **样式同源标注**:Composer 复刻块的样式字面值抄自 `app/src/components/InputBar.vue`,注释互为指针(沙箱 iframe 吃不到 tokens.css,字面值写死是既定先例)。
- **panel dist 必须重建随提交**:`plugins/coding/panel` 的 `pnpm build` 产物 `dist/` 在仓库内,面板从 dist 现读。
- 注释中文、风格同既有文件;TDD 先败后成;每任务一 commit。
- 易踩的坑(已核实,勿再踩):
  - InputBar 随 bench-bar 卸载 → **取稿必须先于 `current` 赋值**(Task 3);
  - `button.ghost` 被 HistoryOverlay 的 `lc-act ghost` 共用 → **只删 `button.act`,保留 `button.ghost`**(Task 5);
  - 函数 ref 回调不得携带无条件响应式写入(App.vue 渲染风暴防线,`App.mount.test.ts` 盯着)。

---

### Task 1: app 组件测试基建 + 壳让位 + 标题栏团子

**Files:**
- Modify: `app/package.json`(devDependencies 加两条)
- Modify: `app/src/components/PanelApp.vue`(:60 附近加 handoff computed;模板 titlebar 与 bench-bar;样式 .name/.titlebar-pet/.bench/.bench-bar)
- Test: `app/src/components/PanelApp.handoff.test.ts`(新建)

**Interfaces:**
- Consumes: 无(本任务自给)
- Produces: `handoff` computed(Task 2/3 复用);`PanelApp.handoff.test.ts` 的 `mountApp()`/`firePanel()` 脚手架(Task 2/3 复用)

- [ ] **Step 1: 装组件测试基建**

```bash
cd app && pnpm add -D @vue/test-utils@^2.4.11 happy-dom@^20.11.2
```

- [ ] **Step 2: 写失败测试** `app/src/components/PanelApp.handoff.test.ts`

```ts
// @vitest-environment happy-dom
// 输入条 handoff(spec 2026-08-19-input-handoff-design.md §A):
// coding:studio 打开 → bench-bar(团子+chip+InputBar)整行让位,团子搬标题栏;切走原样恢复
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";

let brainHandler: ((e: any) => void) | null = null;
const runInputMock = vi.fn(() => Promise.resolve());
vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn((cb: any) => { brainHandler = cb; return Promise.resolve(() => {}); }),
  onPendingConfirms: vi.fn(() => () => {}),
  openHomeWindow: vi.fn(() => Promise.resolve()),
  panelAction: vi.fn(() => Promise.resolve({})),
  sendConfirmBatch: vi.fn(() => Promise.resolve()),
  runInput: (...a: any[]) => runInputMock(...a),
  voiceStart: vi.fn(() => Promise.resolve()),
  interrupt: vi.fn(() => Promise.resolve()),
  reportPanelContext: vi.fn(() => Promise.resolve()),
  setSurface: vi.fn(),
  canRememberSkill: vi.fn(() => false),
  rememberLabelForSkill: vi.fn(() => ""),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ onFocusChanged: vi.fn(() => Promise.resolve(() => {})) }),
}));

import PanelApp from "./PanelApp.vue";

function mountApp() {
  return mount(PanelApp, {
    global: {
      stubs: {
        SchemaPanel: { template: "<div class='schema-stub' />" },
        WebviewPanel: { template: "<div class='webview-stub' />" },
        InputBar: { template: "<div class='inputbar-stub' />" },
        Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
        YbIcon: { template: "<i />" },
      },
    },
  });
}

function firePanel(panel: string) {
  brainHandler!({
    kind: "panel",
    payload: { panel, title: panel, schema: null, webview: { url: "yibao-plugin://x/panel/dist/index.html", v: 1 }, data: {} },
  });
}

describe("输入条 handoff", () => {
  it("coding:studio 打开 → bench-bar 让位 + 团子搬标题栏;切走恢复", async () => {
    const w = mountApp();
    await flushPromises();
    expect(w.find(".bench-bar").exists()).toBe(true);
    expect(w.find(".titlebar .pet").exists()).toBe(false);
    firePanel("coding:studio");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(false);
    expect(w.find(".titlebar .pet").exists()).toBe(true);
    firePanel("toolbox:main");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
    expect(w.find(".titlebar .pet").exists()).toBe(false);
  });
});
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts`
Expected: FAIL(`.titlebar .pet` 不存在;bench-bar 未隐藏)

- [ ] **Step 4: 实现**

`PanelApp.vue` script,`chipText` computed 之后加:

```ts
// ---- 输入条 handoff(2026-08-19 input-handoff spec):coding:studio 打开期间译宝条整行让位,
//      iframe 内 Composer 落进本槽位接管输入;团子搬标题栏做逃生口。纯壳侧行为,零桥协议 ----
const handoff = computed(() => current.value?.panel === "coding:studio");
```

模板 titlebar(现 370-373 行)改为:

```html
    <div class="titlebar" data-tauri-drag-region>
      <span class="name">
        <!-- handoff 逃生口:团子搬到壳标题栏(点击开浮层问译宝,mini 输入见 Task 2) -->
        <Avatar v-if="handoff" class="pet titlebar-pet" :state="state" :size="20" @click="openLayer" />
        {{ current?.title ?? "面板" }}
      </span>
      <button class="x" title="关闭" @click="close">×</button>
    </div>
```

模板 bench-bar(现 453 行)加 `v-if`:

```html
      <div v-if="!handoff" class="bench-bar">
```

样式改动(现 624-628、791-795 行):`.bench` 的 margin 移给 `.bench-bar`(让位后空 .bench 不留缝;`.thread` 绝对定位不受影响):

```css
.bench {
  position: relative;
}
.bench-bar {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  margin: 0 var(--yb-space-2) var(--yb-space-2);
}
```

`.name` 改为 flex 容器 + 新增团子样式:

```css
.name {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  font-size: var(--yb-fs-lg);
  font-weight: 600;
}
.titlebar-pet {
  flex-shrink: 0;
  cursor: pointer;
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts`
Expected: PASS(1 passed)

- [ ] **Step 6: 回归 + 提交**

```bash
cd app && pnpm test
git add app/package.json app/pnpm-lock.yaml app/src/components/PanelApp.vue app/src/components/PanelApp.handoff.test.ts
git commit -m "feat(panel): 输入条 handoff——coding:studio 期间译宝条让位+团子搬标题栏"
```

### Task 2: 逃生口——浮层 mini 输入直问大脑

**Files:**
- Modify: `app/src/components/PanelApp.vue`(script 加 askText/submitBrain/onAskEnter;模板 thread 内 ask-row;样式 .ask-*)
- Test: `app/src/components/PanelApp.handoff.test.ts`(追加用例)

**Interfaces:**
- Consumes: Task 1 的 `handoff` computed、`mountApp()`/`firePanel()` 脚手架
- Produces: 无新接口(纯 PanelApp 内部)

- [ ] **Step 1: 写失败测试**(追加到 `PanelApp.handoff.test.ts` 的 describe 内)

```ts
  it("handoff 期点标题栏团子开浮层,mini 输入直问大脑;收起清空残稿", async () => {
    const w = mountApp();
    await flushPromises();
    firePanel("coding:studio");
    await nextTick();
    await w.find(".titlebar .pet").trigger("click");
    expect(w.find(".ask-row").exists()).toBe(true);
    await w.find(".ask-input").setValue("这个报错什么意思");
    await w.find(".ask-send").trigger("click");
    expect(runInputMock).toHaveBeenCalledWith("这个报错什么意思");
    expect(w.find(".t-row.user").text()).toContain("这个报错什么意思");
    // 收起清空残稿,重开不留
    await w.find(".thread-x").trigger("click");
    await w.find(".titlebar .pet").trigger("click");
    expect((w.find(".ask-input").element as HTMLInputElement).value).toBe("");
  });

  it("非 handoff 时无 mini 输入(主 InputBar 在场,不需要逃生口)", async () => {
    const w = mountApp();
    await flushPromises();
    firePanel("toolbox:main");
    await nextTick();
    expect(w.find(".ask-row").exists()).toBe(false);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts`
Expected: FAIL(`.ask-row` 不存在)

- [ ] **Step 3: 实现**(复活 30dd8c9 的逃生口,isCoding 路由已随 takeover 退役,只剩直问大脑一条路径)

script 顶部 vue import 行加 `watch`:

```ts
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
```

script `submit` 函数之后加:

```ts
/** 逃生口「问团子」(handoff 期浮层底部 mini 输入):直走译宝大脑(原 runInput 路径,
 *  面板 focus 已在大脑上下文里),不打断编码会话。复活自 30dd8c9,takeover 路由已退役。 */
const askText = ref("");
// 浮层收起即清空 mini 输入,下次打开不留残稿
watch(layerVisible, (v) => {
  if (!v) askText.value = "";
});

function submitBrain(text: string) {
  const t = text.trim();
  if (!t) return;
  errorText.value = "";
  askText.value = "";
  pushMsg("user", t); // 输入立刻有落点(浮层时间线)
  void runInput(t).catch((err) => {
    errorText.value = "发送失败：" + String(err);
  });
}

// mini 输入 IME 守卫(同 InputBar:WebKit 下 compositionend 先于确认 Enter 的 keydown,
// 该 keydown 的 isComposing 已为 false——记 compositionend 时间戳,50ms 窗口内的 Enter 一并拦截)
let askCompEnd = 0;
function onAskEnter(e: KeyboardEvent) {
  if (e.isComposing || Date.now() - askCompEnd < 50) return;
  submitBrain(askText.value);
}
```

模板 thread 的 `v-if` 条件扩展(handoff 时空消息也渲染浮层:逃生口要有落点):

```html
        <div v-if="layerVisible && (msgs.length || listeningHint || handoff)" ref="layerRef" class="thread">
```

thread 内聆听行之后、`</div>` 收尾之前加:

```html
          <!-- 逃生口 mini 输入行(仅 handoff 渲染):单行 input + 发送钮,直问译宝大脑 -->
          <div v-if="handoff" class="ask-row">
            <input
              v-model="askText"
              class="ask-input"
              type="text"
              placeholder="问团子…（不打断编码会话）"
              @keydown.enter.exact.prevent="onAskEnter"
              @compositionend="askCompEnd = Date.now()"
            />
            <button type="button" class="ask-send" :disabled="!askText.trim()" @click="submitBrain(askText)">
              发送
            </button>
          </div>
```

样式追加(`.t-row.is-fail` 规则之后):

```css
/* 逃生口 mini 输入行:thread 底部单行 input + accent 发送钮,与 thread 同玻璃调性 */
.ask-row {
  display: flex;
  align-items: center;
  gap: var(--yb-space-2);
  padding-top: 2px;
}
.ask-input {
  flex: 1;
  min-width: 0;
  padding: 5px 10px;
  border: 1px solid var(--yb-surface-border);
  border-radius: var(--yb-radius-md);
  background: var(--yb-surface);
  color: var(--yb-text);
  font-size: var(--yb-fs-md);
  font-family: inherit;
  outline: none;
}
.ask-input:focus {
  border-color: var(--yb-accent);
}
.ask-send {
  flex-shrink: 0;
  border: none;
  border-radius: var(--yb-radius-md);
  padding: 5px 14px;
  background: var(--yb-accent);
  color: var(--yb-text-on-accent);
  font-size: var(--yb-fs-md);
  cursor: pointer;
}
.ask-send:disabled {
  opacity: 0.4;
  cursor: default;
}
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + 提交**

```bash
cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts && pnpm test
git add app/src/components/PanelApp.vue app/src/components/PanelApp.handoff.test.ts
git commit -m "feat(panel): handoff 逃生口——标题栏团子开浮层 mini 输入直问大脑(复活 30dd8c9)"
```

### Task 3: 草稿随迁(壳侧)——takeDraft + 桥就绪暂存 + setCurrent 接线

**Files:**
- Modify: `app/src/components/InputBar.vue`(:332 defineExpose 加 takeDraft)
- Modify: `app/src/components/WebviewPanel.vue`(loaded/stashed/onIframeLoad;两个 iframe `@load`)
- Modify: `app/src/components/PanelApp.vue`(webviewRef;setCurrent 随迁)
- Test: `app/src/components/InputBar.test.ts`(新建)
- Test: `app/src/components/WebviewPanel.test.ts`(新建)
- Test: `app/src/components/PanelApp.handoff.test.ts`(追加)

**Interfaces:**
- Consumes: Task 1 的 `handoff` 与测试脚手架
- Produces: `InputBar` expose `takeDraft(): string`;`WebviewPanel` 就绪暂存语义(load 前 postToIframe 只存最后一条,load 时补发);宿主→iframe 消息 `{type:"handoff-draft", text}`(Task 4 面板侧消费)

- [ ] **Step 1: 写失败测试**

新建 `app/src/components/InputBar.test.ts`:

```ts
// @vitest-environment happy-dom
// takeDraft(handoff 草稿随迁):取走草稿=清文本+清持久化副本;空草稿返回 ""
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setDraftMock = vi.fn();
vi.mock("../state/store", () => ({
  sessionStore: {
    conversation: {
      getActiveConversationId: () => "c1",
      getUIState: () => ({ draft: "" }),
      setDraft: (...a: any[]) => setDraftMock(...a),
    },
  },
}));
vi.mock("../lib/brain", () => ({
  panelAction: vi.fn(() => Promise.resolve({})),
  onBrainEvent: vi.fn(() => Promise.resolve(() => {})),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));

import InputBar from "./InputBar.vue";

describe("InputBar takeDraft", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("有稿:返回 trim 后文本,清空文本与持久化副本", async () => {
    const w = mount(InputBar, { global: { stubs: { YbIcon: { template: "<i />" } } } });
    const ta = w.find("textarea");
    (ta.element as HTMLTextAreaElement).value = "  帮我修这个  ";
    await ta.trigger("input");
    const d = (w.vm as any).takeDraft();
    expect(d).toBe("帮我修这个");
    expect((ta.element as HTMLTextAreaElement).value).toBe("");
    vi.advanceTimersByTime(300); // persistDraft 300ms debounce
    expect(setDraftMock).toHaveBeenCalledWith("c1", "");
  });

  it("空稿:返回空串,不动持久化", () => {
    const w = mount(InputBar, { global: { stubs: { YbIcon: { template: "<i />" } } } });
    expect((w.vm as any).takeDraft()).toBe("");
    vi.advanceTimersByTime(300);
    expect(setDraftMock).not.toHaveBeenCalledWith("c1", "");
  });
});
```

新建 `app/src/components/WebviewPanel.test.ts`:

```ts
// @vitest-environment happy-dom
// postToIframe 就绪暂存:iframe load 前只存最后一条,load 时(init 之后)补发
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn(() => Promise.resolve(() => {})),
  panelAction: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(() => Promise.resolve(null)) }));

import WebviewPanel from "./WebviewPanel.vue";

const PROPS = { panel: "coding:studio", url: "yibao-plugin://coding/panel/dist/index.html", v: 1, data: {} };

describe("WebviewPanel postToIframe 就绪暂存", () => {
  it("load 前暂存,load 后补发;之后直发", async () => {
    const w = mount(WebviewPanel, { props: PROPS });
    const iframe = w.find("iframe").element as HTMLIFrameElement;
    const postSpy = vi.spyOn(iframe.contentWindow!, "postMessage");
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "甲" });
    expect(postSpy).not.toHaveBeenCalled();
    await w.find("iframe").trigger("load");
    expect(postSpy).toHaveBeenCalledWith(
      expect.objectContaining({ src: "yibao-host", type: "handoff-draft", text: "甲" }),
      "*",
    );
    postSpy.mockClear();
    (w.vm as any).postToIframe({ type: "handoff-draft", text: "乙" });
    expect(postSpy).toHaveBeenCalledTimes(1);
  });
});
```

追加到 `PanelApp.handoff.test.ts` describe 内(stub 升级:InputBar/WebviewPanel 换 expose 桩):

```ts
  it("随迁:进 coding 瞬间取走译宝条草稿,postToIframe handoff-draft;空稿不触发", async () => {
    const takeDraft = vi.fn(() => "草稿文字");
    const postToIframe = vi.fn();
    const w = mount(PanelApp, {
      global: {
        stubs: {
          SchemaPanel: { template: "<div />" },
          WebviewPanel: { template: "<div />", setup(_: any, { expose }: any) { expose({ postToIframe }); return {}; } },
          InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ takeDraft, focus() {}, insertText() {} }); return {}; } },
          Avatar: { template: "<button class='avatar-stub' v-bind='$attrs' />" },
          YbIcon: { template: "<i />" },
        },
      },
    });
    await flushPromises();
    firePanel("toolbox:main"); // 先落在非 coding:不触发
    await flushPromises();
    expect(takeDraft).not.toHaveBeenCalled();
    firePanel("coding:studio");
    await flushPromises();
    expect(takeDraft).toHaveBeenCalledTimes(1);
    expect(postToIframe).toHaveBeenCalledWith({ type: "handoff-draft", text: "草稿文字" });
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && pnpm vitest run src/components/InputBar.test.ts src/components/WebviewPanel.test.ts src/components/PanelApp.handoff.test.ts`
Expected: FAIL(takeDraft 不存在;load 前 postMessage 直发;随迁未触发)

- [ ] **Step 3: 实现**

`InputBar.vue`,`defineExpose`(:332)改为:

```ts
/** handoff 草稿随迁(spec §C):取走草稿=清文本+清持久化副本(persistDraft("")走 300ms
 *  debounce 写 store);空草稿返回 ""。调用时机必须在 InputBar 随 bench-bar 卸载之前。 */
function takeDraft(): string {
  const d = text.value.trim();
  if (!d) return "";
  text.value = "";
  persistDraft("");
  return d;
}

// 全局唤起等外部焦点请求(反射键唤起后输入就绪);insertText 供面板 insert-draft 注入文本;
// takeDraft 供 handoff 随迁取稿
defineExpose({ focus: () => inputRef.value?.focus(), insertText, takeDraft });
```

`WebviewPanel.vue`,`postToIframe`(:154)改造 + `postInit` 后加 load 处理;模板两个 iframe 的 `@load="postInit"` 都改为 `@load="onIframeLoad"`:

```ts
// iframe 就绪闸门(input-handoff spec §C):load 前 postToIframe 暂存(只存最后一条),
// load 时随 init 之后补发;iframe 重建(:key/srcdoc 变)即重置
let loaded = false;
let stashed: Record<string, unknown> | null = null;
watch(() => [props.url, props.v, props.html], () => { loaded = false; });

function onIframeLoad() {
  loaded = true;
  postInit();
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
```

`PanelApp.vue`,`inputBarRef`(:332)旁加 `webviewRef`,模板 WebviewPanel 加 `ref="webviewRef"`:

```ts
const inputBarRef = ref();
const webviewRef = ref<InstanceType<typeof WebviewPanel> | null>(null);
```

`setCurrent`(:124)改为:

```ts
/** 面板内容统一入口:赋值 + 重算焦点 + 上报大脑 + 会话分流 surface 随插件切换;
 *  handoff 草稿随迁:进 coding:studio 瞬间取走译宝条草稿移交聚焦工位 Composer(单向)。
 *  取稿必须先于 current 赋值——handoff 后 bench-bar 移除,InputBar 随之卸载。 */
function setCurrent(v: typeof current.value) {
  const entering = current.value?.panel !== "coding:studio" && v?.panel === "coding:studio";
  const draft = entering ? (inputBarRef.value?.takeDraft?.() ?? "") : "";
  current.value = v;
  focus.value = computeFocus(v);
  if (focus.value) setSurface(`panel:${focus.value.plugin}`);
  void reportPanelContext(focus.value).catch(() => {});
  if (draft) void nextTick(() => webviewRef.value?.postToIframe({ type: "handoff-draft", text: draft }));
}
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + 提交**

```bash
cd app && pnpm test && pnpm build
git add app/src/components/InputBar.vue app/src/components/WebviewPanel.vue app/src/components/PanelApp.vue \
  app/src/components/InputBar.test.ts app/src/components/WebviewPanel.test.ts app/src/components/PanelApp.handoff.test.ts
git commit -m "feat(panel): 草稿随迁——译宝条草稿单向移交聚焦工位 Composer(takeDraft+桥就绪暂存)"
```

### Task 4: studio 侧接收 handoff-draft——Composer.fillDraft + 壳路由

**Files:**
- Modify: `plugins/coding/panel/src/components/Composer.vue`(:165 defineExpose 加 fillDraft)
- Modify: `plugins/coding/panel/src/components/StationView.vue`(:384 composerRef 类型;:508 defineExpose 加 fillDraft)
- Modify: `plugins/coding/panel/src/App.vue`(onHostMessage 路由 + pendingDraft 暂存;StationViewExposed 接口)
- Test: `plugins/coding/panel/src/components/Composer.test.ts`(新建)
- Test: `plugins/coding/panel/src/App.handoff.test.ts`(新建)

**Interfaces:**
- Consumes: Task 3 的宿主消息 `{type:"handoff-draft", text}`
- Produces: `Composer` expose `fillDraft(text: string): void`;`StationView` expose `fillDraft(text: string): void`(Task 5 复用同一组件,接口不变)

- [ ] **Step 1: 写失败测试**

新建 `plugins/coding/panel/src/components/Composer.test.ts`:

```ts
// @vitest-environment happy-dom
// Composer fillDraft(handoff 草稿随迁):空稿直填并聚焦;残稿换行追加(不覆盖用户输入)
import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn(() => Promise.resolve({})),
  onInit: vi.fn(),
  onHostMessage: vi.fn(),
  emitPanelEvent: vi.fn(),
}));

import Composer from "./Composer.vue";

describe("Composer fillDraft", () => {
  it("空稿直填并聚焦;残稿换行追加", () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    const ta = w.find("textarea#prompt").element as HTMLTextAreaElement;
    (w.vm as any).fillDraft("帮我修 bug");
    expect(ta.value).toBe("帮我修 bug");
    expect(document.activeElement).toBe(ta);
    (w.vm as any).fillDraft("顺便补测试");
    expect(ta.value).toBe("帮我修 bug\n顺便补测试");
  });
});
```

新建 `plugins/coding/panel/src/App.handoff.test.ts`:

```ts
// @vitest-environment happy-dom
// handoff 草稿随迁(handoff-draft 宿主消息 → 聚焦工位 Composer 填稿并聚焦)
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

let hostMsg: ((m: any) => void) | null = null;
vi.mock("./lib/bridge", () => ({
  hasBridge: true,
  invoke: vi.fn((m: string) => {
    if (m === "coding.list" || m === "coding.sessions") return Promise.resolve({ sessions: [] });
    if (m === "coding.perm_pending") return Promise.resolve({ pending: [] });
    if (m === "coding.history") return Promise.resolve({ messages: [] });
    return Promise.resolve({});
  }),
  onInit: vi.fn(),
  onHostMessage: vi.fn((cb: any) => { hostMsg = cb; }),
  emitPanelEvent: vi.fn(),
}));

import App from "./App.vue";

describe("handoff 草稿随迁", () => {
  it("handoff-draft → 聚焦工位 Composer 填稿并聚焦", async () => {
    const w = mount(App);
    await flushPromises();
    hostMsg!({ type: "handoff-draft", text: "继续上午的改造" });
    await flushPromises();
    const ta = w.find(".station.focused textarea#prompt").element as HTMLTextAreaElement;
    expect(ta.value).toBe("继续上午的改造");
    expect(document.activeElement).toBe(ta);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd plugins/coding/panel && pnpm vitest run src/components/Composer.test.ts src/App.handoff.test.ts`
Expected: FAIL(fillDraft 不是函数;textarea 未填)

- [ ] **Step 3: 实现**

`Composer.vue`,`clear`/`focus` 之后、`defineExpose`(:165)改为:

```ts
/** handoff 草稿随迁(spec §C):壳侧译宝条草稿填入——已有残稿换行追加(不覆盖用户输入),填完聚焦 */
function fillDraft(text: string) {
  const ta = taEl.value;
  if (!ta) return;
  ta.value = ta.value.trim() ? ta.value.replace(/\s+$/, "") + "\n" + text : text;
  ta.focus();
}
defineExpose({ clear, focus, fillDraft });
```

`StationView.vue`,composerRef 类型(:384)与 defineExpose(:508):

```ts
const composerRef = ref<{ clear: () => void; focus: () => void; fillDraft: (t: string) => void } | null>(null);
```

```ts
// handoff 草稿随迁:壳路由 → 本工位 Composer(壳只管投递,工位内直转)
function fillDraft(text: string) { composerRef.value?.fillDraft(text); }
defineExpose({ state, dockH, onData, bindSession, unbindSession, stop, isBusy: busy, hint, fillDraft });
```

`App.vue`:`StationViewExposed` 接口(:33-41)加一行:

```ts
  fillDraft: (text: string) => void;          // handoff 草稿随迁(壳 → 聚焦工位 Composer)
```

import 行加 onHostMessage:

```ts
import { hasBridge, invoke, onInit, onHostMessage } from "./lib/bridge";
```

`setStationRef` 的 stash flush 块之后(:75 的 `}` 后)加 pendingDraft flush;`onInit` 注册附近加 onHostMessage 注册(同在 setup 顶层,保证早于消息到达):

```ts
// handoff 草稿随迁(input-handoff spec §C):壳译宝条草稿 → 聚焦工位 Composer;
// 工位 ref 未就绪(预挂载窗)暂存一条,登记时若恰是聚焦工位即 flush(同 preMountStash 节奏)
let pendingDraft: string | null = null;
onHostMessage((msg) => {
  if (msg?.type !== "handoff-draft") return;
  const text = String((msg as Record<string, unknown>).text ?? "").trim();
  if (!text) return;
  const r = stationRefs[stations.state.focusId];
  if (r) r.fillDraft(text);
  else pendingDraft = text;
});
```

`setStationRef` 内 stash flush 之后追加:

```ts
  if (pendingDraft && id === stations.state.focusId) {
    const d = pendingDraft;
    pendingDraft = null;
    r.fillDraft(d);
  }
```

- [ ] **Step 4: 跑测试确认通过 + 全量 + 构建 dist + 提交**

```bash
cd plugins/coding/panel && pnpm test && pnpm typecheck && pnpm build
git add plugins/coding/panel/src plugins/coding/panel/dist
git commit -m "feat(coding): studio 接收 handoff-draft——Composer fillDraft + 壳路由聚焦工位"
```

### Task 5: Composer 复刻 InputBar 几何

**Files:**
- Modify: `plugins/coding/panel/src/components/Composer.vue`(模板重排:composer-bar 容器;发送/中断圆钮入输入行;头部注释更新)
- Modify: `plugins/coding/panel/src/style.css`(footer/#prompt/keys-row/button.act 段替换;ctx-row 补 padding)
- Modify: `plugins/coding/panel/src/components/StationView.vue`(status slot 加 `v-if="statusView.text"`)
- Test: `plugins/coding/panel/src/components/Composer.test.ts`(追加结构/发送/@ 回归)

**Interfaces:**
- Consumes: Task 4 的 Composer/StationView 接口(不变)
- Produces: 无新接口(纯视觉;`#send`/`#stop`/`#prompt`/`.at-menu` id 与类名保留,既有交互零改动)

- [ ] **Step 1: 写失败测试**(追加到 `Composer.test.ts`,顶部 import 补 `flushPromises`)

```ts
import { flushPromises, mount } from "@vue/test-utils";
```

```ts
describe("Composer 复刻结构", () => {
  it("composer-bar 容器 + 输入行内嵌发送钮;busy 期中断钮现身于其左", async () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    expect(w.find(".composer-bar").exists()).toBe(true);
    expect(w.find(".composer-row textarea#prompt").exists()).toBe(true);
    expect(w.find(".composer-row #send.cbtn.main").exists()).toBe(true);
    expect(w.find("#stop").exists()).toBe(false);
    await w.setProps({ busy: true });
    expect(w.find(".composer-row #stop.cbtn.stop").exists()).toBe(true);
  });

  it("发送回归:点击 #send 上抛 send(文本+refs)", async () => {
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    const ta = w.find("textarea#prompt");
    (ta.element as HTMLTextAreaElement).value = "hello";
    await w.find("#send").trigger("click");
    expect(w.emitted("send")?.[0]).toEqual(["hello", []]);
  });

  it("@ 补全回归:输入 @ 触发文件菜单(coding.files)", async () => {
    const { invoke } = await import("../lib/bridge");
    (invoke as ReturnType<typeof vi.fn>).mockResolvedValue({ files: [{ rel: "src/main.ts" }] });
    const w = mount(Composer, { props: { busy: false, cwd: "/x", onStop: vi.fn() } });
    const ta = w.find("textarea#prompt");
    (ta.element as HTMLTextAreaElement).value = "看下 @src";
    await ta.trigger("input");
    await flushPromises();
    expect(w.find(".at-menu").exists()).toBe(true);
    expect(w.find(".at-item").text()).toBe("src/main.ts");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd plugins/coding/panel && pnpm vitest run src/components/Composer.test.ts`
Expected: FAIL(`.composer-bar` 不存在)

- [ ] **Step 3: 实现**

`Composer.vue` 头部注释(:1-18)中「多工位」行之后追加一行:

```ts
// 复刻宿主 InputBar 几何(handoff spec §D):composer-bar 圆角玻璃容器,
// 发送/中断圆钮入输入行(InputBar mic/send 槽位),keys-row 退役;样式源头 InputBar.vue,改版需同步。
```

模板(:168-205)整体替换为:

```html
<template>
  <!-- 复刻宿主 InputBar(.bar)的圆角容器:chips 行 → 状态细行(有内容才占位)→
       输入行(textarea + 中断/发送圆钮)→ ctx 家具细行;.prompt-wrap 是 @ 菜单定位锚 -->
  <div class="composer-bar">
    <div class="prompt-wrap">
      <AtRefsChips :refs="refs" @remove="removeRef" />
      <div class="status-line"><slot name="status" /></div>
      <div class="composer-row">
        <textarea
          id="prompt"
          ref="taEl"
          placeholder="输入消息，@ 引用文件…"
          rows="1"
          @input="onInput"
          @keydown="onKeydown"
          @compositionend="lastCompEnd = Date.now()"
          @paste="onPaste"
        ></textarea>
        <button v-if="busy" id="stop" class="cbtn stop" type="button" title="中断当前运行(esc)" :disabled="stopArmed" @click="onStop">
          <svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5" /></svg>
        </button>
        <button id="send" class="cbtn main" type="button" title="发送(↵) · 换行(⇧↵) · @ 引用文件" @click="doSend">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" /></svg>
        </button>
      </div>
      <div v-if="at.open" id="at-menu" class="at-menu">
        <div v-if="!at.items.length" class="at-empty">无匹配</div>
        <div
          v-for="(rel, i) in at.items"
          :key="rel"
          class="at-item"
          :class="{ sel: i === at.idx }"
          @mousedown.prevent="atInsert(rel)"
        >{{ rel }}</div>
      </div>
    </div>
    <!-- 家具细行(诚实差异区):cwd chip + 浮层 / mode pill / 引擎 chip + picker,壳 slot 注入 -->
    <div class="ctx-row"><slot name="ctx" /></div>
  </div>
</template>
```

`StationView.vue` status slot(:599-601)加 v-if(StatusLine 空文本也渲染 #status 占位,`有内容才占位`靠这里判):

```html
        <template #status>
          <StatusLine v-if="statusView.text" :text="statusView.text" :spin="statusView.spin" :err="statusView.err" />
        </template>
```

`style.css` 替换「底栏」整段(现 :444-463 注释+footer+#prompt 块)为:

```css
/* ---- Composer(handoff 复刻宿主 InputBar;样式源头 app/src/components/InputBar.vue .bar,
   改版需同步——沙箱吃不到 tokens.css,字面值写死,同 :root 注释先例)----
   footer 透明让位,圆角玻璃条浮在工位底(视觉=壳译宝条原槽位) */
footer { flex: none; background: transparent; padding: 0 10px 10px; }
.composer-bar {
  display: flex; flex-direction: column; align-items: stretch; box-sizing: border-box;
  min-height: 46px; padding: 5px 5px 5px 7px; border-radius: 24px;
  background: rgba(255,255,255,0.94);
  -webkit-backdrop-filter: saturate(140%) blur(20px); backdrop-filter: saturate(140%) blur(20px);
  border: 1px solid rgba(40,60,90,0.10);
  box-shadow: 0 1px 2px rgba(0,0,0,0.04), 0 6px 18px rgba(40,60,90,0.10);
  transition: border-color .15s ease-out, box-shadow .15s ease-out;
}
.composer-bar:focus-within { border-color: var(--accent); outline: 2px solid var(--accent-soft); outline-offset: 1px; }
/* 输入行:textarea 无框透明(描边由 .composer-bar 承担),发送/中断 34px 圆钮(InputBar mic/send 槽位) */
.composer-row { display: flex; gap: 5px; align-items: center; }
#prompt {
  flex: 1; min-width: 0; display: block;
  border: none; background: transparent; -webkit-appearance: none; appearance: none; box-shadow: none;
  font: 14px/1.45 var(--sans); color: var(--text);
  outline: none; resize: none; overflow-x: hidden; overflow-y: auto;
  padding: 7px 0; max-height: 140px;
}
#prompt::placeholder { color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#prompt::-webkit-scrollbar { width: 4px; }
#prompt::-webkit-scrollbar-thumb { background: var(--faint); border-radius: 2px; }
.cbtn { width: 34px; height: 34px; flex-shrink: 0; border-radius: 50%; border: none; cursor: pointer;
        display: grid; place-items: center; transition: all .15s ease-out; }
.cbtn svg { width: 14px; height: 14px; }
.cbtn.main { background: var(--accent); color: #fff; box-shadow: 0 1px 2px rgba(77,144,196,0.25); }
.cbtn.main:hover { background: var(--accent-deep); }
.cbtn.main:active { transform: scale(.97); }
.cbtn.stop { background: var(--red-bg); color: var(--red); }
.cbtn.stop:hover { filter: brightness(.97); }
.cbtn.stop:disabled { opacity: .5; cursor: default; }
/* 状态细行:输入框上方淡小字,有内容才占位(StationView status slot 空态不渲染) */
.status-line { font-size: 11px; color: var(--faint); padding: 0 6px 2px; }
.status-line:empty { display: none; }
```

`.ctx-row`(:481)补 padding(卡内细行,左右与输入行对齐):

```css
.ctx-row { position: relative; display: flex; align-items: center; gap: 6px; padding: 2px 4px 0; }
```

删除 `button.act` 块(现 :663-671,含「段③操作钮」注释;`button.ghost` 保留——HistoryOverlay 的 `lc-act ghost` 共用)与 `.keys-row`(:678,模板已无引用)。`#status`(:679-683)保留(StatusLine 仍在用,现嵌于 .status-line)。

- [ ] **Step 4: 跑测试确认通过 + 全量 + typecheck + 构建 dist + 提交**

```bash
cd plugins/coding/panel && pnpm test && pnpm typecheck && pnpm build
git add plugins/coding/panel/src plugins/coding/panel/dist
git commit -m "refactor(coding): Composer 复刻宿主 InputBar 几何——圆角玻璃容器+发送/中断圆钮入输入行"
```

### Task 6: 四闸回归 + 视觉验收

**Files:** 无新增(验证与文档)

- [ ] **Step 1: 四闸**

```bash
cd app && pnpm test && pnpm build
cd plugins/coding/panel && pnpm test && pnpm typecheck && pnpm build
cd sidecar && uv run pytest -x -q
```

Expected: 全绿(sidecar 无改动,跑闸防共享文件误碰——`app/src/shared/bridge.js` 被 Rust include_bytes,本方案未动它;Rust 侧无改动,cargo 不跑)

- [ ] **Step 2: 视觉自查**

用截图自查器(若 `/tmp/panel-shot.mjs` 还在)或 `pnpm tauri dev` 真机过两态:handoff 前(bench-bar 在)+ handoff 后(Composer 落槽位、标题栏团子)。重点看:圆角/高度/发送钮与译宝条同规格;ctx-row 家具细行不抢戏;空工位居中指引不被 Composer 遮。

- [ ] **Step 3: 验收标准对照(spec §验收标准 A-E 逐条勾)**

- [ ] **Step 4: 台账 + 提交收尾**

`docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md` 输入条行(:29)末尾补一句:`(2026-08-19 input-handoff:面板窗内译宝条让位,Composer 复刻接管,见 specs/2026-08-19-input-handoff-design.md)`。提交:

```bash
git add -A
git commit -m "docs: 输入条 handoff 落地——R4 spec 输入条行补交叉引用"
```
