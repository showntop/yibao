# Phase 1 · 能力表面收口实施计划（2026-08-13）

> **Goal:** 补齐四级能力表面的下两级（Inline 回执 / Peek 探窗）与活动轨，把表面裁决权从「前端猜」下沉为「sidecar 建议 + 宿主裁决」——让简单动作不再开大面板，长任务退出表面后不再失踪。
>
> **关联 spec：** `docs/superpowers/specs/2026-08-13-pet-os-mainline-design.md` §3 Phase 1；设计依据 `docs/research/2026-08-09-capability-surfaces-design.md` §3/§4/§5/§12.7。
>
> **前置：** Phase 0 完成（CI 绿 + 可打包，否则无法真机验收）。

## 背景：Slice 1 留下的两个反效果

能力表面 Slice 1 已落地（`panel` 事件不再抢顶层 tab；主屏内 Stage/Focus 两档；scene 持久化），但只做了两级**重**表面，导致：

1. **简单动作被迫开大面板。** `presentation` 类型当前是 `"stage" | "focus"`（`Home.vue:77`），没有 inline/peek。「存一条素材」「查下周哪天空」这类调研 §5 明确该走 Inline 的动作，也只能整块重排主屏。
2. **表面裁决权在错误的层。** `attention` 完全由前端本地推断——`HomePlugins.vue:317` 靠 `requestedPlugin === plugin && Date.now() <= requestedUntil` 这个时间窗标志判断「是不是用户明确要的」。sidecar 明明知道这次调用是用户显式意图还是模型自作主张，却没有表达渠道。
3. **长任务退出表面即失踪。** 活动轨零实现，coding 这类长跑任务一旦收起工作面就无处可寻，违反调研 §4。

## Global Constraints

- **协议向后兼容**：`ActionResult` 新字段全部可选带默认，旧插件（不声明 presentation）行为不变——由宿主按现有规则推断
- **`presentation` 是建议不是命令**（调研 §12.7）：最终展示级别由宿主裁决器决定
- **裁决器必须是纯函数**：输入意图/建议/当前状态，输出展示级别。这是本阶段唯一适合单测的核心逻辑，必须可测
- sidecar pytest 全绿（基线 900）；`vue-tsc --noEmit` / `vite build` / `npm test` exit 0
- 每任务一 commit，中文 scope（`feat(surface)`）

## File Structure

**改：** `sidecar/src/yibao_brain/ipc.py`（ActionResult 扩字段）、`sidecar/src/yibao_brain/loop.py`（`_panel_payload` 透传，:196-226 附近 + :325/:510 两个 yield 点）、`sidecar/src/yibao_brain/plugins.py`（manifest panel 的 surfaces 声明）、`desktop/src/Home.vue`（presentation 类型扩四档 + 接裁决器）、`desktop/src/components/HomePlugins.vue`（:305-319 改为读 payload）

**新建：** `desktop/src/lib/surface-policy.ts`（裁决器纯函数）、`desktop/src/lib/surface-policy.test.ts`、`desktop/src/components/InlineReceipt.vue`、`desktop/src/components/PeekSurface.vue`、`desktop/src/components/ActivityShelf.vue`

---

## Task 1: 表面协议下沉 sidecar

**Files:** Modify `sidecar/src/yibao_brain/ipc.py`、`sidecar/src/yibao_brain/loop.py`；Test `sidecar/tests/test_loop.py`

**Interfaces:**
- Produces：`ActionResult.presentation`（`"inline"|"peek"|"stage"|"focus"|None`）、`ActionResult.attention`（`"quiet"|"suggest"|"focus"`，默认 `"suggest"`）、`ActionResult.object`（`{type,id,title}` 或 None）
- Produces：`Event(kind="panel").payload` 增加同名三字段 + `origin`

- [ ] **Step 1: 写失败测试**

`sidecar/tests/test_loop.py` 追加（复用文件既有 FakeSkill/FakeProvider 模式）：

```python
def test_panel_event_carries_surface_hints():
    """技能声明的 presentation/attention/object 必须透传进 panel 事件——
    宿主裁决需要这些信息，此前只能靠前端猜「是不是用户明确要的」。"""
    # 构造一个返回 ActionResult(panel="notes:list", presentation="inline",
    # attention="quiet", object={"type":"note","id":"7","title":"读书笔记"}) 的 FakeSkill
    ...
    ev = next(e for e in events if e.kind == "panel")
    assert ev.payload["presentation"] == "inline"
    assert ev.payload["attention"] == "quiet"
    assert ev.payload["object"]["id"] == "7"


def test_panel_event_defaults_when_skill_silent():
    """旧插件不声明 → presentation=None（宿主按老规则推断）、attention="suggest"。"""
    ...
    assert ev.payload["presentation"] is None
    assert ev.payload["attention"] == "suggest"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd sidecar && uv run pytest tests/test_loop.py -k surface_hints -x -q
```
Expected: FAIL（payload 无这些键）

- [ ] **Step 3: 实现**

`ipc.py` `ActionResult`（:28-33）扩：

```python
class ActionResult(BaseModel):
    success: bool
    data: dict = Field(default_factory=dict)
    error: str = ""
    screenshot_path: str | None = None
    panel: str | None = None  # 面板引用「plugin_id:name」：执行成功时带上，壳侧渲染对应面板
    # ---- 能力表面提示（调研 §12.7）：都是「建议」，最终展示级别由宿主裁决 ----
    presentation: Literal["inline", "peek", "stage", "focus"] | None = None
    attention: Literal["quiet", "suggest", "focus"] = "suggest"
    object: dict | None = None  # {type,id,title}：跨应用接力用，不依赖面板 DOM
```

`loop.py` 构造 panel payload 处（`_redirect_to_focused_webview` 的调用点，以及 :325 / :510 两个 `yield Event(kind="panel", payload=payload)`）——把 result 的三字段并进 payload。建议抽一个小函数避免两处重复：

```python
def _with_surface_hints(payload: dict, result: ActionResult, origin: str | None) -> dict:
    """把技能的表面建议并进 panel 载荷。origin 供宿主做 matched-geometry 与返回定位。"""
    return {
        **payload,
        "presentation": result.presentation,
        "attention": result.attention,
        "object": result.object,
        "origin": origin,
    }
```

`origin` 传当前 action id（`Action.id`），前端用它把表面锚定到对应的过程行。

> **注意**：`_redirect_to_focused_webview`（:196-226）返回的是重建的 dict，会丢掉新字段——必须在它**之后**再并入 hints，顺序不能反。

- [ ] **Step 4: 跑测试 + 全量回归**

```bash
cd sidecar && uv run pytest tests/test_loop.py -q && uv run pytest -q
```
Expected: 新 2 PASS；全量 900+ 全绿

- [ ] **Step 5: Commit**

```bash
git add sidecar/src/yibao_brain/ipc.py sidecar/src/yibao_brain/loop.py sidecar/tests/test_loop.py
git commit -m "feat(surface): 表面提示下沉——ActionResult 带 presentation/attention/object，panel 事件透传"
```

---

## Task 2: manifest 声明支持的表面范围

**Files:** Modify `sidecar/src/yibao_brain/plugins.py`（`_load_panels`）；Test `sidecar/tests/test_plugins.py`

插件应能声明「我这个面板支持哪几档表面、最小宽度多少」，但**不能声明自动抢焦点**（调研 §12.6）。

- [ ] **Step 1: 写失败测试**

```python
def test_panel_declares_supported_surfaces():
    """manifest [[panel]] 的 surfaces/min_width 被解析；未声明时默认全档支持。"""
    # 造 manifest：[[panel]] name="board" type="schema" surfaces=["peek","stage"] min_width=720
    assert panel["surfaces"] == ["peek", "stage"]
    assert panel["min_width"] == 720

def test_panel_surfaces_default_all():
    assert panel["surfaces"] == ["inline", "peek", "stage", "focus"]
```

- [ ] **Step 2-3: 跑失败 → 实现**

`_load_panels` 解析两个可选键，非法值过滤掉（不是四个合法档之一就丢弃，而不是报错——单插件不该拖垮加载）。

- [ ] **Step 4: 回归 + Commit**

```bash
git commit -m "feat(surface): manifest 面板声明 surfaces/min_width（非法值静默过滤）"
```

---

## Task 3: 宿主裁决器（纯函数 + 单测）

**Files:** Create `desktop/src/lib/surface-policy.ts`、`desktop/src/lib/surface-policy.test.ts`；Modify `desktop/vitest.config.ts`（include 扩到 `src/lib`）

**这是本阶段的核心逻辑**，也是唯一能被自动化验证的部分——调研 §15 的验收条 3/4（简单动作不开面板、模型建议不自动展开）就是这个函数的行为约定。

- [ ] **Step 1: 写测试（先定契约）**

```ts
// surface-policy.test.ts
describe("decideSurface", () => {
  it("模型自作主张时最多到 peek——绝不自动 stage/focus", () => {
    expect(decideSurface({ suggested: "focus", attention: "suggest", explicit: false, current: null }))
      .toEqual({ presentation: "peek", show: true });
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: false, current: null }))
      .toEqual({ presentation: "peek", show: true });
  });

  it("attention=quiet 只进活动轨，不展开任何表面", () => {
    expect(decideSurface({ suggested: "inline", attention: "quiet", explicit: false, current: null }))
      .toEqual({ presentation: null, show: false });
  });

  it("用户明确请求时按建议展开，可达 stage/focus", () => {
    expect(decideSurface({ suggested: "stage", attention: "suggest", explicit: true, current: null }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("插件不声明 presentation → 回落 stage（保持 Slice 1 既有行为）", () => {
    expect(decideSurface({ suggested: null, attention: "suggest", explicit: true, current: null }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("已在 stage/focus 时新结果不降级——不把用户正在用的工作面缩掉", () => {
    expect(decideSurface({ suggested: "inline", attention: "suggest", explicit: false, current: "stage" }))
      .toEqual({ presentation: "stage", show: true });
  });

  it("面板不支持的档位向下回落到它支持的最高档", () => {
    expect(decideSurface({ suggested: "focus", attention: "suggest", explicit: true, current: null, supported: ["inline", "peek"] }))
      .toEqual({ presentation: "peek", show: true });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd desktop && npm test
```
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```ts
// desktop/src/lib/surface-policy.ts
/**
 * 能力表面裁决：把「插件建议 + 注意力级别 + 用户是否明确要求 + 当前表面」裁决为实际展示级别。
 *
 * 硬规则（调研 §2.3）：模型或插件最多自动展开到 peek；进入 stage/focus 必须有用户明确意图。
 * 这条规则是「结果一回来就跳页」的根治手段，不可为了某个插件开后门。
 */
export type Presentation = "inline" | "peek" | "stage" | "focus";
export type Attention = "quiet" | "suggest" | "focus";

const RANK: Record<Presentation, number> = { inline: 0, peek: 1, stage: 2, focus: 3 };
const AUTO_MAX: Presentation = "peek";

export function decideSurface(input: {
  suggested: Presentation | null;
  attention: Attention;
  explicit: boolean;
  current: Presentation | null;
  supported?: Presentation[];
}): { presentation: Presentation | null; show: boolean } {
  // quiet：只记账，不打扰（进活动轨 / Feed，由调用方处理）
  if (input.attention === "quiet") return { presentation: null, show: false };

  let target: Presentation = input.suggested ?? "stage";
  if (!input.explicit && RANK[target] > RANK[AUTO_MAX]) target = AUTO_MAX;

  // 面板声明的支持范围：向下回落到它支持的最高档
  const supported = input.supported;
  if (supported && supported.length && !supported.includes(target)) {
    const fallback = [...supported].sort((a, b) => RANK[b] - RANK[a]).find((s) => RANK[s] <= RANK[target]);
    target = fallback ?? supported[0];
  }

  // 不把用户正在用的工作面缩掉：新结果只升不降
  if (input.current && RANK[input.current] > RANK[target]) target = input.current;

  return { presentation: target, show: true };
}
```

- [ ] **Step 4: 扩 vitest 覆盖范围**

`desktop/vitest.config.ts` 的 `include` 从只含 `src/state/**/*.test.ts` 扩为同时含 `src/lib/**/*.test.ts`。

- [ ] **Step 5: 验证 + Commit**

```bash
cd desktop && npm test && npx vue-tsc --noEmit
git add desktop/src/lib/surface-policy.ts desktop/src/lib/surface-policy.test.ts desktop/vitest.config.ts
git commit -m "feat(surface): 宿主裁决器——模型最多自动展开到 peek，stage/focus 需明确意图"
```

---

## Task 4: 接线——Home/HomePlugins 改用裁决器

**Files:** Modify `desktop/src/Home.vue`（:76-179 区域）、`desktop/src/components/HomePlugins.vue`（:305-319）

- [ ] **Step 1: 扩类型**

`Home.vue:77` 的 `CapabilityPresentation` 从 `"stage" | "focus"` 扩为 `Presentation`（从 `lib/surface-policy` 导入）。注意 `surface` 域的 scene schema 校验（`desktop/src/state/domains/surface.ts:20-26`）也要放行新值，**并升 `surface:scene` 的 version**（`surface.ts:54`），否则旧快照带着 `presentation:"stage"` 恢复没问题、新值写回后旧版本前端会校验失败。

- [ ] **Step 2: HomePlugins 改为透传后端提示**

`HomePlugins.vue:315-318` 现在的本地时间窗推断保留为 `explicit` 的**来源之一**（用户在插件库里点击确实是明确意图），但 `presentation`/`attention` 改为从 panel 事件 payload 读取：

```ts
  if (isNewPanel && !silent) {
    const explicit = requestedPlugin === plugin && Date.now() <= requestedUntil;
    emit("panel", {
      ...meta,
      explicit,
      suggested: v.presentation ?? null,
      attention: v.attention ?? "suggest",
      supported: v.surfaces ?? undefined,
    });
  }
```

- [ ] **Step 3: Home 用裁决器替换硬编码分支**

`Home.vue:141-145` 的 `onPanelAvailable` 从：

```ts
  if (surface.attention === "stage" || surfaceVisible.value) showSurface();
```

改为调用 `decideSurface`，按返回的 `{presentation, show}` 决定：`show=false` → 只推活动轨（Task 6）；`presentation="inline"` → 交给 Inline 回执（Task 5）；`peek/stage/focus` → 设置 `presentation.value` 后 `showSurface()`。

- [ ] **Step 4: 验证 + Commit**

```bash
cd desktop && npx vue-tsc --noEmit && npx vite build && npm test
git commit -m "feat(surface): Home/HomePlugins 接裁决器——表面级别改由后端建议 + 宿主裁决"
```

---

## Task 5: Inline 回执与 Peek 探窗

**Files:** Create `desktop/src/components/InlineReceipt.vue`、`desktop/src/components/PeekSurface.vue`；Modify `desktop/src/components/CapabilityConversationRail.vue`（过程行收束锚点）

**设计约束（调研 §3.1/§3.2）：**
- Inline：过程行原地收束为宿主原生卡，最多两个动作（撤销/展开）。视觉骨架、间距、按钮、风险提示**全部由宿主控制**，插件只提供图标与 accent
- Peek：从调用锚点 matched-geometry 长出，Esc / 点空白 / 完成动作后缩回原锚点。不重排主屏、不占独立导航历史、用户仍能看见背后的对话

- [ ] **Step 1: InlineReceipt.vue**

props：`{ provider, title, summary, object?, actions? }`。渲染一张紧凑卡（参考 `Bubble.vue` 的过程行样式变量，不新造设计语言）。「展开」动作 emit 给父组件升级到 Peek/Stage。

- [ ] **Step 2: PeekSurface.vue**

复用现有 `SchemaPanel` / `WebviewPanel` 作为内容渲染器（**不复制插件运行时**），外面套宿主 chrome：标题 + 来源 + 关闭。

动效照 Slice 1 已有的 matched-geometry 实现（`HomePlugins.vue` 的 `growIn`/`collapseOut` 是现成参考，含 `clip-path` + `fill:forwards` + `prefers-reduced-motion` 处理）——**复用其时序常量，不要另起一套**，否则会出现调研 §9 警告的「每个插件一套转场」。

键盘：`Esc` 收起（与 Focus 的逐级退出规则统一：清对象 → 退专注 → 收表面）。

- [ ] **Step 3: 挂到过程行锚点**

`CapabilityConversationRail.vue` 的能力调用条目成为 `origin`（Task 1 透传的 action id）对应的锚点：Inline 在原位替换，Peek 从这一行长出。

- [ ] **Step 4: 验证 + Commit**

```bash
cd desktop && npx vue-tsc --noEmit && npx vite build
git commit -m "feat(surface): Inline 回执卡 + Peek 探窗（复用 Slice 1 matched-geometry 时序）"
```

---

## Task 6: 活动轨 Activity Shelf

**Files:** Create `desktop/src/components/ActivityShelf.vue`；Modify `desktop/src/Home.vue`

**设计约束（调研 §4）：** 常态只显示最重要的三类状态；点击胶囊恢复上次表面、滚动位置与对象焦点；完成后停留一个短周期再收束为 Feed。**用户正在操作时，活动只改变状态，不抢键盘焦点、不自动展开、不做补偿点击。**

- [ ] **Step 1: 组件**

胶囊三态：运行中（`Coding · 正在改 4 个文件`）、待批准（琥珀色，`日历 · 等你确认 1 项`）、已完成（`自媒体 · 文章已生成`）。

数据源本阶段先接内存态（`panelState` + 待批准队列 `lib/brain.ts` 的共享 pending 队列 + Task 4 里 `show=false` 的 quiet 结果）。**权威持久化留给 Phase 2 的 TaskTimeline**——本阶段刷新后胶囊会丢，这是已知且可接受的分期取舍，须在实装记录里写明。

- [ ] **Step 2: 恢复逻辑**

点击胶囊 → 用 `surface_id`（本阶段用 `panel` ref 代替，Phase 2 换真 id）恢复表面 + `presentation`；复用 Slice 1 已有的 `restoreSurface()` 路径。

- [ ] **Step 3: 位置**

顶栏右侧（`Home.vue` 顶栏区域）。窄窗时收为一个计数徽标。

- [ ] **Step 4: 验证 + Commit**

```bash
cd desktop && npx vue-tsc --noEmit && npx vite build && npm test
git commit -m "feat(surface): 活动轨——运行中/待批准/已完成胶囊，点击恢复表面（持久化留 Phase 2）"
```

---

## Task 7: 验收

- [ ] **Step 1: 自动化全绿**

```bash
cd sidecar && uv run pytest -q
cd ../desktop && npx vue-tsc --noEmit && npx vite build && npm test
cargo check --manifest-path src-tauri/Cargo.toml
```

- [ ] **Step 2: 真机验收清单**（对应调研 §15 验收标准）

1. 说「记一下这句话」→ 只出 Inline 回执卡，**不开任何面板**（验收条 3）
2. 模型自作主张调某插件 → 最多出 Peek，顶层导航不切换，Stage 不自动展开（验收条 1/4）
3. 在插件库明确点击「打开选题看板」→ 直达 Stage（explicit 路径未被误伤）
4. Peek 打开后按 Esc → 缩回原锚点；背后对话可见、草稿与滚动位置不变（验收条 2/5）
5. 起一个 coding 长任务 → 收起工作面 → 活动轨出现运行中胶囊 → 点击恢复到原表面与滚动位置（验收条 7）
6. 待批准出现时活动轨变琥珀 → 确认它**不抢输入焦点**（验收条 8）
7. 面板声明 `surfaces=["inline","peek"]` 的插件被要求 focus → 回落 peek，不崩

- [ ] **Step 3: 在主线 spec 追加 Phase 1 实装记录**

写明：协议新增字段与默认值、裁决器的硬规则、Inline/Peek 复用的动效时序来源、**活动轨持久化推迟到 Phase 2 的取舍**、以及真机验收逐条结果。

---

## 自审

- 调研覆盖：§3.1 Inline（T5）§3.2 Peek（T5）§2.3 不抢焦点（T3 裁决器 + T7-2）§4 活动轨（T6）§12.6 面板声明支持范围（T2）§12.7 surface descriptor（T1）✅
- 类型一致：`presentation`/`attention` 三处对齐（`ipc.py` 定义 = `loop.py` 透传 = `surface-policy.ts` 类型）✅
- 分期诚实：`surface_id`、TaskTimeline、持久化活动轨明确标注留 Phase 2，不假装本阶段闭环 ✅
- 风险：`surface:scene` schema 版本升级（T4-1）若漏做，旧快照会与新 presentation 值互相污染——已在步骤里点明
