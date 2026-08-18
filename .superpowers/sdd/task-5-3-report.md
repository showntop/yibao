# R4 阶段五 Task 3 报告：手机端 coding 待批卡（_mobile_state 只读合并 _PERM）

## 状态

完成。commit `e2b52b2`（分支 feat/r4-module-panel-runtime，worktree `.worktrees/r4-stage1`）。

## 根因（侦察结论复核）

手机 approvals 拉 `/v1/state` 的 pending（confirm_meta 展开）；coding 的 confirmation_needed
只广播不落 confirm_meta → pending 永远没有 perm_ 条目。裁决回传链路（`_confirm_mobile`
perm_ 前缀 → `_fulfill_coding_perm`）原本已通，本次仅修其返回值。

## 改动内容

1. **`plugins/coding/skills/_runner.py`**
   - 文件头 import 补 `time`。
   - `make_permission_callback._cb` 建 entry 处加 `"created_at": int(time.time())`
     （原 :100-102 区域）；顶部 `_PERM` 注册表注释同步补 created_at 字段说明。
2. **`sidecar/src/yibao_brain/server.py`**
   - 新抽模块级辅助 `_coding_perm_registry()`（:284-290）：
     `sys.modules.get("yibao_plugin_coding__runner")` → `_PERM` dict or None。
   - `_fulfill_coding_perm` 改用它，行为不变（既有用例
     `test_fulfill_coding_perm_routes_and_idempotent` 原样通过）。
   - `_mobile_state`（:1326 起）：pending 在 confirm_meta 展开后追加 `_PERM` 中
     `allow is None` 的挂起项，元素键名对齐 confirm_meta 展开结构
     （`id/skill_id/summary/risk/created_at`）：skill_id 固定 "coding"、risk 固定 1、
     summary 取 `entry.summary or entry.tool or "编码审批"`、created_at 缺省 0。
     docstring 补「只读合并、生命周期随 _PERM.pop 收敛」说明。
   - 顺手修：`_confirm_mobile` 的 perm_ 分支按 `_fulfill_coding_perm` 返回值返
     True/False（原恒 True 假 ok）；兑现失败不再写 `_confirm_done`。
3. **`sidecar/tests/test_server.py`**（P2 B1 组末尾新增，
   `test_mobile_state_merges_coding_perm_pending`）
   - fake 模块单例 `_PERM` 两条（一挂起一已裁决）→ 真 HTTP 拉 `/v1/state`：
     pending 含且仅含挂起项，五字段逐一断言。
   - 钉死顺手修：未知 perm_ rid POST `/v1/confirm` → 404（修前恒 200）。
   - 手机裁决挂起项 → 200 + allow/event 落 `_PERM` + 条目从 pending 收敛。
   - 服务就绪用 `/v1/health` 轮询（固定 sleep 在机器慢时假红，首跑已踩到），
     端口 19869（19862-19868 已被上游用例占用）。

## 验证

- TDD：新用例先红（`assert [] == ['perm_cs1_1']`，确认缺合并源）→ 实现后转绿。
- 相关用例：`test_fulfill_coding_perm_routes_and_idempotent`、
  `test_serve_confirm_batch_routes_coding_perm` 均过（行为不变确认）。
- 闸门：`cd sidecar && .venv/bin/pytest tests/ -q` → **1097 passed in 29.91s**
  （基线 1096 + 新增 1，全绿）。

## Concerns

- **`sidecar/uv.lock` 有与本任务无关的未提交改动**（625±：registry 全部从 pypi.org
  换成清华 tuna 镜像，系 worktree 建 venv 时的环境产物）。未 stage、未动，留在工作区，
  后续任务/终审注意别误带进提交。
- 既有用例无需同步改：`test_fulfill_coding_perm_routes_and_idempotent` 测的是
  `_fulfill_coding_perm` 本身（返回值语义未变）；`_confirm_mobile` perm_ 分支此前无
  用例覆盖，本次新用例已补上（含 404 负路径）。
- `_mobile_state` 合并且 `_PERM` 由 runner 线程写、HTTP 协程读：dict 遍历期间
  `_PERM.pop` 理论上可致 `RuntimeError`（迭代中改尺寸）。CPython 下概率极低
  （60s 超时窗内一次 O(几) 遍历），与既有 `coding.perm_pending` 直读同款风险口径，
  未加锁/快照，如终审要求可加 `list(perm.items())` 快照（一行）。

## 评审修复（R4 阶段五 T3 终审必修）

- `server.py:1336`（`_mobile_state` 合并 `_PERM` 处）`for rid, entry in perm.items():`
  改为 `for rid, entry in list(perm.items()):`——跨线程迭代中 runner 线程可能
  `_PERM.pop` 致 `RuntimeError`（本报告 Concerns 末条已预判此风险），终审要求补快照，
  对齐既有三处遍历口径（`_runner.py:25`、`coding.py:616`、`coding.py:1140`）。
- 闸门：`cd sidecar && .venv/bin/pytest tests/test_server.py -q` → **89 passed**（全绿）。
