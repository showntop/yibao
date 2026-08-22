# R4 阶段二:coding 前端重写(单工位能力对齐,退役 chat.html)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 chat.html(2943 行 vanilla JS)的全部能力重写为 `plugins/coding/panel/` 下的多文件 Vue 工程(coding:studio),单工位能力完全对齐现状,然后切换 panel 引用(coding:chat → coding:studio)并退役 chat.html。

**Architecture:** 核心逻辑收进**纯 TS  stores/libs**(事件→渲染模型归约器、发送状态机、markdown/diff/format 纯函数),node 环境 vitest 全覆盖;Vue SFC 只做薄渲染。事件协议与 API 面以两份侦察为准(见「协议基准」节,来自 chat.html 行为清单与 coding.py 协议清单)。样式从 chat.html 指定行段原样移植(视为引用,不算占位)。

**Tech Stack:** Vue 3.5(SFC,构建期预编译)、TS、vitest(node 环境)、marked + dompurify + highlight.js(正常 npm 依赖,替代 vendor 内联)。

**Spec:** docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md(本计划覆盖落地顺序第 2 步 + 阶段一终审跟进项)

**计划约定(防评审误判):** 组件任务给出完整 `<script setup>`、props/emits 接口与逐条行为清单;CSS 以「移植 chat.html:起止行」引用方式给出,实现者原样复制后可按 BEM 现状微调类名,不视为占位。store/lib 任务给完整代码。

## Global Constraints

- 全程在 worktree `/Users/denny/Work/yibao/.worktrees/r4-stage1`(branch feat/r4-module-panel-runtime)施工;sidecar venv 是 worktree 独立环境(uv sync 重建过,editable 指向 worktree src)
- 测试命令:插件面板 `cd plugins/coding/panel && pnpm test`(vitest);sidecar `cd sidecar && .venv/bin/pytest`;desktop `cd desktop && pnpm test && pnpm build`;Rust `cd desktop/src-tauri && cargo test`
- 中文注释/提交信息,conventional 前缀;每 Task 独立 commit
- **桥契约不变**:window.yibao.invoke/onInit/onMessage/emitEvent;takeover-input/takeover-stop 消息、takeover-state 上报形状 `{state, session}` 与现状逐字一致(PanelApp 依赖)
- **协议容缺红线**(侦察 §1.3/§2):panel_data.data.agent 可缺(rewind/stop 兜底事件);done.usage 可整体缺、cost_usd 恒 null(codex);history fallback 消息无 uuid 键;drivers 的 claude-code 项无 version 键;codex 会话无 permission/user_msg/rewind_ok 事件;decide 对已结局 rid 报「不存在或已超时」是正常幂等
- markdown 渲染任何异常必须静默降级纯文本,绝不上抛(会被 onInit 回调的 try/catch 吞掉后续终态处理导致流式卡死)
- 行为对齐基准是 chat.html 现状(侦察报告 13 项),包括其中的「坑」:用户气泡等 user_msg 回流+1.5s 兜底原地升级、pendingTurnEnded 秒败竞态、resumeSession 必须 delete discardedSessions[sid]、IME 双守卫、autoReplay 让位语义、handoff 的 coding.start 不带 mode/agent
- 旧 srcdoc 链路(tools.html/editor.html/gen 面板)零回归;coding:wall 本阶段不退役(阶段五)

## 协议基准(重写对齐用,摘自侦察,如需全文看 chat.html 与 coding.py)

事件(kind → 字段):text_delta{text} / thinking{text≤500} / tool_use{tool,input} / file_edit{tool,path?,old?,new?} / tool_result{text≤800,is_error} / user_msg{uuid,text}(仅 CC) / permission_request{rid=perm_<sid>_<n>,tool,input}(仅 CC) / permission_done{rid,allow} / rewind_ok{text?} / done{usage?} / stopped{text?} / error{text}。信封:panel_data{panel,data:{session_id,agent?,event}};attach 载荷:{session_id,attach:true,agent?}(无 event 键)。

方法:start{cwd,prompt,agent?,mode?,source?,background?}→{session_id};send{id,prompt,mode?};stop{id};mode{id,mode};rewind{id,user_msg_id}→{ok?};history{id}→{messages:[{role,text,uuid?}],cwd};list{}→{sessions:[SessionRow+live]};drivers{}→{drivers:[{id,available,version?}]};last_sessions{cwd}→{cc?,codex?};attach_cc{cc_session_id,cwd};attach_codex{session_id};handoff_list{cwd}→{sessions:[{session_id,timestamp,first_line}]};handoff_brief{session_id,cwd}→{brief,incomplete};session_brief{id,target}→{brief};files{cwd,q}→{files:[{path,rel}]};decide{rid,allow}。native:pick_folder{}→路径|null;native:save_attachment{data,ext}→绝对路径。桥 resolve 的是 result.data 本体(直读 r.sessions/r.drivers,非 r.data.x)。

---

### Task 1: 阶段一终审跟进项 + 面板测试基建

**Files:**
- Modify: `desktop/src-tauri/src/plugin_proto.rs`(CSP 按 pid 收窄 + form-action)
- Modify: `desktop/src/lib/webview-source.ts`(导出共享类型)+ `desktop/src/components/PanelApp.vue`、`HomePlugins.vue`、`PeekSurface.vue`(引用共享类型替换三处内联)
- Modify: `scripts/panel-build/package.json`(vue pin 到 vendored runtime 同版)+ 重新 vendor 对齐
- Create: `plugins/coding/panel/package.json`、`plugins/coding/panel/vitest.config.ts`、`plugins/coding/panel/tsconfig.json`
- Test: `desktop/src-tauri/src/plugin_proto.rs` 内 #[cfg(test)] 增补;`desktop/src/lib/webview-source.test.ts` 不动

**Interfaces:**
- Produces: `WebviewPayload` 共享类型(三处宿主组件统一引用);CSP 形如 `script-src yibao-plugin://<pid> 'unsafe-inline'`(按 pid 收窄);面板项目 vitest 可跑(`pnpm --dir plugins/coding/panel test`)

- [ ] **Step 1: Rust 失败测试——CSP 按 pid 收窄**

plugin_proto.rs 测试模块加:

```rust
#[test]
fn csp_is_scoped_per_pid() {
    let root = fixture_root();
    let r = handle(&req("yibao-plugin://demo/panel/dist/index.html"), &root);
    let body = String::from_utf8_lossy(r.body()).into_owned();
    assert!(body.contains("script-src yibao-plugin://demo 'unsafe-inline'"));
    assert!(body.contains("form-action 'none'"));
    assert!(!body.contains("script-src yibao-plugin:"));
}
```

Run: `cd desktop/src-tauri && cargo test plugin_proto` → 新测试 FAIL

- [ ] **Step 2: 实现——CSP_META 从常量改函数**

plugin_proto.rs:删 `const CSP_META`,改:

```rust
/// CSP 按 pid 收窄到本插件源;/__yibao__/ SDK 在任意 pid 下可服务,故同属本源可达。
/// form-action 禁表单外发;connect-src 'none' 断网络(XHR/fetch/ws)。
fn csp_meta(pid: &str) -> String {
    format!(
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src yibao-plugin://{pid} 'unsafe-inline'; style-src yibao-plugin://{pid} 'unsafe-inline'; img-src yibao-plugin://{pid} data:; font-src yibao-plugin://{pid} data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
    )
}
```

`inject_sdk(html: &str)` 改签名 `inject_sdk(html: &str, pid: &str)`,`format!("{}{}{}", csp_meta(pid), IMPORTMAP, BRIDGE_TAG)`;handle 内调用点同步传 pid。既有测试 `injects_*`、`serves_sdk_and_injects_html` 中断言 `Content-Security-Policy` 的继续成立(子串仍在)。

Run: `cargo test plugin_proto` → 全绿(6 旧 + 1 新)

- [ ] **Step 3: WebviewPayload 共享类型**

`desktop/src/lib/webview-source.ts`:文件已有 `export interface WebviewPayload`(若已是 export 则跳过)。三处宿主组件把内联 `webview: { html?: string; url?: string; v?: number } | null` 替换为 `webview: WebviewPayload | null` 并 import。PanelApp.vue 两处(:34/:362 附近)、HomePlugins.vue 两处、PeekSurface.vue 一处,共五处。

Run: `cd desktop && pnpm build && pnpm test` → 绿

- [ ] **Step 4: panel-build 的 vue 与 vendored runtime 对齐**

```bash
grep '"version"' desktop/node_modules/vue/package.json   # 记下版本,如 3.5.40
```

`scripts/panel-build/package.json` 的 `vue` 依赖改为该精确版本(去 `^`);`cd scripts/panel-build && pnpm install`;重新 vendor:`cp desktop/node_modules/vue/dist/vue.runtime.esm-browser.prod.js desktop/src-tauri/resources/sdk/vue.esm-browser.js`(若版本已一致则跳过,`cmp` 验证)。

- [ ] **Step 5: 面板测试基建**

新建 `plugins/coding/panel/package.json`:

```json
{
  "name": "yibao-panel-coding",
  "private": true,
  "type": "module",
  "scripts": { "test": "vitest run", "build": "node ../../../scripts/panel-build/build.mjs coding" },
  "dependencies": {
    "dompurify": "^3.2.4",
    "highlight.js": "^11.11.1",
    "marked": "^15.0.0",
    "vue": "3.5.40"
  },
  "devDependencies": {
    "typescript": "^5.6.0",
    "vitest": "^4.1.10"
  }
}
```

(vue 版本与 Step 4 查得的 vendored 版本一致。)

新建 `plugins/coding/panel/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
```

新建 `plugins/coding/panel/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

根 `.gitignore` 加一行:`plugins/*/panel/node_modules/`(面板依赖产物不入库)。

装依赖:`cd plugins/coding/panel && pnpm install`
冒烟:放一个 `src/smoke.test.ts`(`expect(1).toBe(1)`)跑通后删掉,只验证基建。

- [ ] **Step 6: 全量闸门 + commit**

Run: `cd desktop/src-tauri && cargo test` 绿;`cd desktop && pnpm test && pnpm build` 绿

```bash
git add desktop/src-tauri/src/plugin_proto.rs desktop/src-tauri/resources/sdk/vue.esm-browser.js desktop/src/lib/webview-source.ts desktop/src/components/PanelApp.vue desktop/src/components/HomePlugins.vue desktop/src/components/PeekSurface.vue scripts/panel-build plugins/coding/panel/package.json plugins/coding/panel/vitest.config.ts plugins/coding/panel/tsconfig.json plugins/coding/panel/pnpm-lock.yaml
git commit -m "chore: 终审跟进——CSP 按 pid 收窄 + form-action、WebviewPayload 共享类型、vue 版本对齐、面板测试基建"
```

---

### Task 2: 协议类型 + 会话 store(事件归约器与发送状态机)

**Files:**
- Create: `plugins/coding/panel/src/lib/types.ts`
- Create: `plugins/coding/panel/src/lib/bridge.ts`
- Create: `plugins/coding/panel/src/stores/session.ts`
- Test: `plugins/coding/panel/src/stores/session.test.ts`

**Interfaces:**
- Consumes: 协议基准(全局约束节)
- Produces(后续任务全部依赖):
  - `types.ts`:`CodingEvent`、`Usage`、`PanelData`、`SessionRow`、`AgentName`、`DriverInfo`、`HistoryMessage`
  - `bridge.ts`:`invoke<T>(method: string, params?: Record<string, unknown>): Promise<T>`、`onInit(cb)`、`onHostMessage(cb)`、`emitPanelEvent(name, payload)`、`hasBridge: boolean`
  - `session.ts`:`createSessionStore(deps)` 返回 store,字段与方法见下方完整代码。渲染模型 `RenderItem` 联合类型供组件消费。

- [ ] **Step 1: types.ts(完整代码)**

```ts
// coding 面板协议类型(与 plugins/coding/skills 的 _runner.py/_codex_runner.py normalize 输出对齐)。
// 容缺红线:agent 在 rewind/stop 兜底事件缺失;usage 可整体缺;cost_usd 恒 null(codex);
// history fallback 消息无 uuid;codex 会话无 user_msg/permission_*/rewind_ok。
export type AgentName = "claude-code" | "cc" | "codex";

export interface Usage {
  duration_ms?: number;
  cost_usd?: number | null;
  input_tokens?: number;
  output_tokens?: number;
}

export type CodingEvent =
  | { kind: "text_delta"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool_use"; tool: string; input: Record<string, unknown> }
  | { kind: "file_edit"; tool: string; path: string | null; old: string | null; new: string | null }
  | { kind: "tool_result"; text: string; is_error: boolean }
  | { kind: "user_msg"; uuid: string; text: string }
  | { kind: "permission_request"; rid: string; tool: string; input: Record<string, unknown> }
  | { kind: "permission_done"; rid: string; allow: boolean }
  | { kind: "rewind_ok"; text?: string }
  | { kind: "done"; usage?: Usage }
  | { kind: "stopped"; text?: string }
  | { kind: "error"; text: string };

export interface PanelData {
  session_id?: string;
  agent?: string;
  event?: CodingEvent;
  attach?: boolean;
}

export interface SessionRow {
  id: string; agent: string; cwd: string; prompt: string;
  status: "running" | "done" | "stopped" | "failed";
  created_at: number; finished_at: number; cc_session_id: string;
  source: string; mode: string;
  live: "waiting" | "running" | "idle";
}

export interface DriverInfo { id: string; available: boolean; version?: string | null }
export interface HistoryMessage { role: "user" | "assistant" | "marker"; text: string; uuid?: string }
```

- [ ] **Step 2: bridge.ts(完整代码)**

```ts
// window.yibao 桥的 TS 封装。桥本身由宿主注入(srcdoc 内联或 yibao-plugin:// 协议层),
// 这里只做类型化与空值守卫;无桥时 hasBridge=false(设计预览模式),invoke 一律 reject。
declare global {
  interface Window {
    yibao?: {
      invoke(method: string, params?: Record<string, unknown>): Promise<unknown>;
      onInit(cb: (data: unknown, msg?: Record<string, unknown>) => void): void;
      onMessage?(cb: (msg: Record<string, unknown>) => void): void;
      emitEvent(name: string, payload: unknown): void;
    };
    YIBAO_BRIDGE_VERSION?: number;
  }
}

export const hasBridge = !!(
  typeof window !== "undefined" && window.yibao && window.yibao.invoke && window.yibao.onInit
);

// 桥 resolve 的是 result.data 本体——直读返回,不再有 .data 层
export async function invoke<T = Record<string, unknown>>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (!hasBridge) return Promise.reject(new Error("桥不可用"));
  return window.yibao!.invoke(method, params) as Promise<T>;
}

export function onInit(cb: (data: unknown, msg?: Record<string, unknown>) => void): void {
  if (hasBridge) window.yibao!.onInit(cb);
}

export function onHostMessage(cb: (msg: Record<string, unknown>) => void): void {
  if (hasBridge && window.yibao!.onMessage) window.yibao!.onMessage(cb);
}

export function emitPanelEvent(name: string, payload: unknown): void {
  if (hasBridge) window.yibao!.emitEvent(name, payload);
}
```

- [ ] **Step 3: 失败测试 session.test.ts(先写,覆盖下列行为清单)**

测试基建:用假 `invoke`(可编程返回/挂起)+ 同步假定时器(`setTimer` 收集回调手动触发)。骨架与三个代表用例给全,其余按清单同构展开:

```ts
import { describe, expect, it, vi } from "vitest";
import { createSessionStore, type SessionDeps } from "./session";

function makeDeps(invokeImpl?: SessionDeps["invoke"]) {
  const timers: Array<{ fn: () => void; ms: number; cleared: boolean }> = [];
  const reports: string[] = [];
  const invoke = invokeImpl ?? vi.fn(async () => ({ session_id: "s1" }));
  const deps: SessionDeps = {
    invoke,
    report: (st) => reports.push(st),
    setTimer: (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t as never; },
    clearTimer: (t) => { (t as (typeof timers)[0]).cleared = true; },
    userEchoFallbackMs: 1500,
  };
  return { deps, timers, reports, invoke };
}
const ev = (e: Parameters<ReturnType<typeof createSessionStore>["applyEvent"]>[0]) => e;

describe("事件归约", () => {
  it("text_delta 连到同一气泡;非文本事件切断后再起新气泡", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "text_delta", text: "你" }));
    s.applyEvent(ev({ kind: "text_delta", text: "好" }));
    expect(s.state.items).toHaveLength(1);
    expect(s.state.items[0]).toMatchObject({ type: "assistant", raw: "你好", done: false });
    s.applyEvent(ev({ kind: "tool_use", tool: "Bash", input: { command: "ls" } }));
    s.applyEvent(ev({ kind: "text_delta", text: "完" }));
    expect(s.state.items).toHaveLength(3);
    expect(s.state.items[0]).toMatchObject({ done: true });
    expect(s.state.items[2]).toMatchObject({ type: "assistant", raw: "完" });
  });

  it("tool_result 挂最近工具卡;file_edit 切断配对", () => {
    const { deps } = makeDeps();
    const s = createSessionStore(deps);
    s.applyEvent(ev({ kind: "tool_use", tool: "Bash", input: {} }));
    s.applyEvent(ev({ kind: "tool_result", text: "ok", is_error: false }));
    const card = s.state.items[0];
    expect(card).toMatchObject({ type: "tool", results: [{ text: "ok", isError: false }] });
    s.applyEvent(ev({ kind: "file_edit", tool: "Edit", path: "a.ts", old: "x", new: "y" }));
    s.applyEvent(ev({ kind: "tool_result", text: "done", is_error: false }));
    expect((card as { results: unknown[] }).results).toHaveLength(1); // 不错挂
  });

  it("send 秒败竞态:终态先于 invoke 返回 → 不进 streaming", async () => {
    let release!: (v: { session_id: string }) => void;
    const invoke = vi.fn(() => new Promise<{ session_id: string }>((res) => { release = res; }));
    const { deps } = makeDeps(invoke as never);
    const s = createSessionStore(deps);
    const p = s.send("/tmp", "hi", "acceptEdits", "claude-code");
    s.applyEvent(ev({ kind: "error", text: "boom" })); // 终态抢跑
    release({ session_id: "s1" });
    await p;
    expect(s.state.streaming).toBe(false);
    expect(s.state.ended).toBe("error");
  });
});
```

其余用例覆盖以下完整行为清单(每条一个测试):

1. text_delta 连到同一 assistant 气泡(dataset.raw 累积),非文本事件切断气泡(再 text_delta 起新气泡)
2. tool_use 产生折叠工具卡;随后 tool_result 挂到该卡(计数/错误标);file_edit 切断 lastToolCard(后续 tool_result 不错挂)
3. permission_request 产生等待卡;permission_done 收敛(allow→「✓ 已允许」/deny→「✗ 已拒绝」),waiting 标志随 permission_request 置位、permission_done 复位
4. user_msg 产生用户气泡(带 uuid);若有文本匹配的兜底气泡则原地升级(不双份)
5. done/stopped/error 终态:streaming 复位、ended 标记、usage 累加(done.usage 缺/cost_usd null 不炸)
6. 异会话事件在 currentSession 已设时被过滤;未设时放行(首块早于 invoke 返回的竞态);discardedSessions 过滤
7. send:start 路由(无 currentSession→coding.start;有→coding.send);sending/streaming 状态流转;1.5s 兜底用户气泡;pendingTurnEnded 秒败竞态(invoke 返回前终态已到,不进 streaming)
8. stop:coding.stop 调用;stopped 事件经 onSessionEnded 复位
9. resumeSession:historyToItems 转换(user/assistant/marker)、清 log、delete discardedSessions[sid](锁死回归)、skipIfEmpty 返回 0、requestResume 防重入(只留最新 pending)
10. newChat:清态 + currentSession 进 discarded
11. takeover:takeover-input busy 时入队、空闲直发;drainTakeoverQueue 在终态泄放(stopped 清空队列);takeover-stop 调 stop
12. reportState:仅 takeover 态经 emitPanelEvent 上报 {state, session}

- [ ] **Step 4: 实现 session.ts(完整代码)**

```ts
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
```

(注:`pendingTurnEnded` 秒败竞态在 onSessionEnded 首行置位——终态到时若仍在 sending 窗,说明 invoke 尚未返回,send 的 then 分支见此标记直接 return 不进 streaming。)

- [ ] **Step 5: 跑测试至全绿**

Run: `cd plugins/coding/panel && pnpm test`
Expected: 行为清单 12 条全绿

- [ ] **Step 6: Commit**

```bash
git add plugins/coding/panel/src/lib/types.ts plugins/coding/panel/src/lib/bridge.ts plugins/coding/panel/src/stores/session.ts plugins/coding/panel/src/stores/session.test.ts
git commit -m "feat: studio 会话 store——事件归约器 + 发送状态机(行为对齐 chat.html)"
```

---

### Task 3: 渲染纯函数库(markdown / diff / format)

**Files:**
- Create: `plugins/coding/panel/src/lib/markdown.ts`
- Create: `plugins/coding/panel/src/lib/diff.ts`
- Create: `plugins/coding/panel/src/lib/format.ts`
- Test: 对应三个 `.test.ts`

**Interfaces:**
- Produces:
  - `mdToHtml(raw: string): string | null`(marked gfm+breaks;异常/缺库 → 返回 null 表示降级纯文本;**不消毒**——消毒在组件 v-html 前完成)
  - `sanitizeHtml(html: string): string`(DOMPurify.sanitize,`FORBID_ATTR:["style"]`;组件 v-html 唯一入口)
  - `createMdThrottler(schedule: (fn: () => void) => void, minInterval?: number)` → `{ request(render: () => void): void; flush(): void }`(150ms 节流 + trailing flush;rAF 由调用方注入)
  - `lcsLines(oldText: string, newText: string): DiffLine[]`(`DiffLine = {type:"add"|"del"|"ctx", text:string}`);`multiEditDiff(newJson: string): {segments: {head: string; lines: DiffLine[]}[]} | null`(MultiEdit 的 JSON edits 逐段 LCS,解析失败 null)
  - `fmtTok(n)`、`fmtCost(n)`、`relTime(now, ts)`、`humanFirstLine(text)`(errbar 摘要:跳空行/`<` 开头协议行/堆栈行,剥行内标签,兜底文案)

- [ ] **Step 1: 失败测试**

- markdown:基本 gfm(标题/代码块/行内码)、`breaks:true` 换行、原始 HTML 保留(消毒在组件)、marked 抛错时返回 null
- throttler:间隔内合并多次 request 只跑一次、trailing 补跑、flush 立即跑
- diff:`lcsLines("a\nb", "a\nc")` → ctx a / del b / add c(顺序按 LCS);空 old 全 add;空 new 全 del;`multiEditDiff` 解析 `{"edits":[{"old_string":"x","new_string":"y"}]}` 出段,坏 JSON 返回 null
- format:fmtTok(999→"999"、1500→"1.5k"、2300000→"2.3M");relTime 分钟/小时/天;humanFirstLine 跳过规则与兜底

lcsLines 移植 chat.html:1311-1336 的 DP 实现(原样翻成 TS);format 各函数对齐 :870-937、:1181-1191、wall subtitle 的相对时间语义。

- [ ] **Step 2: 实现三库,测试全绿,commit**

```bash
git add plugins/coding/panel/src/lib/markdown.ts plugins/coding/panel/src/lib/diff.ts plugins/coding/panel/src/lib/format.ts plugins/coding/panel/src/lib/*.test.ts
git commit -m "feat: studio 渲染纯函数库(markdown/diff/format)"
```

---

### Task 4: 渲染组件 + App 骨架(消息流/工具卡/diff 卡/审批卡/marker/errbar/runpill)

**Files:**
- Create: `plugins/coding/panel/src/components/MessageList.vue`、`AssistantBubble.vue`、`ToolCard.vue`、`FileEditCard.vue`、`PermCard.vue`、`MarkerLine.vue`、`ErrBar.vue`、`RunPill.vue`
- Modify: `plugins/coding/panel/src/App.vue`(替换骨架:接 store + MessageList;头部与 Composer 占位到 Task 5/6 接入)
- Test: `plugins/coding/panel/src/lib/render-model.test.ts`(如归约有遗漏在这补;组件不做 DOM 测试,走真机验收)

**Interfaces:**
- Consumes: Task 2 的 `RenderItem`/`createSessionStore`、Task 3 的 `mdToHtml/sanitizeHtml/lcsLines/multiEditDiff/fmt*`
- Produces: 完整消息流渲染(Task 5+ 往 App 加头部/Composer)

**行为清单(逐条对齐 chat.html,实现者核对):**
- AssistantBubble:流式期 raw 累积,150ms 节流重渲(用 createMdThrottler);`raw>50000` 流式期纯文本、done 终渲解除;think-block 在顶部(渲染前摘出、渲后按序插回——Vue 下用计算属性分离 thinking/raw 即可天然保序,无需 DOM 移植);`.done` 时尾部 ✓;markdown 管线异常 → 纯文本兜底
- 代码块增强:pre 外套 codeblock,头条左语言标签右复制钮(复制后 ✓ 1.2s);hljs 仅 `getLanguage(lang)` 命中才 highlightElement;复制 navigator.clipboard 失败退化 execCommand
- ToolCard:默认折叠单行(chevron ▸ + 图标 + 工具名 + 意图摘要 + tally);首展开才显示 input JSON pretty;tool_result ≤8 行直显,>8 行 details 收起「… +N lines」;is_error → 卡 `.err` + tally ✗;tally 规则:Grep→`N matches`、Read/Bash→`N lines`、其他不计
- FileEditCard:默认展开,头行 `✎ path +a -d tool`(title 全路径);体:old+new→lcsLines;仅 new 且 tool=MultiEdit→multiEditDiff 逐段(段头「第 N 处」),null 退纯文本;仅 new(Write)→全文 add;无 old/new(codex file_change)→note 说明
- PermCard(只读镜像):等待态 `🔐 「<tool>」需要许可` + input JSON(>300 截断)+ `⏳ 等待审批…在顶部确认条或主屏收件箱处理` + 琥珀脉冲边;done 收敛单行 `✓ 已允许 <verb>`/`✗ 已拒绝`;PERM_VERBS 映射(Bash→运行命令,Edit/MultiEdit/Write→保存修改,WebFetch→抓取网页,WebSearch→联网搜索,NotebookEdit→保存 notebook,默认「允许」)
- MarkerLine:居中灰小字 pill,err 红态
- ErrBar:摘要(humanFirstLine)+ 详情 toggle;出现/消失重算 RunPill bottom
- RunPill:fixed 底部居中,`bottom = footer高 + 10 + errbar 高`;sending 期可见「提交中…」;streaming 秒表 `prefix · Ns`;rp-stop 仅在 streaming 后解锁;done 行「✓ 完成 · Ns · X tok · $Y」各段 isFinite 守御
- 样式:移植 chat.html :70-100(:root 变量)、:220-228(hljs 主题)、:342-364(perm)、:452(480px 媒体查询)及各卡样式段;消息带限宽 760px 居中、气泡/卡 max-width 85%
- App:`onInit` → `store.handleData`;`body.takeover` 类驱动隐藏输入区(Task 8 完整接线)

- [ ] **Step 1-3:实现组件与 App,`pnpm build`(面板构建)通过,`pnpm test` 绿,commit `feat: studio 渲染组件与 App 骨架(消息流/工具卡/diff/审批镜像)`**

---

### Task 5: Composer(输入框 + @ chips + 文件补全 + 粘贴截图 + IME + 排队)

**Files:**
- Create: `plugins/coding/panel/src/components/Composer.vue`、`AtRefsChips.vue`
- Create: `plugins/coding/panel/src/lib/refs.ts`(纯函数)+ `refs.test.ts`
- Modify: `plugins/coding/panel/src/App.vue`(接入)

**Interfaces:**
- Produces: `refs.ts`:`composeRefs(refs: string[]): string`(`\n\n引用文件:\n@a\n@b`)、`matchAtQuery(textBeforeCaret: string): {start: number; query: string} | null`(正则 `/@([\w\-./]*)$/`)、`basename(p: string): string`
- Composer props:`{ busy: boolean; cwd: string }`;emits:`send(text: string, refs: string[])`、`stop`(takeover 不经过 Composer——store.takeoverInput 直驱,takeover 态 Composer 本就隐藏)

**行为清单:**
- Enter 发送、⇧↵ 换行;IME 双守卫(`isComposing` + compositionend 50ms 窗)
- @ 触发补全:capture 阶段 keydown(Esc 关菜单 stopPropagation、↑↓ 循环、Enter 插入带 IME 守卫);`coding.files {cwd, q}` 查询,最多 12 条,无匹配显「无匹配」;插入成 chip 不留在文本
- chips 行:basename 显示、title 全路径、× 删除、空隐藏;发送成功后清空
- 粘贴图片:clipboardData.items 找 image/ → FileReader dataURL → `native:save_attachment {data(base64 逗号后), ext}` → 返回绝对路径入 chips + 状态行「已引用截图」;失败状态行报错;非图片走默认
- busy(sending||streaming)时 send 禁用,输入不锁(现状 takeoverQueue 语义在 store;Composer 自身 busy 只反映)
- esc 中断经 App 全局 keydown(Task 8 接)

- [ ] **Step 1-3:refs 纯函数 TDD;组件实现;构建 + 测试绿;commit `feat: studio Composer(@ chips/文件补全/粘贴截图/IME)`**

---

### Task 6: 头部控件(cwd chip + 浮层 / 引擎 chip + picker / mode pill / drivers)

**Files:**
- Create: `plugins/coding/panel/src/components/CwdChip.vue`、`AgentChip.vue`、`ModePill.vue`、`StatusLine.vue`
- Create: `plugins/coding/panel/src/stores/drivers.ts`(+ test)
- Modify: `plugins/coding/panel/src/App.vue`

**行为清单:**
- drivers store:`probe()` 调 coding.drivers;codexAvailable: null/false/true;`curAgent==="codex"` 且不可用强制回 claude-code;探测失败保持 null 按可用呈现;claude-code 项无 version 键容缺
- CwdChip:basename 显示,空态「选择项目目录」;浮层 input Enter/Esc(非组字、stopPropagation);📁 → native:pick_folder;commitCwd:空/同值忽略、running 拒(状态行提示)、setCwd + newChat + coding.list → refreshCwdState(默认引擎记忆:该 cwd 时间倒序首个命中行的 agent;codex 不可用强制 CC)+ 触发 autoReplay(Task 7 接)
- AgentChip:三态(徽标态 ro 显示 curSessAgent/跨引擎待定 sw 虚线显 switchAgent/无会话显 curAgent);codex 不可用且无会话整颗 disabled;四种 title 文案对齐 chat.html:1769-1775;点击 streaming/sending 拦截「会话进行中,先停止再切换引擎」
- picker:fixed 弹层 + backdrop;两项(CC 全能力恒可选/Codex 按可用);当前项 ✓(有会话 = switchAgent||curSessAgent);pickAgent:有会话同引擎清待定、异引擎置 switchAgent + 状态行提示;无会话设 curAgent
- ModePill:acceptEdits⇄plan,plan 态强调色;有活动会话时 coding.mode{id,mode}(catch 静默)
- StatusLine:状态行 + 错误红态
- 互收逻辑:任一 picker/浮层开时关其他;esc 优先级:agent-picker→history→handoff→stop(Task 7/8 接全)

- [ ] **Step 1-3:drivers store TDD;组件实现;构建 + 测试绿;commit `feat: studio 头部控件(cwd/引擎/mode/状态行)`**

---

### Task 7: handoff 双路径 + rewind + 接续浮层 + autoReplay

**Files:**
- Create: `plugins/coding/panel/src/components/HandoffCard.vue`、`HandoffPicker.vue`、`HistoryOverlay.vue`
- Create: `plugins/coding/panel/src/lib/replay.ts`(+ test)
- Modify: `plugins/coding/panel/src/App.vue`

**Interfaces:**
- `replay.ts`:`pickReplayCandidate(rows: SessionRow[], cwd: string, codexAvailable: boolean|null): {sid: string; agent: string} | null`(同 cwd、排除 live running/waiting、排除 codex 不可用;rows 已按时间倒序取首个)+ 让位判断纯函数

**行为清单:**
- handoffSend(chip 跨引擎):占 sending 窗;`coding.session_brief {id, target}`;等待期改主意(currentSession/switchAgent 变了)丢弃;成功:旧 sid 进 discarded、currentSession=null、switchAgent=null、curAgent=新引擎、marker「—— 交接给 X 继续(上下文为摘要移植)——」、send({prompt:"【交接上下文】\n"+brief+"\n\n【用户继续】\n"+userText, agent})(**走 start 不带 mode**)
- Codex→CC:history 浮层区 1 Codex 卡 [交接给 CC] → `coding.handoff_list {cwd}` → 0 条提示/1 条直进/多条 picker(显示 fmtTs+first_line)→ `coding.handoff_brief {session_id, cwd}`(失败也开卡:红条+空 textarea 可手动粘贴)→ HandoffCard(可编辑 textarea、[取消]、[用它开始 → Claude Code];开始时 seal readonly + `coding.start {cwd, prompt:brief, source:"codex:"+sid}`(**不带 mode/agent**),受理前即 streaming 态,秒败竞态同 send)
- rewind:用户气泡 uuid 时挂 ⏪;点击 disabled 防重 → `coding.rewind {id, user_msg_id}` → 结果经事件流 rewind_ok 回(marker);失败状态行
- HistoryOverlay:openHistory(streaming/cwd 空拒绝);两路并发 last_sessions(失败降级 null)+ list;区 1 上次会话(CC 卡 [继续]→attach_cc→resumeSession(sid,"claude-code");Codex 卡 [原生续]→attach_codex→resumeSession(sid,"codex");[交接给 CC]);区 2 译宝历史(normCwd 去尾斜杠过滤,行:引擎徽标+时间+目录名+prompt 42 字+status 着色 done绿/running蓝/failed红/stopped琥珀,点击 resumeSession(row.id,row.agent));两区皆空空态文案;overlay 空白关闭
- autoReplay:prefillCwd/commitCwd 后 refreshCwdState → pickReplayCandidate → requestResume(sid, agent, {skipIfEmpty:true});空会话顺延(0→tryNext;-1 停止);currentSession||resuming 让位
- prefillCwd:init 时 coding.list,cwd 空则取首行 cwd

- [ ] **Step 1-3:replay 纯函数 TDD;组件与编排实现;构建 + 测试绿;commit `feat: studio 交接/rewind/接续浮层/自动回放`**

---

### Task 8: takeover 接线 + esc 优先级 + App 集成收尾

**Files:**
- Modify: `plugins/coding/panel/src/App.vue`

**行为清单:**
- `onInit(data, msg)`:`setTakeover(!!msg?.takeover)` → body.takeover 类(隐藏 Composer 区);takeover 退出清空 store 队列
- `onHostMessage`:takeover-input{text,refs} → store.takeoverInput(busy 入队状态行「已排队,本轮结束后自动发送」);takeover-stop → store.takeoverStop
- takeover 态每状态变化 `emitPanelEvent("takeover-state", {state, session})`(store.report 已挂,此处接真桥);state ∈ sending/streaming/waiting/idle
- esc 优先级(document keydown):agent-picker 开→关;history 开→关;handoff-picker 开→关;否则 currentSession && (streaming||sending) → stop
- 无桥预览:hasBridge=false 时显示 bridge-warn + 示例对话(renderPreviewSample 等价物,Vue 渲染静态样例)
- 真机冒烟清单(随 commit 记录在报告):发起会话→流式→工具卡展开→审批镜像(只读,裁决走 L2)→停止→恢复会话→切换引擎交接→rewind→@chip→粘贴截图→takeover(面板窗输入条路由)

- [ ] **Step 1-3:实现;构建 + 测试绿;commit `feat: studio takeover 接线与 App 集成收尾`**

---

### Task 9: 切换退役(panel ref 切 coding:studio,删 chat.html)

**Files:**
- Modify: `plugins/coding/skills/coding.py`(`_stream` emit panel "coding:chat"→"coding:studio";start/send/list/attach 的 ActionResult panel;RewindSkill/StopSkill 兜底 emit 同步改)
- Modify: `plugins/coding/api.toml`(start/send/list/attach 的 `panel = "coding:chat"` → `"coding:studio"`)
- Modify: `plugins/coding/manifest.toml`(删 chat 的 [[panel]] webview 声明)
- Delete: `plugins/coding/panel/chat.html`、`plugins/coding/panel/vendor/`(三库已走 npm)
- Modify: `desktop/src/components/PanelApp.vue`(isCoding 泛化:`panel.startsWith("coding:")` 且 (webviewHtml||webviewUrl))
- Modify: `desktop/src/components/HomePlugins.vue`(:387 wall 刷新守卫等 coding:chat 字面引用排查)
- Modify: `sidecar/src/yibao_brain/server.py`(如有 coding:chat 字面引用,如任务卡路由)→ 改 coding:studio
- Test: `sidecar/tests/` 引用 coding:chat 面板名的用例更新(grep 全仓 `coding:chat`)

**行为清单:**
- grep `coding:chat` 全仓,逐一裁定:运行时引用改 coding:studio;历史文档不改
- 关键裁决点(先查再改):loop.py 任务卡点击路由、HomeFeed 收件箱、server.py _fulfill_coding_perm 的 surface 文案、feed 任务卡 meta.plugin==="coding" 的 attach 路由( panel 事件 data.attach 流不变)
- isCoding 泛化后:takeover 输入条路由对 studio 生效;ChatPanel 旧 ref 不再有面板注册(残留引用给清晰空态)
- sidecar 全量 + desktop 全量 + 面板测试 + cargo 全绿
- 真机验收清单(报告记录):既有 P1/P2/R3 验收全过(对话/回放/引擎/交接/rewind/mode/chips/截图/takeover/逃生口/后台任务卡);studio 在面板窗与大窗都能开

- [ ] **Step 1-4:改 → grep 复查 → 全量闸门 → commit `feat: coding:studio 上线——panel ref 切换与 chat.html 退役`**

---

## 验收标准(阶段二)

A. studio 单工位能力与 chat.html 现状逐条对齐(侦察 13 项清单逐条过)
B. P1/P2/R3 既有验收不回归(takeover/逃生口/审批 L2/后台任务卡/会话墙)
C. 全量测试绿:sidecar pytest、desktop vitest+build、cargo test、panel vitest
D. 真机:studio 在面板窗(经 coding.start/list 等触发)与大窗(子入口)均可用;热加载仍有效
