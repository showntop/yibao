# 小窗能力表面（Phase 1.5）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把小窗的「面板 = 弹独立浮窗」换成给已有过程行补一个表面属性，并补上声明式插件表达表面建议的渠道，使 Phase 1 验收条 ①「记一下这句话只出 Inline 不开面板」在大窗小窗同时成立。

**Architecture:** sidecar 侧只加一处协议（`[[tool]]` 的 `presentation`/`attention`），注入点复用 `DeclarativeTool.run()` 已有的「成功才回填 panel」后处理。前端不改裁决器 `decideSurface`——它在非 explicit 时本就封顶 `peek`，因此「`stage`/`focus` 才开窗，其余一律出行」这个二值映射使「模型自动调用绝不在小窗开浮窗」自动成立。表面属性按 `origin`（发起动作 id）补到对应过程行上，找不到就新建一行——**小窗不引入卡，也不动大窗**。

**Tech Stack:** Python 3 + pydantic（sidecar）、Vue 3 `<script setup>` + TypeScript（前端）、pytest、vitest。

## Global Constraints

- **协议向后兼容**：`[[tool]]` 新字段可选，不声明 = 现有行为完全不变
- **`presentation` 是建议不是命令**：最终形态由宿主裁决器决定
- **失败结果不带表面建议**：与既有「失败不放 panel 引用」共用同一 `if result.success`
- **窄规则宁缺毋滥**：漏报退化为可点行加一次点击（语义仍通顺），误报的代价是抢屏，因此宁可漏
- **不改 `desktop/src/lib/surface-policy.ts`**：其 11 条既有单测是本阶段的回归防线
- **不动大窗**：`InlineReceipt.vue` / `PeekSurface.vue` / `ActivityShelf.vue` / `Home.vue` 一律不改
- **小窗不做卡、不做 ActivityShelf**
- sidecar pytest 基线 **905** 全绿；`vue-tsc --noEmit` / `vite build` / `npm test`（基线 **70**）exit 0
- 每任务一 commit，中文 scope（`feat(surface)`）

## File Structure

**新建**
- `desktop/src/lib/pet-surface.ts` —— 形态映射 + 计数提取 + 失活，纯函数
- `desktop/src/lib/pet-surface.test.ts`
- `desktop/src/lib/explicit-intent.ts` —— 窄规则文本匹配，纯函数
- `desktop/src/lib/explicit-intent.test.ts`
- `desktop/src/components/SurfaceLine.vue` —— 带表面属性的行

**修改**
- `sidecar/src/yibao_brain/plugins.py` —— `DeclarativeTool.__init__`（:130）读声明、`run()`（:172-184）注入
- `plugins/notes/manifest.toml` —— `keep` 声明 `presentation = "inline"`
- `sidecar/tests/test_plugins.py` —— 4 条新测
- `desktop/src/App.vue` —— `BubbleMsg`（:54）、`submit`（:770）、`launchPlugin`（:449）、`action_proposed`（:558-565）、`case "panel"`（:727-738）、气泡渲染（:1155）

---

## Task 1: 声明式 tool 可声明表面建议

**Files:**
- Modify: `sidecar/src/yibao_brain/plugins.py:130-155`（`__init__`）、`:172-184`（`run`）
- Modify: `plugins/notes/manifest.toml:17-30`
- Test: `sidecar/tests/test_plugins.py`

**Interfaces:**
- Consumes: `_SURFACE_LEVELS`（`plugins.py:29`，值为 `("inline", "peek", "stage", "focus")`）
- Produces: `DeclarativeTool` 实例属性 `self._presentation: str | None`、`self._attention: str | None`；`run()` 返回的 `ActionResult` 在 `success=True` 时携带这两个字段

- [ ] **Step 1: 写失败测试**

在 `sidecar/tests/test_plugins.py` 末尾追加。复用文件既有的 `_write_plugin` / `_load` / `data_dir` 模式（见 `:141` 的 `test_db_tool_end_to_end`）。

```python
SURFACE_MANIFEST = """
id = "notes"
name = "闪念盘"
capabilities = ["db"]

[[table]]
name = "notes"
columns = [
  {name = "id", type = "text", pk = true},
  {name = "text", type = "text"},
  {name = "created_at", type = "integer"},
]

[[tool]]
id = "keep"
type = "db"
description = "记一条闪念"
risk = "L1"
presentation = "inline"
attention = "quiet"
[tool.params]
text = {type = "string", description = "内容"}
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "bad"
type = "db"
description = "非法声明"
risk = "L1"
presentation = "gigantic"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "silent"
type = "db"
description = "不声明"
risk = "L1"
[tool.db]
op = "insert"
table = "notes"

[[tool]]
id = "boom"
type = "db"
description = "会失败的删除"
risk = "L1"
presentation = "inline"
[tool.db]
op = "delete"
table = "no_such_table"
"""


def test_declarative_tool_carries_surface_hints(data_dir, tmp_path):
    """声明式 tool 的 presentation/attention 必须带进 ActionResult。

    notes/reminders 这批最该走 Inline 的插件全是声明式的；Phase 1 只给
    ActionResult 加了字段，等于把它们排除在表面模型之外。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = SkillRegistry()
    _load(tmp_path, reg)

    keep = reg.get("notes.keep")
    r = keep.run({"text": "买点牛奶", "created_at": 1}, keep.plugin_ctx)
    assert r.success
    assert r.presentation == "inline"
    assert r.attention == "quiet"


def test_declarative_tool_invalid_presentation_ignored(data_dir, tmp_path):
    """非法值静默过滤（与 [[panel]].surfaces 既有约定一致），不抛错、不阻断加载。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = SkillRegistry()
    assert _load(tmp_path, reg) == {"notes": "ok"}

    bad = reg.get("notes.bad")
    r = bad.run({"text": "x", "created_at": 1}, bad.plugin_ctx)
    assert r.success
    assert r.presentation is None


def test_declarative_tool_silent_defaults(data_dir, tmp_path):
    """不声明 → presentation=None、attention 保持 ActionResult 默认 "suggest"。

    这条锁住向后兼容：旧插件行为完全不变。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = SkillRegistry()
    _load(tmp_path, reg)

    silent = reg.get("notes.silent")
    r = silent.run({"text": "x", "created_at": 1}, silent.plugin_ctx)
    assert r.success
    assert r.presentation is None
    assert r.attention == "suggest"


def test_declarative_tool_failure_carries_no_hints(data_dir, tmp_path):
    """失败结果不带表面建议——失败不该建议展开面板。

    与既有「失败不放 panel 引用」共用同一个 if result.success 判断。"""
    _write_plugin(tmp_path, "notes", SURFACE_MANIFEST)
    reg = SkillRegistry()
    _load(tmp_path, reg)

    boom = reg.get("notes.boom")
    r = boom.run({"id": "x"}, boom.plugin_ctx)
    assert not r.success
    assert r.presentation is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd sidecar && uv run pytest tests/test_plugins.py -k surface_hints -v`
Expected: FAIL —— `assert None == "inline"`（`presentation` 恒为默认 `None`）

- [ ] **Step 3: 读取声明**

`plugins.py` 的 `DeclarativeTool.__init__` 里，在 `self.refresh = refresh`（:154）之后、`self._registry = ...`（:155）之前插入：

```python
        # 表面建议（能力表面 Phase 1.5）：属于「单次调用」的语义，故声明在 [[tool]]
        # 而非 [[panel]]——同一个 notes:list 面板，被 keep 触发该 inline（只是回执），
        # 被 list 触发该 stage（用户要浏览）。非法值静默过滤，与 surfaces 约定一致。
        pres = spec.get("presentation")
        self._presentation = pres if pres in _SURFACE_LEVELS else None
        att = spec.get("attention")
        self._attention = att if att in ("quiet", "suggest", "focus") else None
```

- [ ] **Step 4: 在唯一后处理点注入**

`plugins.py` 的 `run()`（:181-184）改为：

```python
        result = handler(params, ctx)
        if result.success:  # 失败不放 panel 引用，也不带表面建议
            if self._panel_ref:
                result.panel = self._panel_ref
            if self._presentation:
                result.presentation = self._presentation
            if self._attention:
                result.attention = self._attention
        return result
```

**只有这一个注入点。** 不要去改 `_run_db` / `_run_http` 等 7 处 `ActionResult(success=True, ...)` 返回点。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd sidecar && uv run pytest tests/test_plugins.py -v`
Expected: PASS（新增 4 条）

- [ ] **Step 6: notes.keep 声明 inline**

`plugins/notes/manifest.toml` 的 `[[tool]] id = "keep"` 段，在 `panel = "notes:list"`（:22）下一行加：

```toml
presentation = "inline"
```

`list` 与 `delete` **不加**——用户要浏览列表时该走 stage，保持默认推断。这一步是大窗验收条 ① 成立的直接前提。

- [ ] **Step 7: 全量回归**

Run: `cd sidecar && uv run pytest`
Expected: 909 passed（基线 905 + 4）

- [ ] **Step 8: Commit**

```bash
git add sidecar/src/yibao_brain/plugins.py sidecar/tests/test_plugins.py plugins/notes/manifest.toml
git commit -m "feat(surface): 声明式 tool 可声明 presentation/attention——notes.keep 走 Inline"
```

---

## Task 2: 窄规则 explicit 匹配器

**Files:**
- Create: `desktop/src/lib/explicit-intent.ts`
- Test: `desktop/src/lib/explicit-intent.test.ts`

**Interfaces:**
- Produces: `matchExplicitOpen(text: string, plugins: { id: string; name: string }[]): string | null` —— 命中返回插件 id，否则 `null`

`vitest.config.ts` 的 `include` 已覆盖 `src/lib/**/*.test.ts`（Phase 1 已扩），无需再改。

- [ ] **Step 1: 写失败测试**

```ts
// desktop/src/lib/explicit-intent.test.ts
import { describe, expect, it } from "vitest";
import { matchExplicitOpen } from "./explicit-intent";

const PLUGINS = [
  { id: "notes", name: "闪念盘" },
  { id: "calendar", name: "日历" },
];

describe("matchExplicitOpen", () => {
  it("动词 + 插件名 → 命中", () => {
    expect(matchExplicitOpen("打开闪念盘", PLUGINS)).toBe("notes");
  });

  it("动词 + 插件 id → 命中", () => {
    expect(matchExplicitOpen("展开 calendar", PLUGINS)).toBe("calendar");
  });

  it("只有动词没有宾语 → 不命中", () => {
    expect(matchExplicitOpen("打开", PLUGINS)).toBeNull();
  });

  it("只有插件名没有动词 → 不命中", () => {
    // 「闪念盘里有牛奶吗」是查询不是打开指令，绝不能判成 explicit
    expect(matchExplicitOpen("闪念盘里有牛奶吗", PLUGINS)).toBeNull();
  });

  it("弱动词不算明确意图", () => {
    // 「看看」「查查」语气太弱，误判的代价是抢屏——宁可漏
    expect(matchExplicitOpen("看看闪念盘", PLUGINS)).toBeNull();
  });

  it("空插件列表 → 不命中", () => {
    expect(matchExplicitOpen("打开闪念盘", [])).toBeNull();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && npx vitest run src/lib/explicit-intent.test.ts`
Expected: FAIL —— `Failed to resolve import "./explicit-intent"`

- [ ] **Step 3: 实现**

```ts
// desktop/src/lib/explicit-intent.ts
/**
 * 窄规则：从用户消息里识别「明确要求打开某个插件」。
 *
 * 存在的理由：小窗没有大窗插件库那种点击信号，用户的明确意图只能从话里认。
 * sidecar 认不了——agent loop 视角里「用户要求所以模型调了 list」和「模型
 * 自己想调 list」是同一个形状；让模型自报 explicit 又等于让被守的人当守门人。
 *
 * 刻意做窄：动词与宾语都取自有限集合。漏报只是退化成可点行、用户点一下（语义
 * 仍通顺），误报却会抢屏——成本不对称，所以宁可漏。
 */

/** 只认强指令动词。「看看」「查查」「有没有」这类语气太弱，是查询不是导航。 */
const OPEN_VERBS = ["打开", "展开", "显示", "调出", "给我看"];

/** 句末疑问语气：用户在问，不是在下打开指令。 */
const INTERROGATIVE_END = /[吗呢？?]\s*$/;

export function matchExplicitOpen(text: string, plugins: { id: string; name: string }[]): string | null {
  if (!text || !plugins.length) return null;
  const t = text.trim();
  if (INTERROGATIVE_END.test(t)) return null;
  if (!OPEN_VERBS.some((v) => t.startsWith(v))) return null;
  const hit = plugins.find((p) => (p.name && t.includes(p.name)) || (p.id && t.includes(p.id)));
  return hit ? hit.id : null;
}
```

**判据是「整句以强动词开头」，不是「句中含强动词」。** 这条锚定是本模块的核心：否定（「不要打开日历」）、疑问（「能打开日历吗」）、陈述（「闪念盘里有牛奶吗」）都会把别的词放在动词前面，因而天然不命中，**不需要枚举任何 blocklist**。用两个全串 `includes` 的写法会让这三类全部误命中并抢屏——那正是本模块最该避免的。句末疑问词是唯一的补充规则，兜底「打开日历了吗」这类动词确实开头的疑问句。

> **已知边界（不修）：** 「打开闪念列表」匹配不到名为「闪念盘」的插件；「帮我打开日历」因礼貌前缀破坏锚定也不命中。两者都退化成可点行加一次点击——宁缺毋滥的预期代价。真机验收若发现漏报过多，再考虑白名单少量礼貌前缀或把面板标题纳入匹配材料。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd desktop && npx vitest run src/lib/explicit-intent.test.ts`
Expected: PASS（6 条）

- [ ] **Step 5: Commit**

```bash
git add desktop/src/lib/explicit-intent.ts desktop/src/lib/explicit-intent.test.ts
git commit -m "feat(surface): 窄规则识别明确打开意图——宁缺毋滥，漏报退化为一次点击"
```

---

## Task 3: 形态映射与表面属性

**Files:**
- Create: `desktop/src/lib/pet-surface.ts`
- Test: `desktop/src/lib/pet-surface.test.ts`

**Interfaces:**
- Consumes: `Presentation` 类型（`desktop/src/lib/surface-policy.ts` 导出，值为 `"inline" | "peek" | "stage" | "focus"`）
- Produces:
  - `interface SurfaceAttr { panel: string; title: string; count: number | null; live: boolean }`
  - `petFormOf(d: { presentation: Presentation | null; show: boolean }): "window" | "line"`
  - `surfaceCount(data: unknown): number | null`
  - `deactivateAll(rows: { surface?: SurfaceAttr }[]): void`

- [ ] **Step 1: 写失败测试**

```ts
// desktop/src/lib/pet-surface.test.ts
import { describe, expect, it } from "vitest";
import { deactivateAll, petFormOf, surfaceCount, type SurfaceAttr } from "./pet-surface";

describe("petFormOf", () => {
  it("stage → 开窗", () => {
    expect(petFormOf({ presentation: "stage", show: true })).toBe("window");
  });

  it("focus → 开窗", () => {
    expect(petFormOf({ presentation: "focus", show: true })).toBe("window");
  });

  it("inline → 行", () => {
    expect(petFormOf({ presentation: "inline", show: true })).toBe("line");
  });

  it("peek → 行（小窗只有行一种形态）", () => {
    expect(petFormOf({ presentation: "peek", show: true })).toBe("line");
  });

  it("不展开（quiet）→ 行", () => {
    expect(petFormOf({ presentation: null, show: false })).toBe("line");
  });

  it("show=false 时即便带着 stage 也不开窗", () => {
    // 裁决器非 explicit 本就封顶 peek，这里是双保险：
    // 「模型自动调用绝不在小窗开浮窗」不能依赖单一判断
    expect(petFormOf({ presentation: "stage", show: false })).toBe("line");
  });
});

describe("surfaceCount", () => {
  it("db 类结果数 rows", () => {
    expect(surfaceCount({ rows: [1, 2, 3] })).toBe(3);
  });

  it("没有 rows → null（只显示面板名）", () => {
    expect(surfaceCount({ id: "abc" })).toBeNull();
  });

  it("非对象输入不炸", () => {
    expect(surfaceCount(null)).toBeNull();
    expect(surfaceCount(undefined)).toBeNull();
    expect(surfaceCount("x")).toBeNull();
  });
});

describe("deactivateAll", () => {
  it("所有带表面的行一律失活——流里永远最多一条可点", () => {
    const attr = (live: boolean): SurfaceAttr => ({ panel: "notes:list", title: "闪念列表", count: 3, live });
    const rows = [{ surface: attr(true) }, {}, { surface: attr(true) }];
    deactivateAll(rows);
    expect(rows[0].surface!.live).toBe(false);
    expect(rows[2].surface!.live).toBe(false);
  });

  it("已失活的行保持失活（幂等）", () => {
    const rows = [{ surface: { panel: "p", title: "t", count: null, live: false } }];
    deactivateAll(rows);
    expect(rows[0].surface.live).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd desktop && npx vitest run src/lib/pet-surface.test.ts`
Expected: FAIL —— `Failed to resolve import "./pet-surface"`

- [ ] **Step 3: 实现**

```ts
// desktop/src/lib/pet-surface.ts
import type { Presentation } from "./surface-policy";

/**
 * 一行的「表面属性」：与「进度属性」（pstate）正交。
 * 进度回答「它在干嘛、做完没有」，表面回答「这里有个面板可以打开」。
 */
export interface SurfaceAttr {
  panel: string;
  title: string;
  /** 结果条数；取不到就只显示面板名 */
  count: number | null;
  /** 是否可点。新面板事件到达时旧行一律失活——流里永远最多一条可点 */
  live: boolean;
}

/**
 * 裁决器输出 → 小窗落地形态，二值。
 *
 * 小窗只有行一种形态（见 spec §2.1），所以 inline 与 peek 不再有形态差别；
 * 剩下的唯一区别是「是否允许开窗」，而那由 explicit 决定。
 *
 * 不需要给 decideSurface 加更严的自动上限：它在非 explicit 时本就封顶到 peek，
 * 因此 stage/focus 只可能在 explicit 时出现——「模型自动调用绝不在小窗开浮窗」
 * 由这个映射自动成立。
 */
export function petFormOf(d: { presentation: Presentation | null; show: boolean }): "window" | "line" {
  if (!d.show || d.presentation === null) return "line";
  return d.presentation === "stage" || d.presentation === "focus" ? "window" : "line";
}

/** db 类声明式工具恒返回 {rows:[…]}，据此白拿计数；其它形状取不到就不显示。 */
export function surfaceCount(data: unknown): number | null {
  if (!data || typeof data !== "object") return null;
  const rows = (data as { rows?: unknown }).rows;
  return Array.isArray(rows) ? rows.length : null;
}

/** 新面板事件到达：此前所有行的表面属性失活（只剩历史，不再是入口）。 */
export function deactivateAll(rows: { surface?: SurfaceAttr }[]): void {
  for (const r of rows) if (r.surface?.live) r.surface.live = false;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd desktop && npx vitest run src/lib/pet-surface.test.ts`
Expected: PASS（11 条）

- [ ] **Step 5: Commit**

```bash
git add desktop/src/lib/pet-surface.ts desktop/src/lib/pet-surface.test.ts
git commit -m "feat(surface): 小窗形态映射二值化——stage/focus 才开窗，其余落行"
```

---

## Task 4: 表面行组件

**Files:**
- Create: `desktop/src/components/SurfaceLine.vue`

**Interfaces:**
- Consumes: `SurfaceAttr`（Task 3）
- Produces: `<SurfaceLine :attr="…" @open="…" />` —— `live` 时可点并 emit `open`；否则不可点、不 emit

- [ ] **Step 1: 建组件**

```vue
<!-- desktop/src/components/SurfaceLine.vue -->
<script setup lang="ts">
// 带表面属性的行（Phase 1.5）：可点时是入口，失活后是历史。
// 小窗不做卡——卡不承载撤销，多提供的信息接近于零，却在 360px 里顶满一整块。
// 不显示 ✓：失败结果本就不带表面建议，「有表面」已蕴含「成功了」。
import type { SurfaceAttr } from "../lib/pet-surface";

const props = defineProps<{ attr: SurfaceAttr }>();
const emit = defineEmits<{ open: [] }>();

function onOpen(): void {
  if (props.attr.live) emit("open");
}
</script>

<template>
  <div
    :class="['s-line', attr.live ? 'is-live' : 'is-past']"
    :role="attr.live ? 'button' : undefined"
    :tabindex="attr.live ? 0 : undefined"
    @click="onOpen"
    @keydown.enter="onOpen"
    @keydown.space.prevent="onOpen"
  >
    <span>{{ attr.title }}<template v-if="attr.count !== null"> · {{ attr.count }} 条</template></span>
    <span v-if="attr.live" class="sl-ar" aria-hidden="true">›</span>
  </div>
</template>

<style scoped>
.s-line {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: var(--yb-space-1);
  padding: 2px 2px;
  font-size: var(--yb-fs-sm);
  /* 无边无底：行不该有卡感 */
  background: transparent;
  border: none;
}
.is-live {
  color: var(--yb-accent);
  cursor: pointer;
}
.is-live:hover {
  text-decoration: underline;
}
/* 失活：只剩「发生过什么」，不承担导航——回插件视图才是导航入口 */
.is-past {
  color: var(--yb-text-faint);
  cursor: default;
}
.sl-ar {
  color: var(--yb-text-faint);
}
</style>
```

- [ ] **Step 2: 类型与构建闸门**

Run: `cd desktop && npx vue-tsc --noEmit && npx vite build`
Expected: 两者 exit 0

- [ ] **Step 3: Commit**

```bash
git add desktop/src/components/SurfaceLine.vue
git commit -m "feat(surface): 表面行组件——可点是入口，失活是历史"
```

---

## Task 5: 小窗接线

**Files:**
- Modify: `desktop/src/App.vue` —— `BubbleMsg`（:54-62）、`launchPlugin`（:449-456）、`action_proposed`（:558-565）、`case "panel"`（:727-738）、`submit`（:770）、气泡渲染（:1155-1166）

**Interfaces:**
- Consumes: `matchExplicitOpen`（Task 2）、`petFormOf` / `surfaceCount` / `deactivateAll` / `SurfaceAttr`（Task 3）、`SurfaceLine.vue`（Task 4）、`decideSurface`（`desktop/src/lib/surface-policy.ts`，不改动）

- [ ] **Step 1: 扩 BubbleMsg 与导入**

`App.vue:54-62` 的 `BubbleMsg` 增加一个可选字段。**表面属性直接挂在既有气泡上**，不建平行数据结构——这正是「一行 = 一个动作，进度与表面是两个属性」的落地：

```ts
type BubbleMsg = {
  role: "user" | "ai" | "sys";
  text: string;
  pstate?: "run" | "ok" | "fail";
  halted?: boolean;
  icon?: "clock" | "alert" | "doc";
  /** 晨间反刍 deep-link：morning_recap 提醒气泡携带 day 字符串时，点击切到 home 回顾视图 */
  recap?: string;
  /** 表面属性（Phase 1.5）：与 pstate 正交。有则该行渲染为面板入口 */
  surface?: SurfaceAttr;
};
```

在 `<script setup>` 导入区加：

```ts
import SurfaceLine from "./components/SurfaceLine.vue";
import { matchExplicitOpen } from "./lib/explicit-intent";
import { decideSurface, type Attention, type Presentation } from "./lib/surface-policy";
import { deactivateAll, petFormOf, surfaceCount, type SurfaceAttr } from "./lib/pet-surface";
```

`Attention` / `Presentation` 是 Step 4 的 cast 需要的——`PanelPayload` 里这些字段声明为宽类型。

- [ ] **Step 2: 加 explicit 时间窗与锚点表**

在 `const bubbles = ref<BubbleMsg[]>([]);`（:86）附近加（与 `HomePlugins.vue:61-62` 同构）：

```ts
// explicit 时间窗：插件视图点击 / 窄规则命中两个来源共用
let requestedPlugin = "";
let requestedUntil = 0;
function markExplicit(pluginId: string): void {
  requestedPlugin = pluginId;
  requestedUntil = Date.now() + 8000;
}

// action id → 过程行下标：panel 事件按 origin 找回该行补表面属性。
// 必须与 procIdx 分开：procIdx 在 action_result 就删了（:575），
// 而 panel 事件在 action_result 之后才到（loop.py:331 → :337）。
const surfaceAnchor = new Map<string, number>();
```

- [ ] **Step 3: 接 explicit 的两个来源**

**来源一（回归修复）** —— `launchPlugin`（:449）在 `panelAction` 之前置标志。**不做这一步，接上裁决器后点插件将开不了窗**（今天它能开纯粹因为 `case "panel"` 无条件开窗）：

```ts
async function launchPlugin(p: PluginInfo) {
  pluginErr.value = "";
  markExplicit(p.id);
  try {
    await panelAction(`${p.id}.list`, {});
  } catch (err) {
    requestedUntil = 0;
    pluginErr.value = "启动失败：" + String(err);
  }
}
```

**来源二** —— `submit`（:770）在 `bubbles.value.push({ role: "user", text })` 之后立刻跑窄规则，并清掉上一轮的锚点（锚点只在一次 run 内有意义，借此限制 Map 增长）：

```ts
  surfaceAnchor.clear();
  const wanted = matchExplicitOpen(text, plugins.value);
  if (wanted) markExplicit(wanted);
```

`plugins.value`（:408）由 `loadPlugins()`（:437）填充。用户若从未进过插件视图它是空数组，窄规则自然不命中——可接受的降级，用户点一下行即可。

- [ ] **Step 4: 抽开窗动作**

在 `launchPlugin` 附近加（`case "panel"` 与行点击共用，避免两处各写一遍收窗逻辑）。**必须先于 Step 5 建立**，Step 5 的代码会调用它：

```ts
/** 开面板浮窗 + 宠物窗收回球形态。
 *  行点击不必携带面板身份：可点行恒为最新那条，而面板窗内容渲染器本就
 *  跟着最新 panel 事件走，两者天然指向同一个面板。 */
function openPanelWindow(): void {
  panelOpen.value = true;
  void openPanel();
  if (expanded.value) void collapse();
}
```

- [ ] **Step 5: 记锚点并改写 case "panel"**

`action_proposed`（:561-564）在既有 `procIdx.set` 旁加一行：

```ts
      if (e.action?.id && !procSkip(e.action)) {
        procIdx.set(e.action.id, bubbles.value.length);
        surfaceAnchor.set(e.action.id, bubbles.value.length);
        bubbles.value.push({ role: "sys", text: procLabel(e.action), pstate: "run" });
      }
```

`App.vue:727-738` 整段替换：

```ts
    case "panel": {
      // 面板不再无条件弹独立浮窗（调研 §16 反模式）：先把表面属性补到发起它的
      // 那一行上（找不到就新建），再决定要不要开窗。stage/focus 只可能在
      // explicit 时出现——裁决器非 explicit 本就封顶 peek。
      const panel = e.payload?.panel ?? "";
      const title = e.payload?.title || panel || "插件面板";
      const plugin = panel.split(":", 1)[0] || panel;
      const explicit = requestedPlugin === plugin && Date.now() <= requestedUntil;
      requestedUntil = 0;

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
      const attr: SurfaceAttr = { panel, title, count: surfaceCount(e.payload?.data), live: true };
      const at = e.payload?.origin ? surfaceAnchor.get(e.payload.origin) : undefined;
      const row = at !== undefined ? bubbles.value[at] : undefined;
      if (row) row.surface = attr;
      else bubbles.value.push({ role: "sys", text: "", surface: attr });
      if (e.payload?.origin) surfaceAnchor.delete(e.payload.origin);

      if (petFormOf(decision) === "window") openPanelWindow();
      break;
    }
```

- [ ] **Step 6: 渲染**

渲染处（`App.vue:1155-1166`）把 `v-for` 上移到 `<template>`，有表面属性的行改走 `SurfaceLine`：

```html
        <template v-for="(b, i) in bubbles" :key="i">
          <SurfaceLine v-if="b.surface" :attr="b.surface" @open="openPanelWindow()" />
          <Bubble
            v-else
            :role="b.role"
            :text="b.text"
            :streaming="i === streamingIdx"
            :pstate="b.pstate"
            :halted="b.halted"
            :icon="b.icon"
            :class="{ 'recap-clickable': !!b.recap }"
            @click="onRecapClick(b.recap)"
          />
        </template>
```

> `v-for` 与 `:key` 只能留在 `<template>` 上，别在 `<Bubble>` 上也留一份。

- [ ] **Step 7: 类型、构建与单测闸门**

Run: `cd desktop && npx vue-tsc --noEmit && npx vite build && npx vitest run`
Expected: 三者 exit 0，vitest **94 passed**（基线 70 + Task 2 实际落地 13 + Task 3 的 11）

- [ ] **Step 8: Commit**

```bash
git add desktop/src/App.vue
git commit -m "feat(surface): 小窗接裁决器——面板落成过程行的表面属性，不再无条件弹浮窗"
```

---

## Task 6: 验收与实装记录

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-pet-window-surface-design.md`（末尾追加实装记录）
- Modify: `docs/superpowers/specs/2026-08-13-pet-os-mainline-design.md`（回填 Phase 1 验收条 ① 的阻塞标注）

- [ ] **Step 1: 四道闸门**

```bash
cd sidecar && uv run pytest
cd ../desktop && npx vue-tsc --noEmit && npx vite build && npx vitest run
cd src-tauri && cargo check
```
Expected: pytest 909 passed；vitest 94 passed；vue-tsc / vite build / cargo check 全 exit 0

- [ ] **Step 2: 真机验收**

`npm run tauri build -- --debug` 出包后逐条跑：

1. 小窗说「记一下这句话」→ 过程行**原地**变成 `闪念列表 · N 条 ›`，不新增一行、不开浮窗、宠物窗不收回球形态
2. 小窗任何模型自动调用 → 不开浮窗
3. 小窗说「打开闪念盘」→ 窄规则命中 → 直接开浮窗，且流里那一行仍可点
4. 小窗点插件视图里的插件 → 开浮窗（**回归项**，本阶段若做错这里会静默失效）
5. 连记三条 → 只有最新一行可点（accent + `›`），其余转为灰色不可点，流不刷屏
6. 无面板的动作（如记忆写入）→ 过程行仍显示 `✓`，不受影响
7. 大窗说「记一下这句话」→ **Inline 回执**而非 Peek（补齐 Phase 1 验收条 ①）

- [ ] **Step 3: 写实装记录**

按主线 spec §5 的文档纪律，在 `2026-08-13-pet-window-surface-design.md` 末尾追加「实装记录」段：落地日期、与设计的偏差、验证命令与结果、真机七条的逐条结论。同时把主线 spec 里 Phase 1 实装记录中验收条 ① 的「已知不成立，阻塞于 Phase 1.5」标注改为已通过。

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/
git commit -m "docs(surface): Phase 1.5 实装记录——小窗表面属性落地与真机验收结论"
```

---

## Self-Review 记录

**Spec 覆盖：** §2 进度与表面正交 → Task 5 Step 1（`BubbleMsg.surface` 与 `pstate` 并存）+ Step 4（按 `origin` 补属性，找不到就建）；§2.1 只有行没有卡 → Task 4 组件、Global Constraints「不动大窗」；§2.2 计数 → Task 3 `surfaceCount`；§2.3 开窗不携带面板身份 → Task 5 Step 5 `openPanelWindow`；§2.4 旧行不可点 → Task 3 `deactivateAll` + Task 4 `is-past` 不 emit；§3 二值映射 → Task 3 `petFormOf`；§4 explicit 三来源 → Task 2 + Task 5 Step 3（前两个）+ Step 5（行点击直接开窗，不需再置标志，因为不会触发新的 panel 事件）；§5 协议 → Task 1；§6 明确不做 → Global Constraints；§7 测试策略 → 各任务 Step 1 与 Task 6。

**类型一致性：** `SurfaceAttr` 在 Task 3 定义、Task 4 消费、Task 5 存进 `BubbleMsg.surface`，字段名 `panel/title/count/live` 三处一致。`petFormOf` 返回 `"window" | "line"` 是二值字符串联合，不再有第三值——Task 5 里只判 `=== "window"`。

**一处刻意的不对称：** 行点击**不**调 `markExplicit`。时间窗标志的作用是让**后续到达的 panel 事件**被判为 explicit，而行点击只是开窗、不触发新工具调用，因此无需置标志。`launchPlugin` 与窄规则则必须置——它们都会引发新的 panel 事件。
