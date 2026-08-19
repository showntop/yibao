# 面板输入模式声明(handoff 扩到大窗)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「壳输入条怎么办」做成插件声明式配置(manifest `[[panel]].input` 四模式),handoff 从硬编码 coding:studio 收编为读声明,并扩到大窗(主窗插件页 HomePlugins)。

**Architecture:** sidecar `plugins.py` 解析+校验+透传(一处改 5 发送点全带,Rust 零改动);app `PanelPayload` 加 `input?` 字段;PanelApp/HomePlugins 两个宿主的让位判定都读声明。壳侧行为二值:`input ∈ {handoff, none}` → 壳条隐藏;草稿随迁仅 handoff。

**Tech Stack:** sidecar Python(pytest);app Vue 3.5 + TS(vitest + happy-dom + @vue/test-utils,Task 基建已于 input-handoff 就位)。

**Spec:** `docs/superpowers/specs/2026-08-19-panel-input-modes-design.md`(四模式定义与验收标准以此为准)

## Global Constraints

- **缺省零变化**:不声明 `input` 的面板行为必须与现状逐字节一致(载荷不带 input 键,前端 undefined 按 inherit)。
- **随迁仅 handoff**:`none` 面板没有 Composer,随迁等于丢稿——setCurrent 的取稿判定必须是 `input === "handoff"`,不是让位判定。
- **既有 handoff 测试必须同步改**:`PanelApp.handoff.test.ts` 的 `firePanel()` 载荷补 `input` 字段,否则判定改读声明后旧用例全红。
- **大窗逃生口零新元素**:顶部「主屏」tab 即逃生口,HomePlugins 不加团子/mini 输入。
- 注释中文全角标点,风格同既有文件;TDD 先败后成;每任务一 commit;panel 工程与 Rust 零改动。
- sidecar 测试命令:`cd sidecar && uv run --extra dev pytest -x -q`(worktree 新 venv 无 dev extras,必须带 --extra dev)。

---

### Task 1: sidecar——input 解析 + 校验 + 透传

**Files:**
- Modify: `sidecar/src/yibao_brain/plugins.py`(`_load_panels` :419+ 统一尾处理 :468-477;`_surface_decl_from` :350-359)
- Test: `sidecar/tests/test_plugins.py`(追加;沿用既有面板测试的 fixture/惯例,先读该文件找 panel 相关用例模式)

**Interfaces:**
- Consumes: manifest `[[panel]].input`(字符串,可选)
- Produces: panel 事件载荷新增 `input` 键(仅声明时存在;合法值 `inherit|coexist|handoff|none`)——Task 2/3 前端消费

- [ ] **Step 1: 写失败测试**

既有模式(`test_plugins.py`):`_write_plugin(tmp_path, name, manifest, files)` + 模块级 `NOTES_PANEL_MANIFEST` 用 `.replace('type = "schema"\nname = "list"', ...)` 注字段;`get_panel(ref)` 读注册表;`panel_payload(ActionResult(success=True, data={...}, panel=ref))` 读载荷;告警走 `print(..., file=sys.stderr)` 用 `capsys` 断言。追加(放 :779 surfaces 用例族之后):

```python
# ---------- 面板输入模式声明 [[panel]].input(panel-input-modes spec)----------


def test_panel_input_declared_passthrough(data_dir, tmp_path):
    """声明 input = "handoff" → 注册表保留且 panel_payload 载荷透传。"""
    from yibao_brain.plugins import get_panel, panel_payload

    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\ninput = "handoff"',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert get_panel("notes:list")["input"] == "handoff"
    p = panel_payload(ActionResult(success=True, data={"rows": []}, panel="notes:list"))
    assert p["input"] == "handoff"


def test_panel_input_absent_no_key(data_dir, tmp_path):
    """未声明 → 注册表与载荷都无 input 键(缺省 inherit 由前端语义兜,不发键)。"""
    from yibao_brain.plugins import get_panel, panel_payload

    _write_plugin(tmp_path, "notes", NOTES_PANEL_MANIFEST, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
    _load(tmp_path, reg)
    assert "input" not in get_panel("notes:list")
    p = panel_payload(ActionResult(success=True, data={"rows": []}, panel="notes:list"))
    assert "input" not in p


def test_panel_input_invalid_warns_and_drops(data_dir, tmp_path, capsys):
    """非法值 → stderr 告警 + 按未声明处理(不回退出键,前端语义 inherit)。"""
    from yibao_brain.plugins import get_panel

    manifest = NOTES_PANEL_MANIFEST.replace(
        'type = "schema"\nname = "list"',
        'type = "schema"\nname = "list"\ninput = "takeover"',
    )
    _write_plugin(tmp_path, "notes", manifest, {"panel/list.schema.json": LIST_SCHEMA})
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}
    assert "input" not in get_panel("notes:list")
    assert "inherit" in capsys.readouterr().err
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run --extra dev pytest tests/test_plugins.py -k input -x -q`
Expected: FAIL(断言键不存在/未透传)

- [ ] **Step 3: 实现**

`plugins.py` `_load_panels` 统一尾处理段(:468-477,surfaces/min_width 先例处)加:

```python
_INPUT_MODES = ("inherit", "coexist", "handoff", "none")  # 模块级常量,放 _SURFACE_LEVELS 旁
```

尾处理循环内(parsed 已建、surfaces 处理之后;告警同款 print 到 stderr,对齐 :430/:440 先例):

```python
        im = p.get("input")
        if im is not None:
            if im in _INPUT_MODES:
                parsed["input"] = im
            else:
                print(f"[yibao] 插件 {pid} panel {ref} 的 input={im!r} 非法,按 inherit 处理", file=sys.stderr)
```

`_surface_decl_from`(:350-359)加透传:

```python
    if "input" in panel: out["input"] = panel["input"]
```

- [ ] **Step 4: 跑测试确认通过 + 全量 + 提交**

```bash
cd sidecar && uv run --extra dev pytest -x -q
git add sidecar/src/yibao_brain/plugins.py sidecar/tests/test_plugins.py
git commit -m "feat(brain): 面板输入模式声明——[[panel]].input 解析/校验/透传(四模式,缺省 inherit 不发键)"
```

### Task 2: app 类型 + PanelApp 声明化(收编硬编码)

**Files:**
- Modify: `app/src/lib/brain.ts`(PanelPayload :44-65)
- Modify: `app/src/components/PanelApp.vue`(current 类型 :35-41;onEvent panel 构造;pullCache 泛型 :350-366;handoff computed;setCurrent entering 判定)
- Test: `app/src/components/PanelApp.handoff.test.ts`(firePanel 载荷补 input + 新增「无声明不让位」用例)

**Interfaces:**
- Consumes: Task 1 的载荷 `input` 键
- Produces: `PanelPayload["input"]` 类型 = `"inherit" | "coexist" | "handoff" | "none"`(Task 3 复用)

- [ ] **Step 1: 改测试先红**

`PanelApp.handoff.test.ts` 的 `firePanel`:

```ts
function firePanel(panel: string, input?: string) {
  brainHandler!({
    kind: "panel",
    payload: { panel, title: panel, schema: null, webview: { url: "yibao-plugin://x/panel/dist/index.html", v: 1 }, data: {}, ...(input ? { input } : {}) },
  });
}
```

既有三个用例里所有 `firePanel("coding:studio")` 改为 `firePanel("coding:studio", "handoff")`。describe 内追加:

```ts
  it("声明缺省/非 handoff:即使是 coding:studio 也不让位(判定只读声明,无硬编码)", async () => {
    const w = mountApp();
    await flushPromises();
    firePanel("coding:studio"); // 无 input 键
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
    firePanel("coding:studio", "coexist");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
  });

  it("none 让位但不随迁(无 Composer,随迁=丢稿)", async () => {
    const takeDraft = vi.fn(() => "草稿");
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
    firePanel("toolbox:main");
    await flushPromises();
    firePanel("zimeiti:board", "none");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(false); // 让位
    expect(takeDraft).not.toHaveBeenCalled();          // 但不取稿
  });
```

（追加用例需在文件顶部确认 `mount`/`vi` 已 import——已在。)

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts`
Expected: FAIL(旧三例:无 input 字段不再让位;新两例同理)

- [ ] **Step 3: 实现**

`brain.ts` PanelPayload(`min_width` 行 :62 后)加:

```ts
  /** 面板输入安排（manifest [[panel]].input 四模式）:handoff/none 时壳输入条让位;缺省 inherit */
  input?: "inherit" | "coexist" | "handoff" | "none";
```

`PanelApp.vue`:

current 类型(:35-41)加 `input?: "inherit" | "coexist" | "handoff" | "none";`;onEvent panel 构造与 pullCache 的 invoke 泛型同步加 `input` 字段(e.payload?.input 透传;泛型加 `input?: string`)。

handoff computed(现 `current.value?.panel === "coding:studio"`)改为:

```ts
// ---- 输入条 handoff(input-handoff spec):声明制(panel-input-modes spec)——
//      input ∈ {handoff, none} 壳条让位;随迁仅 handoff(none 无 Composer,随迁=丢稿) ----
const handoff = computed(() => {
  const m = current.value?.input;
  return m === "handoff" || m === "none";
});
```

setCurrent 的 entering 判定(现 `v?.panel === "coding:studio"`)改为:

```ts
  const entering = current.value?.input !== "handoff" && v?.input === "handoff";
```

（注意:`current.value?.input !== "handoff"` 含 null/undefined 初态,语义 = 「从非 handoff 进 handoff」。)

全文件检查:`"coding:studio"` 字面量在 PanelApp.vue 应零残留(grep 验证,验收标准 C)。

- [ ] **Step 4: 跑测试确认通过 + 全量 + 提交**

```bash
cd app && pnpm vitest run src/components/PanelApp.handoff.test.ts && pnpm test && pnpm build
git add app/src/lib/brain.ts app/src/components/PanelApp.vue app/src/components/PanelApp.handoff.test.ts
git commit -m "refactor(panel): handoff 判定声明制——读 [[panel]].input,收编 coding:studio 硬编码"
```

### Task 3: HomePlugins 大窗让位

**Files:**
- Modify: `app/src/components/HomePlugins.vue`(current 类型 :212-219;onEvent panel 构造 :395-407;pullCache 泛型 :545-569;bench-bar :735;样式如需要)
- Test: `app/src/components/HomePlugins.handoff.test.ts`(新建)

**Interfaces:**
- Consumes: Task 2 的 `PanelPayload["input"]` 类型与判定规则(`input ∈ {handoff, none}` 让位)
- Produces: 无新接口

- [ ] **Step 1: 写失败测试** `app/src/components/HomePlugins.handoff.test.ts`

```ts
// @vitest-environment happy-dom
// 大窗 handoff(panel-input-modes spec §C):插件页激活面板的 input ∈ {handoff, none} → 底部 bench-bar 让位;
// 缺省/inherit 不动;切走恢复。逃生口 = 顶部导航(本组件不加新元素)。
import "fake-indexeddb/auto";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { describe, expect, it, vi } from "vitest";

let brainHandler: ((e: any) => void) | null = null;
vi.mock("../lib/brain", () => ({
  onBrainEvent: vi.fn((cb: any) => { brainHandler = cb; return Promise.resolve(() => {}); }),
  onPendingConfirms: vi.fn(() => () => {}),
  openHomeWindow: vi.fn(() => Promise.resolve()),
  panelAction: vi.fn(() => Promise.resolve({})),
  sendConfirmBatch: vi.fn(() => Promise.resolve()),
  runInput: vi.fn(() => Promise.resolve()),
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

import HomePlugins from "./HomePlugins.vue";

function mountPage() {
  // HomePlugins 子组件较重,桩掉与本特性无关的;先挂载读模板,缺的桩按报错补齐
  return mount(HomePlugins, {
    global: {
      stubs: {
        SchemaPanel: { template: "<div />" },
        WebviewPanel: { template: "<div />" },
        InputBar: { template: "<div />", setup(_: any, { expose }: any) { expose({ focus() {}, insertText() {} }); return {}; } },
        Avatar: { template: "<button v-bind='$attrs' />" },
        YbIcon: { template: "<i />" },
      },
    },
  });
}

function firePanel(panel: string, input?: string) {
  brainHandler!({
    kind: "panel",
    payload: { panel, title: panel, schema: null, webview: { url: "yibao-plugin://x/panel/dist/index.html", v: 1 }, data: {}, ...(input ? { input } : {}) },
  });
}

describe("大窗 handoff", () => {
  it("input=handoff → bench-bar 让位;无声明不动;切走恢复", async () => {
    const w = mountPage();
    await flushPromises();
    firePanel("coding:studio"); // 无声明
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
    firePanel("coding:studio", "handoff");
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(false);
    firePanel("toolbox:main"); // 切走
    await nextTick();
    expect(w.find(".bench-bar").exists()).toBe(true);
  });
});
```

**实现注意(写给实现者)**:HomePlugins 挂载依赖多(store/router/其它组件),测试若因未 mock 的依赖报错,逐条补 mock/stub 直至能挂——只允许动测试文件,mount 不上就 NEEDS_CONTEXT 上报。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd app && pnpm vitest run src/components/HomePlugins.handoff.test.ts`
Expected: FAIL(input=handoff 时 bench-bar 仍在)

- [ ] **Step 3: 实现** `HomePlugins.vue`

current 类型(:212-219)加 `input?: "inherit" | "coexist" | "handoff" | "none";`;onEvent panel 构造(:395-407)与 pullCache 泛型(:545-569)透传 `input`。

判定 computed(`chipText` 附近):

```ts
// ---- 大窗输入条 handoff(panel-input-modes spec §C):声明制,与 PanelApp 同规则——
//      input ∈ {handoff, none} 壳条让位;逃生口 = 顶部「主屏」tab,不加新元素 ----
const handoff = computed(() => {
  const m = current.value?.input;
  return m === "handoff" || m === "none";
});
```

模板 bench-bar(:735)加 `v-if="!handoff"`。**检查让位后的布局缝隙**:读 `.bench`/`.bench-bar` 的样式块,若 margin/padding 挂在容器上会在让位后留空缝——参照 PanelApp 的修法(水平 margin 留容器、bottom margin 归 bench-bar)对症处理,改动最小化。

- [ ] **Step 4: 跑测试确认通过 + 全量 + 提交**

```bash
cd app && pnpm vitest run src/components/HomePlugins.handoff.test.ts && pnpm test && pnpm build
git add app/src/components/HomePlugins.vue app/src/components/HomePlugins.handoff.test.ts
git commit -m "feat(home): 大窗输入条 handoff——插件页读 [[panel]].input 让位,主屏导航即逃生口"
```

### Task 4: coding 声明 + 回归 + 文档收尾

**Files:**
- Modify: `plugins/coding/manifest.toml`([[panel]] :43-48)
- Modify: `docs/superpowers/specs/2026-08-19-input-handoff-design.md`(背景节后补一句交叉引用)
- Modify: `docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md`(:29 输入条行的交叉引用句尾补「大窗同规则,见 panel-input-modes spec」——若 T6 已写则在其后追加)

- [ ] **Step 1: coding manifest 声明**

`plugins/coding/manifest.toml` 的 `[[panel]]`(name = "studio")加一行:

```toml
input = "handoff"
```

- [ ] **Step 2: 全闸回归**

```bash
cd sidecar && uv run --extra dev pytest -x -q
cd app && pnpm test && pnpm build
cd plugins/coding/panel && pnpm test   # 零改动,兜底跑
```

Expected: 全绿。app 用例数应比 main(f646319 时点 126)多 2(PanelApp 新增)+1(HomePlugins 新增)= 129。

- [ ] **Step 3: 文档收尾 + 提交**

`2026-08-19-input-handoff-design.md` 背景段末尾加:

```
后续:声明制四模式 + 大窗扩展见 specs/2026-08-19-panel-input-modes-design.md(本 spec 的 coding:studio 硬编码判定已收编为读声明)。
```

```bash
git add plugins/coding/manifest.toml docs/superpowers/specs/
git commit -m "feat(coding): studio 声明 input=handoff——大窗/面板窗同规则让位 + spec 交叉引用"
```

- [ ] **Step 4: 验收标准对照**

逐条核 spec §验收标准 A-D:A/B 属真机(B 的「不动面板零变化」由 T1 测试+全闸背书);C 由 T2 grep 背书;D 由全闸背书。写进报告。
