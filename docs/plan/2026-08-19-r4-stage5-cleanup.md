# R4 阶段五：收口 + 留档小修 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** R4 收口——wall.schema.json 退役（会话墙能力已由 studio 左栏接替）+ 留档小修打包（codex resume brief fallback、usage baseline 持久化、手机端 coding 待批卡、takeover 非 file contexts 丢弃[退役即关闭]）+ 阶段三/四累积小修。

**Architecture:** 后端三件小修各就其位（coding.py/_runner.py/server.py）；wall 退役走彻底路线（含 coding_sessions 事件链整链拆除——侦察确认 app/mobile 已无消费者）；面板/前端只做删除与小修，无新功能。

**spec:** `docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md`（落地顺序第 5 步）
**前序:** 阶段四终审 Yes（HEAD `737e582`）；四件小修侦察结论见台账与下文各任务「侦察依据」

## Global Constraints

- 施工目录：worktree `/Users/denny/Work/yibao/.worktrees/r4-stage1`，**绝不碰主检出**。
- 中文注释全角标点，与所在文件风格一致。
- 用户明示：不要考虑兼容、不要考虑改动量——wall 退役走彻底路线。
- 闸门：后端任务 `cd sidecar && .venv/bin/pytest tests/ -q`（基线 1092）；面板/前端任务 `cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck`（基线 133）/ `cd app && pnpm test && pnpm build`（基线 115）。
- commit message 中文，对齐 git log 风格。

## 留档小修四件处置总表

| 条目 | 处置 | 任务 |
|---|---|---|
| codex resume brief fallback | 后端自动 fallback（resume 零事件失败 → 摘要新会话续跑） | T1 |
| usage baseline 持久化 | sessions 表加列，每轮落库/回填（实际缺陷比 spec 记录更重：每次多轮 resume 都漂移，不止重启） | T2 |
| 手机端 coding 待批卡 | `_mobile_state` pending 只读合并 `_PERM` 挂起项（事件链不动、移动端零改动） | T3 |
| takeover 非 file contexts 丢弃 | **关闭，无代码**：转发层已随阶段三 T7 退役（98da05a），丢弃路径不复存在 | — |

---

### Task 1: codex resume brief fallback（resume 失败自动摘要续跑）

**侦察依据：** `codex exec resume` 失败（encrypted_content bug）时 stdout 零事件、未捕获 thread.started（`cc_sid is None`）、runner 补发 error 事件（`_codex_runner.py:289-297`）→ `_stream` 落 failed，用户只能手动新会话+brief。本任务自动化该补救（v2-codex-driver spec:16/37 留档）。

**Files:**
- Modify: `plugins/coding/skills/coding.py`（`_spawn_stream`/`_stream`/SendSkill 调用点）
- Test: `sidecar/tests/test_codex_runner.py`

**Interfaces:**
- Consumes: 既有 `_build_brief`（coding.py 引自 `_brief.py`）、SessionBriefSkill 的 messages 尾 40 条查询（coding.py:847-849）、节选兜底文案（:858-861）
- Produces: resume 失败一次性自动 fallback；无新 API

- [ ] **Step 1: 失败测试**（test_codex_runner.py 追加；fake runner 形态参照该文件既有用例）

```python
def test_resume_failure_falls_back_to_brief_new_session():
    """codex resume 零事件失败 → 自动用摘要新开会话续跑：
    二次调用 resume_session_id is None、prompt 含【交接上下文】、终态 done、cc_session_id 更新。"""
    # fake runner：第一次返 error 事件（无 thread.started）；第二次正常 done 且带新 thread_id
    # 断言：runner.run 被调两次；第二次 resume_session_id is None；
    #       第二次 prompt 含 "【交接上下文】" 与原用户 prompt；messages 表有 fallback marker 留痕
```

- [ ] **Step 2: 跑确认失败**

Run: `cd sidecar && .venv/bin/pytest tests/test_codex_runner.py -k fallback -q`
Expected: FAIL

- [ ] **Step 3: 实现**（coding.py）

1. `_spawn_stream`（:120-123）与 `_stream`（:163-166）签名加 `llm=None`；SendSkill 调用点（:466-467）透传 `ctx.llm`（StartSkill 无 resume 不动）。
2. `_stream` 内 `runner.run` 返回后、定终态前插一次性 fallback 分支，判据（「resume 根本没跑起来」）：
```python
if (agent == "codex" and resume_session_id and cc_sid is None
        and state["error"] and not cancel.is_set() and llm is not None):
```
   （cc_sid None = 未捕获 thread.started = stdout 零事件，恰是 resume 失败形态；turn 中途失败已捕获 thread_id 不触发；无 llm 时跳过 fallback 走原 failed 路径。）
3. 分支内：
   - 按 SessionBriefSkill 同款查 messages 尾 40 条 → turns；`_build_brief(llm, turns, git, "Codex", "Codex")`；llm 失败/空时复用 :858-861 节选兜底文案
   - `_persist("marker", "resume 失败，已用交接摘要新开会话续跑")` 留痕（marker 进面板流）
   - 以 `resume_session_id=None`、prompt=`"【交接上下文】\n" + brief + "\n\n【用户继续】\n" + 原 prompt` 重调 `runner.run` **一次**（再败自然落原 failed 路径，无循环）
   - 新 run 捕获的新 thread_id 由既有逻辑落库（:259-262 区域）
4. 头注/docstring 同步：`_stream` docstring 补 fallback 行为一句。

- [ ] **Step 4: 全量回归 + Commit**

Run: `cd sidecar && .venv/bin/pytest tests/ -q`（1092+新增 全绿）

```bash
git add plugins/coding/skills/coding.py sidecar/tests/test_codex_runner.py
git commit -m "feat: codex resume 失败自动 brief fallback——摘要新会话续跑(R4 阶段五 T1)"
```

---

### Task 2: usage baseline 持久化（sessions 表加列）

**侦察依据：** codex `turn.completed.usage` 是 thread 累计值；baseline 存 `_SESSIONS[sid]` 内存 entry，而 entry 每轮新建（只含 cancel）且流终 pop（coding.py:136-137/158）→ **第 2 轮起每轮 done 都报全量累计，token 逐轮重复计膨胀**（不止 spec 记的重启漂移）。测试盲区：test_codex_runner.py:220-233 复用同 entry dict，形态与生产脱节。

**Files:**
- Modify: `plugins/coding/manifest.toml`（sessions 表加列）
- Modify: `plugins/coding/skills/coding.py`（`_spawn_stream` 回填 + `_stream` done 分支落库）
- Test: `sidecar/tests/test_coding_plugin.py`

**Interfaces:**
- Consumes: `PluginDb.apply_schema` additive 迁移（plugindb.py:67-73，加列零迁移成本）；runner 就地写 `session_entry["usage_baseline"]`（_codex_runner.py:142，不动）
- Produces: sessions 表 `usage_baseline` TEXT 列（JSON 串 `{"input_tokens":N,"output_tokens":N}`）

- [ ] **Step 1: 失败测试**

```python
def test_usage_baseline_persisted_and_restored():
    """第二轮 send：sessions 行 usage_baseline 已落库;新 entry 从库回填(内存 entry 删除后仍差分正确)。"""
    # 夹具跑两轮 codex send;第一轮 done 后断言 DB 行 usage_baseline 非空 JSON;
    # 模拟重启(entry pop 后 _spawn_stream)第二轮 done 的 usage 是增量而非全量
```

（实施者按 test_coding_plugin.py 既有 codex 夹具形态补全；关键断言：第二轮 done 事件的 usage 为增量。）

- [ ] **Step 2: 跑确认失败** → **Step 3: 实现**

1. `manifest.toml` sessions `[[table]]` 加列：`{name = "usage_baseline", type = "text", default = ""}`（列定义语法以该文件既有列为准）。
2. `coding.py` `_spawn_stream`：新建 entry 后查 sessions 行，`usage_baseline` 非空则 `json.loads` 回填进 `_SESSIONS[sid]`（try/except 静默，坏数据不炸流）。CC 引擎无害（其 runner 不读该键）。
3. `coding.py` `_stream` 的 `on_event` done 分支：从 `_SESSIONS.get(sid)` 读 `usage_baseline`（runner 此刻已更新），`db.update("sessions", sid, {"usage_baseline": json.dumps(base)})`；try/except 与 `_persist` 同风格隔离。注意 db.update 的实际签名以 plugindb.py:142 为准（可能按 where dict 而非主键——照 SendSkill :465 的既有 update 写法）。
4. 注释：`_report_final` docstring 的「usage 不落库」措辞修订（baseline 落库 ≠ usage 落库，说清）。

- [ ] **Step 4: 全量回归 + Commit**

```bash
git add plugins/coding/manifest.toml plugins/coding/skills/coding.py sidecar/tests/test_coding_plugin.py
git commit -m "fix: codex usage baseline 落库——修多轮 resume token 全量重复计(R4 阶段五 T2)"
```

---

### Task 3: 手机端 coding 待批卡（_mobile_state 只读合并 _PERM）

**侦察依据：** 手机 approvals 走 `/v1/state` 的 pending（=confirm_meta 全量，`server.py:695/737`）；coding 的 confirmation_needed 只广播不落 confirm_meta（proactive.py:53-58）→ 手机收到 SSE 帧触发 refresh 但 pending 里永远没有 perm_ 条目。裁决回传已通（`_confirm_mobile` perm_ 前缀路由 `_fulfill_coding_perm`，server.py:1303-1307）。修法：pending 只读合并 `_PERM` 挂起项，生命周期靠 `_PERM.pop` 自动收敛。

**Files:**
- Modify: `plugins/coding/skills/_runner.py`（entry 加 created_at）
- Modify: `sidecar/src/yibao_brain/server.py`（`_mobile_state` 合并 + `_confirm_mobile` 返回值顺手修）
- Test: `sidecar/tests/test_server.py`

**Interfaces:**
- Consumes: `_PERM` entry（已有 event/allow/tool/summary/params，T1 阶段四）
- Produces: `/v1/state` 的 pending 含 coding 挂起项 `{id, skill_id:"coding", summary, risk:1, created_at}`

- [ ] **Step 1: 失败测试**（test_server.py，挂在 2799 行「P2 B1」测试组旁）

```python
def test_mobile_state_includes_coding_perm_pending(monkeypatch):
    """_PERM 挂起项合并进 _mobile_state 的 pending;已裁决(allow 非 None)不出现。"""
    # fake sys.modules["yibao_plugin_coding__runner"]._PERM 两条(一挂起一已裁决)
    # 调 _mobile_state() 断言 pending 含 perm 条目且字段齐(id/skill_id/summary/risk/created_at)
```

- [ ] **Step 2: 跑确认失败** → **Step 3: 实现**

1. `_runner.py` `make_permission_callback._cb` 建 entry 处加 `"created_at": int(time.time())`（文件头补 `import time`——若已 import 则免）。
2. `server.py`：抽小辅助 `_coding_perm_registry()`（`sys.modules.get("yibao_plugin_coding__runner")` → `_PERM` dict or None；`_fulfill_coding_perm` 改用它，行为不变）。
3. `_mobile_state`（:1318-1321）：pending 在 confirm_meta 展开后追加 `_PERM` 中 `allow is None` 项：
```python
{"id": rid, "skill_id": "coding", "summary": str(entry.get("summary") or entry.get("tool") or "编码审批"),
 "risk": 1, "created_at": int(entry.get("created_at") or 0)}
```
（entry 缺字段兜底；pending 的既有元素结构以 `_mobile_state` 现状为准对齐键名。）
4. 顺手修（侦察发现）：`_confirm_mobile` 的 perm_ 分支按 `_fulfill_coding_perm` 返回值返 True/False（现在恒 True 假 ok）；对应测试同步（既有用例 test_server.py:2813-2853 若断言恒 True 需改）。
5. 注释：`_mobile_state` docstring 补「coding 审批只读合并，生命周期随 _PERM.pop 收敛」。

- [ ] **Step 4: 全量回归 + Commit**

```bash
git add plugins/coding/skills/_runner.py sidecar/src/yibao_brain/server.py sidecar/tests/test_server.py
git commit -m "fix: 手机端 coding 待批卡——_mobile_state 只读合并 _PERM 挂起项(R4 阶段五 T3)"
```

---

### Task 4: wall 退役（彻底路线，含 coding_sessions 事件链拆除）

**侦察依据（删除清单已逐项核实）：** 墙能力已由 studio 左栏接替（rail=coding.sessions + 流事件派生活体 + 行内停止 + attach）；app 侧唯一消费者是 HomePlugins 的墙刷新；mobile 无引用；`coding_sessions` 事件除墙刷新外无消费者。

**Files:**
- Delete: `plugins/coding/panel/wall.schema.json`
- Modify: `plugins/coding/manifest.toml`（删 `[[panel]] wall` 段）
- Modify: `plugins/coding/api.toml`（删 wall_data/wall_stop 两段）
- Modify: `plugins/coding/skills/coding.py`（删 WallDataSkill/_LIVE_TEXT/_rel_time/_emit_sessions_changed 及 7 调用点 + make_tools 注册 + 注释改道）
- Modify: `sidecar/src/yibao_brain/proactive.py`（三元组去 coding_sessions + 注释）
- Modify: `app/src/components/HomePlugins.vue`（删 wallTimer/scheduleWallRefresh/coding_sessions case/清理点）
- Modify: `app/src/lib/brain.ts`（类型联合去 coding_sessions）
- Modify: `sidecar/tests/test_coding_plugin.py`（删 P2 B4 整段 6 用例 + make_tools 断言去 wall_data、「二十件」→「十九件」）
- Delete: `sidecar/tests/test_coding_sessions_events.py`
- Modify（注释同步）: `sidecar/tests/test_coding_plugin.py:539`、`sidecar/tests/test_codex_runner.py:511`

**Interfaces:** 无新接口；`coding.attach` 保留（Feed 任务卡「接管」路由不变）；studio rail 已覆盖墙语义（零新增代码）。

- [ ] **Step 1: 按上列清单删除/修改**，逐处注意：
  - `_emit_sessions_changed` 7 个调用点（coding.py:155/:295/:396/:468/:511/:1367/:1548）——:295 处注意保留 reminder 发射本体（只删 emit_sessions_changed 行）
  - coding.py 注释改道：AttachSkill docstring（:568/:578）、StudioSkill docstring（:669）的「会话墙/wall_data」措辞改指 studio 左栏
  - HomePlugins.vue 删除后 grep `wall` 与 `coding_sessions` 全 app/src 应零命中
  - 全仓 grep `wall_data|wall_stop|coding:wall|wall.schema|coding_sessions|_rel_time|_LIVE_TEXT|WallDataSkill` 收尾（命中只允许出现在 docs/ 历史文档与台账）
- [ ] **Step 2: 全闸 + Commit**

```bash
cd sidecar && .venv/bin/pytest tests/ -q
cd app && pnpm test && pnpm build
git add -A
git commit -m "refactor: 会话墙退役——wall.schema.json/wall_data/wall_stop/coding_sessions 事件链整拆(R4 阶段五 T4)"
```

---

### Task 5: 累积小修包（大脑孤儿回收匹配 + 三处死码/小修）

**Files:**
- Modify: `sidecar/src/yibao_brain/instance.py` + Test: `sidecar/tests/test_instance.py`
- Modify: `app/src/lib/at-mention.ts` + Test: `app/src/lib/at-mention.test.ts`
- Modify: `app/src/components/PanelApp.vue`
- Modify: `plugins/coding/panel/src/App.vue` + `plugins/coding/panel/src/lib/format.ts`（+ 其测试）

**逐项：**

- [ ] **1. 大脑孤儿回收匹配修复**（2026-08-18 实证 PID 90888 占锁事故）：`instance.py:20` `_BRAIN_PATTERN = "yibao_brain.server"` 是 pgrep 正则（`.` 匹配 `-` 能搜到入口点进程），但 `_is_brain_process` 的 Python 字面 `in` 检查认不出连字符入口名 `yibao-brain-server`（uv run 启动形态）→ 孤儿漏杀 → 单实例锁占死。修法：加 `_BRAIN_ENTRY = "yibao-brain-server"`，`_is_brain_process` 判据改 `(_BRAIN_PATTERN in cmd or _BRAIN_ENTRY in cmd) and ("python" in cmd or ".venv" in cmd)`。test_instance.py 加用例：入口点形 cmdline 判 True。
- [ ] **2. fileRefPaths 死导出**（阶段三终审发现）：`at-mention.ts:46` `fileRefPaths` 仅测试引用（takeover 退役后生产零消费）。删导出 + at-mention.test.ts 对应 describe 块。
- [ ] **3. PanelApp focusInput 误中**（pre-existing）：`PanelApp.vue:300-301` `querySelector("input")` 命中 InputBar 隐藏 file-input（主输入是 textarea）→ 点团子聚焦静默 no-op。改调 InputBar 已 expose 的 `focus()`（经 ref）。
- [ ] **4. review tap params 一行**（阶段四终审 backlog）：壳 demux tap 的 upsert 现写死 `params: {}` → 悬停空。format.ts 加 `permPublicParams(input)`（镜像后端 `_public_params`：command/file_path/path 取一截 200，否则 {}）+ 测试；App.vue tap 处改调它。
- [ ] **5. spec 状态行**：`docs/superpowers/specs/2026-08-17-coding-studio-r4-design.md` 落地顺序 5 步标注完成态（每步尾加 ✅(YYYY-MM-DD)），留档小修四件处置按本计划总表更新（takeover 件注「退役即关闭」）。

**闸门：**
```bash
cd sidecar && .venv/bin/pytest tests/ -q          # instance 用例
cd app && pnpm test && pnpm build                 # at-mention/PanelApp
cd plugins/coding/panel && pnpm test && pnpm build && pnpm typecheck  # format/App
```

```bash
git add -A
git commit -m "fix: 累积小修包——大脑孤儿回收入口点匹配/死导出/团子聚焦/review 悬停 params(R4 阶段五 T5)"
```

---

### Task 6: 全闸 + R4 全分支终审 + 总验收清单

- [ ] **Step 1: 四闸全跑**（panel / app / sidecar / cargo + prepare-dist）
- [ ] **Step 2: 全分支终审**（阶段五范围 737e582..HEAD；并回看全分支 Minor backlog 终局分诊）
- [ ] **Step 3: 总验收清单交付用户**（阶段三/四/五合一）

## Self-Review 记录

- spec 覆盖：wall 退役(T4)✓ 文档更新(T4 注释改道 + T5-5 spec 状态行)✓ 留档四件(T1/T2/T3/关闭件)✓
- 侦察发现的超预期项：usage baseline 缺陷实际比 spec 记录更重（每轮漂移非仅重启）——T2 按真实缺陷修
- 不碰：手机端代码（零改动达成）；codex 无审批钩子（E 条仅适用 CC 属预期）
