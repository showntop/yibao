# 后台盯命令（watch mode slice 4）设计

日期：2026-07-31  关联：[[v1-status]]（watch mode slice 4 = 后台盯任务，第一刀=跑命令盯完成）。

## 目标 / 非目标
**目标**：新增 `watch_command` 技能——后台跑一条 shell 命令（编译/下载/测试/长跑脚本），立即返回不阻塞；完成或失败时**主动报告**（退出码 + 末尾输出）。
**非目标**：轮询条件、盯 app/窗口（后续子项）；不解析命令安全性（交给现有风险闸门确认）。

## 架构
- **复用现有主动事件通道**：`SkillContext.emit_event`（已存在，plugins 的 agents 任务完成通知在用）= `{kind:"reminder",text:…}` → 经 `_gate_proactive_event`（`proactive.level`）→ 气泡/Feed/语音。slice 4 只需把该通道接到**真实技能**。
- **`WatchCommandSkill`**（`skills_real.py`）：
  - `id=watch_command`，`default_risk=L3_HIGH`（跑任意 shell → 走确认闸门；L2+ 需 confirm）。
  - `run(params, ctx)`：取 `command`（必填）+ `name`（可选别名）；**起 daemon 线程**跑 `subprocess.run(command, shell=True, capture, timeout=600)`；**立即返回** `ActionResult(success, data={"started",...})`。
  - 线程内：完成/失败/超时 → 组装短文本 → `ctx.emit_event({"kind":"reminder","text":…})`（无 emit 则静默，不崩）。末尾输出取后 500 字。
- **接线（3 处小改，真实技能拿到 emit_event）**：
  1. `ToolInvoker.__init__` 加 `self.emit_event = None`；`execute` 里对真实技能 ctx 设 `ctx.emit_event = self.emit_event`（不覆盖插件已注入的）。
  2. `serve_async`：把现有 `_emit` 闭包（`call_soon_threadsafe(_on_plugin_event)`）在 build 后赋给 `agent.invoker.emit_event`。
  3. 系统提示加一句：长耗时任务用 `watch_command` 后台盯、完成会自动报告。
- **并发/取消**：每次调用起独立 daemon 线程，多任务并发；进程退出时 daemon 线程随进程结束（可接受）。

## 隐私 / 安全
- `shell=True` 跑 LLM/用户给的命令——**风险闸门 L3 强制确认**，用户明示批准才跑；不在这层做命令白名单（YAGNI）。
- 不上云、不读屏幕；隐私零增量。

## 测试（不阻塞、确定性）
- `WatchCommandSkill`：① `echo hello` → 立即返回 started；后台线程完成后 emit `完成…hello`；② `exit 7` → emit `失败…7`；③ 无 emit_event → 不崩；④ 缺 command → 失败。用短轮询等线程完成。
- `ToolInvoker`：设 `invoker.emit_event` → 真实技能 ctx.emit_event 被注入（不覆盖插件）。

## 验收（用户）
- [ ] 让译宝「后台盯 `npm run build`（或任意长命令）」→ 确认后立即回「已开始盯」；完成收到结果播报（成功/失败 + 末尾几行）。
- [ ] 「主动找我」=安静 → 完成只落动态不响；=完整 → 气泡+语音。
- [ ] 多个 watch_command 并发互不干扰。
