# R4 阶段四：统一 review 栏 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** studio 右栏统一 review：全量待批按会话分组、单条/组级裁决走 coding.decide；工位消息流内不再镜像待批卡；与 L2 确认条/收件箱/手机端双通道幂等同步消失。

**Architecture:** 侦察结论（替代 spec 的 permission_resolved 广播设计）：任何通道裁决后 runner 等待方恒发 `permission_done`（面板流）+ `action_result`（L2 出队），全表面同步信号已存在，后端无需新增广播；唯一缺口是「面板晚开错过 permission_request」——后端补一个 quiet 查询 `coding.perm_pending`（列 _PERM 挂起项）供面板挂载快照。面板侧：新增 review 聚合 store（壳层，跨工位/含未绑会话）+ ReviewRail 纯展示组件；session store 的 perm 卡镜像移除（waiting 状态保留）；壳 demux 在工位路由前先行 tap 权限事件。

**Tech Stack:** 面板 Vue 3 + vitest；sidecar pytest；无新增依赖。

**spec:** `docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md`（落地顺序第 4 步 + 验收 E）
**前序:** 阶段三已终审通过（分支 HEAD `f4dcd98`）

## Global Constraints

- 施工目录：worktree `/Users/denny/Work/yibao/.worktrees/r4-stage1`，**绝不碰主检出**。
- 中文注释全角标点，与所在文件风格一致。
- 幂等契约：裁决只经 `coding.decide`（_PERM 先到先得，L2 confirm_batched 直写同注册表）；卡片消失一律等 `permission_done` 流事件驱动（单一事实源），decide 失败（`权限请求不存在或已超时`）时本地兜底移除。
- 60s 超时 deny 是既有行为（`_runner.py` timeout_s=60），本阶段只验证不改。
- 闸门：面板 `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`（基线 132 绿）；后端任务加 `cd sidecar && .venv/bin/pytest tests/ -q`（基线 1089 绿）。
- commit message 中文，对齐 git log 风格。

## 文件结构

**后端：**
- `plugins/coding/skills/coding.py` — PermPendingSkill（T1）
- `plugins/coding/api.toml` — 注册 quiet 方法（T1）
- `sidecar/tests/test_coding_plugin.py` — 用例（T1；若该文件不合适，放最近的 coding 测试文件，实施者判断并披露）

**面板：**
- `plugins/coding/panel/src/stores/review.ts` + `review.test.ts` — 待批聚合 store（T2）
- `plugins/coding/panel/src/lib/format.ts` — `permSummary(tool, input)`（T2，镜像 `_runner._summarize_tool_input` 语义：command/file_path/path 取一、否则 json，单行截 80 字）
- `plugins/coding/panel/src/stores/session.ts` + `session.test.ts` — perm 卡镜像移除（T3）
- `plugins/coding/panel/src/components/MessageList.vue` — perm 分支删除（T3）
- `plugins/coding/panel/src/components/PermCard.vue` — 删除（T3）
- `plugins/coding/panel/src/components/ReviewRail.vue` — 纯展示右栏（T4）
- `plugins/coding/panel/src/App.vue` + `style.css` — 壳集成（T5）
- `plugins/coding/panel/src/lib/render-model.test.ts` — 若有 perm 断言同步改（T3 实施者核查）

---

### Task 1: 后端 coding.perm_pending（quiet 查询，面板挂载快照源）

**Files:**
- Modify: `plugins/coding/skills/coding.py`
- Modify: `plugins/coding/api.toml`
- Test: `sidecar/tests/test_coding_plugin.py`

**Interfaces:**
- Produces: `coding.perm_pending`（direct + quiet，L0 只读）→ `data.pending: [{rid, sid, tool, summary, params}]`
  - `sid`：rid 形如 `perm_<sid>_<seq>`，解析用 `rid[len("perm_"):].rsplit("_", 1)[0]`（seq 恒为尾部整数，sid 含下划线安全）
  - `summary`：`_runner._summarize_tool_input(tool, input)` 同语义——注册表 entry 不存 input，**entry 需扩字段**：`_runner.make_permission_callback` 的 `_PERM[rid]` 加入 `"tool": str(tool_name)` 与 `"summary": _summarize_tool_input(tool_name, input)`、`"params": _public_params(input)`（写 entry 三字段是唯一改 _runner.py 的地方）
  - pending 定义：`entry.get("allow") is None`
- 备注：`_live_state`（coding.py:527-535）遍历 _PERM 只看 allow，不受 entry 扩字段影响；`release_pending_permissions` 同理

- [ ] **Step 1: 写失败测试**（追加到 test_coding_plugin.py 或合适的 coding 测试文件；复用该文件既有夹具模式）

```python
def test_perm_pending_lists_only_undecided():
    """_PERM 挂起项列出 rid/sid/tool/summary/params；已裁决与已清理项不出现。"""
    import threading
    from yibao_plugin_coding import _runner  # 模块单例名以既有测试的 import 惯例为准
    _runner._PERM.clear()
    ev = threading.Event()
    _runner._PERM["perm_s1_1"] = {"event": ev, "allow": None,
        "tool": "Bash", "summary": "ls -la", "params": {"command": "ls -la"}}
    _runner._PERM["perm_s1_2"] = {"event": threading.Event(), "allow": True,
        "tool": "Edit", "summary": "a.py", "params": {"file_path": "a.py"}}
    # 调 PermPendingSkill.run({}, ctx)（ctx 用既有夹具的最小桩）
    # 断言：pending 仅 1 条；rid == "perm_s1_1"；sid == "s1"；tool == "Bash"；summary/params 透传
    _runner._PERM.clear()
```

（实施者按测试文件既有惯例补全 import 与 ctx 桩；断言以实际 API 返回形为准。另加一条 entry 缺 tool/summary 老字段的兼容用例：缺省返 "" 不炸。）

- [ ] **Step 2: 跑确认失败**

Run: `cd sidecar && .venv/bin/pytest tests/test_coding_plugin.py -k perm_pending -q`
Expected: FAIL（PermPendingSkill 不存在）

- [ ] **Step 3: 实现**

`_runner.py` `make_permission_callback` 的 entry 行（:97-98）改：
```python
        entry = {"event": threading.Event(), "allow": None,
                 "tool": str(tool_name), "summary": _summarize_tool_input(tool_name, input),
                 "params": _public_params(input)}
```

`coding.py` 新增（放在 DecideSkill 后）：
```python
class PermPendingSkill(Skill):
    """待批权限清单（studio review 栏挂载快照；quiet 只读）。

    面板晚开错过 permission_request 流事件时补齐全量挂起项；之后的增删由流事件驱动
    （permission_request 增 / permission_done 删）。rid 解析 sid：perm_<sid>_<seq>，rsplit 去尾序号。
    """
    id = "coding.perm_pending"
    label = "待批权限清单"
    description = "列出 coding 当前全部挂起中的工具权限请求（review 栏快照用；L0 只读）。"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.id,
                "description": self.description,
                "parameters": {"type": "object", "properties": {}, "required": []}}}

    def run(self, params: dict, ctx: Any) -> ActionResult:
        out = []
        for rid, entry in list(_PERM.items()):
            if not isinstance(entry, dict) or entry.get("allow") is not None:
                continue
            sid = rid[len("perm_"):].rsplit("_", 1)[0] if rid.startswith("perm_") else ""
            out.append({"rid": rid, "sid": sid,
                        "tool": str(entry.get("tool") or ""),
                        "summary": str(entry.get("summary") or ""),
                        "params": entry.get("params") if isinstance(entry.get("params"), dict) else {}})
        return ActionResult(success=True, data={"pending": out})
```

并在技能注册处登记（参照既有 Skill 注册惯例；实施者定位 `make_tools`/注册列表把 PermPendingSkill 加进去）。`api.toml` 追加：
```toml
# review 栏挂载快照：待批权限清单（quiet 不发 panel 事件）
[[method]]
name = "coding.perm_pending"
handler = "coding.perm_pending"
direct = true
quiet = true
```

- [ ] **Step 4: 跑确认通过 + 全量回归**

Run: `cd sidecar && .venv/bin/pytest tests/ -q`
Expected: 1089+新 全绿

- [ ] **Step 5: Commit**

```bash
git add plugins/coding/skills/coding.py plugins/coding/skills/_runner.py plugins/coding/api.toml sidecar/tests/test_coding_plugin.py
git commit -m "feat: coding.perm_pending——review 栏挂载快照源（_PERM 挂起项 quiet 查询）(R4 阶段四 T1)"
```

---

### Task 2: review 聚合 store（壳层，跨工位含未绑会话）

**Files:**
- Create: `plugins/coding/panel/src/stores/review.ts`
- Test: `plugins/coding/panel/src/stores/review.test.ts`
- Modify: `plugins/coding/panel/src/lib/format.ts`（加 permSummary）

**Interfaces:**
- Consumes: T1 的 pending 行形（snapshot 用）
- Produces（T4/T5 消费）:
  - `interface ReviewItem { rid: string; sid: string; tool: string; summary: string; params: Record<string, string> }`
  - `createReviewStore()` → `{ state, upsert, resolve, snapshot, groups }`
  - `state: { items: ReviewItem[] }`（插入序）
  - `upsert(item)`：rid 幂等（同 rid 覆盖，不重复入列）
  - `resolve(rid)`：移除（无此 rid 静默）
  - `snapshot(items: ReviewItem[])`：全量替换（挂载同步）
  - `groups: ComputedRef<Array<{ sid: string; items: ReviewItem[] }>>`（按 sid 分组，保插入序）
  - `permSummary(tool: string, input: unknown): string`（format.ts；command/file_path/path 取其一，否则 JSON.stringify，单行化截 80 字；镜像 `_runner._summarize_tool_input`）

- [ ] **Step 1: 失败测试**（review.test.ts）

```ts
import { describe, expect, it } from "vitest";
import { createReviewStore } from "./review";
import { permSummary } from "../lib/format";

const it1 = { rid: "perm_s1_1", sid: "s1", tool: "Bash", summary: "ls", params: { command: "ls" } };
const it2 = { rid: "perm_s1_2", sid: "s1", tool: "Edit", summary: "a.py", params: {} };
const it3 = { rid: "perm_s2_3", sid: "s2", tool: "Write", summary: "b.py", params: {} };

describe("review store", () => {
  it("upsert 按 rid 幂等;resolve 移除;groups 按 sid 分组保序", () => {
    const s = createReviewStore();
    s.upsert(it1); s.upsert(it2); s.upsert(it3);
    expect(s.state.items).toHaveLength(3);
    s.upsert({ ...it1, summary: "ls -la" }); // 同 rid 覆盖不重复
    expect(s.state.items).toHaveLength(3);
    expect(s.state.items[0]!.summary).toBe("ls -la");
    const g = s.groups.value;
    expect(g.map((x) => x.sid)).toEqual(["s1", "s2"]);
    expect(g[0]!.items.map((x) => x.rid)).toEqual(["perm_s1_1", "perm_s1_2"]);
    s.resolve("perm_s1_1");
    expect(s.state.items.map((x) => x.rid)).toEqual(["perm_s1_2", "perm_s2_3"]);
    s.resolve("perm_nonexistent_9"); // 静默
    expect(s.state.items).toHaveLength(2);
  });

  it("snapshot 全量替换", () => {
    const s = createReviewStore();
    s.upsert(it1);
    s.snapshot([it3]);
    expect(s.state.items.map((x) => x.rid)).toEqual(["perm_s2_3"]);
  });
});

describe("permSummary", () => {
  it("command/file_path/path 取一,否则 json;单行截 80", () => {
    expect(permSummary("Bash", { command: "ls -la" })).toBe("ls -la");
    expect(permSummary("Edit", { file_path: "/tmp/a.py" })).toBe("/tmp/a.py");
    expect(permSummary("Write", { path: "b.py" })).toBe("b.py");
    expect(permSummary("Read", { offset: 1 })).toBe('{"offset":1}');
    expect(permSummary("Bash", { command: "a\nb" })).toBe("a b");
    expect(permSummary("Bash", { command: "x".repeat(100) }).length).toBe(80);
    expect(permSummary("Bash", null)).toBe("{}");
  });
});
```

（注：`_summarize_tool_input` 对空 dict 走 `json.dumps({})` → `"{}"`，permSummary 对齐之。）

- [ ] **Step 2: 跑确认失败** → **Step 3: 实现** → **Step 4: 跑确认通过**

Run: `cd plugins/coding/panel && pnpm vitest run src/stores/review.test.ts`

- [ ] **Step 5: 闸门 + Commit**

```bash
git add plugins/coding/panel/src/stores/review.ts plugins/coding/panel/src/stores/review.test.ts plugins/coding/panel/src/lib/format.ts
git commit -m "feat: review 聚合 store——待批跨会话聚合/分组/快照 + permSummary(R4 阶段四 T2)"
```

---

### Task 3: 工位流内 perm 卡镜像移除（waiting 状态保留）

**Files:**
- Modify: `plugins/coding/panel/src/stores/session.ts`
- Modify: `plugins/coding/panel/src/stores/session.test.ts`
- Modify: `plugins/coding/panel/src/components/MessageList.vue`（删 perm 分支与 import）
- Delete: `plugins/coding/panel/src/components/PermCard.vue`
- Modify（如有 perm 断言）: `plugins/coding/panel/src/lib/render-model.test.ts`

**Interfaces:**
- Consumes: 无
- Produces: `permission_request`/`permission_done` 事件仍驱动 `state.waiting`（工位头 dot-waiting / 左栏活体依赖之）；`RenderItem` 联合类型**删 perm 变体**；store return 面不变

spec：「工位流内不再镜像待批卡」——审批唯一落点是 review 栏（T4/T5）。

- [ ] **Step 1: 改测试**：session.test.ts 中 perm 卡相关断言改为只断言 `state.waiting` 流转（waiting=true on request / false on done / 终态复位 false）；不再断言 perm RenderItem。render-model.test.ts 若有 perm 用例同步删。
- [ ] **Step 2: 跑确认失败**
- [ ] **Step 3: 实现**：session.ts 的 `applyEvent` 两分支改：
```ts
      case "permission_request":
        finalizeAssistant();
        state.waiting = true;   // 待批卡不再进消息流;统一落 review 栏(壳层聚合)
        return;
      case "permission_done":
        state.waiting = false;
        return;
```
删 `RenderItem` 的 perm 变体、MessageList 的 perm 渲染分支与 PermCard import、删 PermCard.vue 文件。grep `perm` 全 panel src 确认无残留引用（除 waiting 语义与 review store）。
- [ ] **Step 4: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`

```bash
git add -A plugins/coding/panel/src
git commit -m "refactor: 工位流内 perm 卡镜像移除——审批统一落 review 栏(R4 阶段四 T3)"
```

---

### Task 4: ReviewRail 组件（纯展示）

**Files:**
- Create: `plugins/coding/panel/src/components/ReviewRail.vue`

**Interfaces:**
- Consumes: T2 `ReviewItem`
- Produces（T5 装配）:
  - props: `{ groups: Array<{ sid: string; label: string; stationId: number | null; items: ReviewItem[] }>; drawer: boolean }`
    - `label`：壳算好传入（`stationId !== null` → `工位 {stationId}`；否则 sid slice(0,8)）
  - emits: `decide(rid: string, allow: boolean)`、`decide-group(sid: string, allow: boolean)`、`close-drawer`
- 纯展示：零 invoke/store；卡片区显 tool 名 + summary（params 细节 title 悬停）；单条「允许/拒绝」；组头「全批」+ 组内计数；drawer 模式同 SessionRail 范式（罩层 + 右滑出）
- class 名：`review-rail/review-head/review-group/review-group-head/review-card/review-card-tool/review-card-summary/review-allow/review-deny/review-approve-all/review-mask/review-drawer`（T5 样式钉这些名）

- [ ] **Step 1: 实现组件**（结构仿 SessionRail 双写范式；允许=主色钮、拒绝=次钮，样式 T5 落）
- [ ] **Step 2: 闸门 + Commit**

```bash
git add plugins/coding/panel/src/components/ReviewRail.vue
git commit -m "feat: 统一 review 栏组件(纯展示)(R4 阶段四 T4)"
```

---

### Task 5: 壳集成——demux tap/快照/右栏布局/裁决接线 + agent-picker 定位修

**Files:**
- Modify: `plugins/coding/panel/src/App.vue`
- Modify: `plugins/coding/panel/src/style.css`
- Modify（顺手，阶段三 backlog 裁定）: `style.css` 的 `#agent-picker` fixed→absolute 锚 `.station`

**Interfaces:**
- Consumes: T2 review store、T4 ReviewRail、T1 perm_pending
- Produces: 完整统一 review 栏

设计要点：

1. **demux tap**（在既有 demux 内，工位路由之前/并行）：
```ts
const ev = d.event;
if (ev && ev.kind === "permission_request") {
  review.upsert({ rid: String(ev.rid || ""), sid, tool: String(ev.tool || ""),
                  summary: permSummary(String(ev.tool || ""), ev.input), params: {} });
} else if (ev && ev.kind === "permission_done") {
  review.resolve(String(ev.rid || ""));
}
```
（对已绑 sid：tap 之后照常投递工位——工位 waiting 态仍由 store 维护。）

2. **挂载快照**：`invoke("coding.perm_pending", {})` → `review.snapshot(r.pending ?? [])`；失败静默（流事件会兜底后续增删）。

3. **裁决接线**：
```ts
async function onDecide(rid: string, allow: boolean) {
  try { await invoke("coding.decide", { rid, allow }); }
  catch { review.resolve(rid); } // 已超时/已被他通道裁决:本地兜底移除(幂等契约)
  // 成功路径不本地移除:等 permission_done 流事件驱动(单一事实源)
}
function onDecideGroup(sid: string, allow: boolean) {
  for (const it of review.state.items.filter((x) => x.sid === sid)) void onDecide(it.rid, allow);
}
```

4. **布局**：宽窗——review 栏仅 `review.state.items.length > 0` 时出列（右栏 260px，自动进出）；窄窗——不出列，改 stations 区右上角「审批 N」徽按钮（与 ☰ 对称），点击开 drawer（`reviewDrawerOpen` ref），drawer 内裁决后若清空自动收。
5. **groups props 装配**：`review.groups.value.map(g => ({ ...g, label/stationId: stations.stationForSid(g.sid) }))`。
6. **agent-picker 修**：`#agent-picker` fixed→absolute 锚 `.station`（阶段三终审 backlog 裁定顺手项）；验证 backdrop/esc 关闭路径不回归。
7. **样式**：review-rail 卡片/分组/按钮（对齐既有暗色调色板变量）；「审批 N」徽按钮窄窗才显。

- [ ] **Step 1: 实现**
- [ ] **Step 2: 闸门 + Commit**

Run: `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`

```bash
git add plugins/coding/panel/src/App.vue plugins/coding/panel/src/style.css
git commit -m "feat: 统一 review 栏壳集成——demux tap/挂载快照/右栏布局/裁决接线(R4 阶段四 T5)"
```

---

### Task 6: 全闸 + 阶段四终审

- [ ] **Step 1: 全闸**
```bash
cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck
cd sidecar && .venv/bin/pytest tests/ -q
cd desktop && pnpm test && pnpm build   # 回归确认(本阶段 app 侧零改动预期)
```
- [ ] **Step 2: 阶段四全分支终审**（范围：f4dcd98..HEAD），验收 E 逐条对证：
  - 两会话同时挂起 → review 栏按会话分组 ✓
  - 单条裁决、组级全批生效 ✓
  - L2 确认条/收件箱裁决后 review 栏卡片同步消失（permission_done 流驱动）✓ 反之亦然（action_result 既有）✓
  - 60s 超时 deny 兜底（既有，runner timeout_s=60）✓

## Self-Review 记录

- spec 覆盖：统一 review 栏(T2/T4/T5)✓ 工位流 perm 卡移除(T3)✓ permission_resolved 广播→裁定冗余,替代为 perm_pending 快照(T1,侦察依据=permission_done+action_result 双发已覆盖全表面)✓ 双通道幂等(既有 _PERM 先到先得,不动)✓ 60s 超时(既有,不动)✓ 窄窗抽屉(T5)✓
- 阶段三顺手项：agent-picker fixed→absolute(T5)✓
- 不碰：L2 确认条/收件箱/手机端（手机端看不到 coding 待批卡是阶段五留档小修）；wall 退役（阶段五）。
