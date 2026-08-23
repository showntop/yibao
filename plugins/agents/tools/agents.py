"""agents：智能体调度——把任务派给本机 CLI 智能体（Claude Code / Codex）后台执行，完成主动播报。

dispatch_task（L3，走用户确认）：Popen spawn 子进程后登记 db 立即返回；
daemon 线程 _wait 等进程结束 → 落库 → 经 ctx.emit_event 播报（前端亮窗+气泡+TTS）。
task_status（L0）查行 + log 尾部；task_stop（L2）terminate 在跑进程（句柄丢失时按 pid 兜底）。
新智能体接入：_AGENTS 加一项（bin + 结果摘要解析）+ _build_argv 加分支即可。
进程登记/等待收尾与 sandbox.py（code_exec 沙箱脚本）共用，见 _common.py。

写权限（C-1 教训：无沙箱时 claude 全程 permission_denials 空跑烧钱）：
- claude 套 macOS Seatbelt（sandbox-exec）：写限 cwd/logs/~/.claude，放开网络，policy 落盘可审计；
- codex 用内建 --sandbox workspace-write（macOS Seatbelt / Linux Landlock）。
底座重启后对账（make_tools 时）：running 行 pid 活着 → _reap_detached 探活收尾；已死 → interrupted。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from yibao_brain import config
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


def _sibling(stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules：全插件共享同一实例（_PROCS 唯一）。

    插件加载器不把模块挂 sys.modules，普通 import 拿不到同插件兄弟模块，只能按文件路径来。
    R-35 归一：加载逻辑收敛到 _common.load_sibling（公共件无兄弟依赖，无循环），
    本文件与 sandbox.py 均为薄委托。
    """
    return _load_common().load_sibling(Path(__file__).parent, "yibao_plugin_agents", stem)


def _load_common():
    """加载本目录 _common.py（固定路径内联；公共件不含兄弟依赖，无递归/无二次种子加载）。"""
    name = "yibao_plugin_agents__common"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("_common.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_common = _sibling("_common")

_SUMMARY_TAIL = _common._SUMMARY_TAIL  # 摘要解析失败/为空时退化为 log 尾部 500 字
_STATUS_TAIL = 2000  # task_status 返回的 log 尾部长度

# task_id → 在跑进程句柄（task_stop 用；底座重启后丢失，stop 按 pid 兜底）
_PROCS = _common._PROCS
_tail_text = _common._tail_text

# PATH 找不到 CLI 时的补查目录（GUI spawn 的大脑 PATH 很薄，fnm/本地安装的 CLI 常在以下位置）
_CLI_FALLBACK_DIRS = ("~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin")


def _find_cli(bin_name: str) -> str | None:
    """PATH 里找 CLI，失败再补查常见安装目录；找到返回完整路径，否则 None。"""
    path = shutil.which(bin_name)
    if path:
        return path
    for d in _CLI_FALLBACK_DIRS:
        candidate = os.path.join(os.path.expanduser(d), bin_name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _build_argv(agent_name: str, bin_path: str, prompt: str, cwd_real: str,
                logs_dir: str, task_id: str) -> tuple[list[str] | None, str | None]:
    """构造命令行（成功返回 argv, None；失败返回 None, 人话原因）。

    claude 必须套 Seatbelt 沙箱（无沙箱时 permission_denials 空跑烧钱，C-1 根因）：
    写限 cwd/logs/~/.claude、放开网络（要调 API）、--dangerously-skip-permissions 免交互授权；
    policy 落盘 logs/<task_id>.sb 可审计。codex 用内建 sandbox 无需外套。
    """
    if agent_name == "claude":
        sandbox_exec = shutil.which("sandbox-exec")
        if sandbox_exec is None:
            return None, "当前环境不支持派任务给 claude（需要 macOS 沙箱，找不到 sandbox-exec）"
        policy_path = os.path.join(logs_dir, f"{task_id}.sb")
        roots = [cwd_real, logs_dir, os.path.expanduser("~/.claude")]
        try:
            with open(policy_path, "w", encoding="utf-8") as f:
                f.write(_sibling("sandbox")._build_profile(roots, allow_network=True))
        except OSError as e:
            return None, f"写沙箱策略文件失败：{e}"
        return [sandbox_exec, "-f", policy_path, bin_path, "-p", prompt,
                "--dangerously-skip-permissions", "--output-format", "json"], None
    # codex：内建沙箱（macOS Seatbelt / Linux Landlock），写限工作区；--json 输出 JSONL 事件流
    return [bin_path, "exec", "--sandbox", "workspace-write", "--skip-git-repo-check",
            prompt, "--json"], None


def _claude_summary(log: str) -> str:
    """claude -p --output-format json：stdout 是事件数组，取最后一个 type=="result" 项的结果文本。"""
    events = json.loads(log)
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "result":
            return str(ev.get("result") or "").strip()
    return ""


def _codex_summary(log: str) -> str:
    """codex exec --json：stdout 按行 JSONL，取最后能解析的行（提取其中的文本字段，没有就用该行原文）。"""
    for line in reversed(log.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            for key in ("text", "message", "result", "content"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            item = obj.get("item")  # codex 事件流里 agent 消息常嵌在 {"item": {"text": …}}
            if isinstance(item, dict):
                v = item.get("text")
                if isinstance(v, str) and v.strip():
                    return v.strip()
        return line
    return ""


# 适配表：bin（PATH + 补查目录里找）、summarize（从 log 解析结果摘要，异常由调用方退化）
_AGENTS = {
    "claude": {"bin": "claude", "summarize": _claude_summary},
    "codex": {"bin": "codex", "summarize": _codex_summary},
}


def _summarize(agent: str, log: str) -> str:
    """适配表解析摘要；任何异常/空结果退化为 log 尾部。"""
    try:
        text = _AGENTS[agent]["summarize"](log)
    except Exception:
        text = ""
    return text or log[-_SUMMARY_TAIL:].strip()


def _wait(proc, task_id: str, agent: str, prompt: str, log_path: str, timeout_s: float, ctx) -> None:
    """daemon 线程：等 CLI 智能体收尾 → 落库 → 播报。实现在 _common._wait（沙箱脚本复用同一套）。"""
    _common._wait(proc, task_id, f"{agent} 任务", prompt, log_path, timeout_s, ctx,
                  summarize=lambda log: _summarize(agent, log))


# precheck 启发式：短描述 + 命中这些关键词 → 像步骤明确的一次性任务，应走 code_exec
_ONE_SHOT_KEYWORDS = ("统计", "转换", "整理", "计算", "格式化", "生成文件")


class DispatchTaskTool(Tool):
    id = "agents.dispatch_task"
    label = "派任务给智能体"
    description = (
        "把任务派给本机 CLI 智能体（Claude Code/Codex）后台执行：立即返回不等结果，"
        "完成后译宝主动播报。【仅用于】需要智能体自主多轮决策的开放任务——"
        "修 bug（要读代码、改、跑测试迭代）、写功能、重构、代码库调研。"
        "【不要用于】步骤明确的一次性任务（统计/转换/整理/小计算/生成文件）——"
        "那些用 agents.code_exec 自己写脚本沙箱跑，秒级且免费。"
    )
    default_risk = RiskLevel.L3_HIGH

    def precheck(self, params: dict) -> str | None:
        """启发式拦截：短描述 + 一次性任务关键词 → 建议改走 code_exec（秒级免费）。"""
        prompt = str(params.get("prompt") or "")
        if len(prompt) < 120 and any(k in prompt for k in _ONE_SHOT_KEYWORDS):
            return ("此任务像步骤明确的一次性任务，应使用 agents.code_exec"
                    "（自己写脚本沙箱执行，秒级免费）；确需自主多轮决策请在描述中说明探索/迭代原因")
        return None

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string", "description": "目标智能体：claude 或 codex"},
                        "prompt": {"type": "string",
                                   "description": "任务描述，要具体完整——智能体看不到你们的对话上下文"},
                        "cwd": {"type": "string", "description": "工作目录（智能体在此目录下干活）"},
                        "timeout_min": {"type": "integer", "description": "超时分钟数，默认 30"},
                    },
                    "required": ["agent", "prompt", "cwd"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        agent_name = str(params.get("agent") or "").strip()
        spec = _AGENTS.get(agent_name)
        if spec is None:
            return ActionResult(
                success=False,
                error=f"未知智能体：{agent_name or '(空)'}（可用：{', '.join(_AGENTS)}）",
            )
        prompt = str(params.get("prompt") or "").strip()
        if not prompt:
            return ActionResult(success=False, error="任务描述（prompt）不能为空")
        bin_path = _find_cli(spec["bin"])
        if bin_path is None:
            return ActionResult(
                success=False,
                error=f"{spec['bin']} 未安装或未登录（PATH 和常见安装目录里都找不到 {spec['bin']}）",
            )
        cwd = str(params.get("cwd") or "").strip()
        cwd_real = os.path.realpath(cwd) if cwd else ""
        if not cwd_real or not os.path.isdir(cwd_real):
            return ActionResult(success=False, error=f"工作目录不存在：{cwd or '(空)'}")
        try:
            timeout_min = int(params.get("timeout_min") or 30)
        except (TypeError, ValueError):
            return ActionResult(success=False, error="timeout_min 必须是整数（分钟）")
        if timeout_min <= 0:
            return ActionResult(success=False, error="timeout_min 必须大于 0")

        task_id = uuid.uuid4().hex[:12]
        logs_dir = os.path.join(config.plugin_data_dir("agents"), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        log_path = os.path.join(logs_dir, f"{task_id}.log")
        argv, err = _build_argv(agent_name, bin_path, prompt, cwd_real, logs_dir, task_id)
        if argv is None:
            return ActionResult(success=False, error=err)
        try:
            log_file = open(log_path, "w", encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"创建日志文件失败：{e}")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd_real, stdin=subprocess.DEVNULL,
                stdout=log_file, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as e:
            log_file.close()
            return ActionResult(success=False, error=f"启动 {agent_name} 失败：{e}")
        log_file.close()  # 子进程已继承写 fd，父进程这份关掉
        _PROCS[task_id] = proc
        ctx.db.insert("tasks", {
            "id": task_id, "agent": agent_name, "prompt": prompt, "cwd": cwd_real,
            "status": "running", "exit_code": -1, "pid": proc.pid, "log_path": log_path,
            "created_at": int(time.time()), "finished_at": 0,
        })
        threading.Thread(
            target=_wait,
            args=(proc, task_id, agent_name, prompt, log_path, timeout_min * 60, ctx),
            daemon=True,
        ).start()
        return ActionResult(success=True, data={
            "task_id": task_id,
            "human": f"已派给 {agent_name}，任务 {task_id}，完成后我会主动汇报",
        })


class TaskStatusTool(Tool):
    id = "agents.task_status"
    label = "查任务状态"
    description = "查看一个智能体任务的状态与日志尾部"
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "任务 id（dispatch_task 返回的 task_id）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        task_id = str(params.get("id") or "").strip()
        if not task_id:
            return ActionResult(success=False, error="缺少任务 id")
        rows = ctx.db.query("tasks", where={"id": task_id})
        if not rows:
            return ActionResult(success=False, error=f"任务不存在：{task_id}")
        row = rows[0]
        log_tail = _tail_text(row.get("log_path") or "", _STATUS_TAIL)
        return ActionResult(success=True, data={
            "row": row,
            "log_tail": log_tail,
            "human": f"{row['agent']} 任务 {task_id}：{row['status']}",
        })


class TaskStopTool(Tool):
    id = "agents.task_stop"
    label = "停止任务"
    description = "停止一个还在运行的智能体任务（终止其子进程）"
    default_risk = RiskLevel.L2_MEDIUM

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "任务 id（dispatch_task 返回的 task_id）"},
                    },
                    "required": ["id"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        task_id = str(params.get("id") or "").strip()
        if not task_id:
            return ActionResult(success=False, error="缺少任务 id")
        rows = ctx.db.query("tasks", where={"id": task_id})
        if not rows:
            return ActionResult(success=False, error=f"任务不存在：{task_id}")
        if rows[0]["status"] != "running":
            return ActionResult(success=False, error=f"任务已结束（{rows[0]['status']}），无需停止")
        proc = _PROCS.get(task_id)
        try:
            pid = int(rows[0].get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if proc is None and not _common._pid_alive(pid):
            return ActionResult(success=False, error="进程句柄已丢失（可能重启过）且进程已不在，无法停止")
        # 先落 stopped 再杀：收尾线程（_wait / _reap_detached）被唤醒后读到 stopped 会保留它；
        # 反过来先杀，收尾线程可能在本 update 之前把状态翻成 failed/done（竞态）
        ctx.db.update("tasks", task_id, {"status": "stopped", "finished_at": int(time.time())})
        if proc is not None:
            proc.terminate()
        else:
            os.kill(pid, signal.SIGTERM)  # 句柄丢失（重启过）但 pid 还活着：按 pid 兜底
        return ActionResult(success=True, data={
            "id": task_id,
            "human": f"已停止任务 {task_id}",
        })


def _reconcile_orphans(ctx: Any) -> None:
    """底座重启后对账（make_tools 时跑）：running 行 pid 还活着 → 挂 _reap_detached 探活收尾；
    已死 → 落 interrupted（重启把 _wait 线程带走了，没人替它收尾）。
    """
    try:
        rows = ctx.db.query("tasks", where={"status": "running"})
    except Exception as e:
        print(f"[agents] 任务对账查询失败：{e}", file=sys.stderr)
        return
    for row in rows:
        task_id = str(row.get("id") or "")
        if not task_id:
            continue
        try:
            pid = int(row.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if _common._pid_alive(pid):
            kind = str(row.get("kind") or "agent")
            agent = str(row.get("agent") or "")
            label = "沙箱脚本" if kind == "script" else f"{agent} 任务"
            summarize = None if kind == "script" else (lambda log, a=agent: _summarize(a, log))
            threading.Thread(
                target=_common._reap_detached,
                args=(pid, task_id, label, str(row.get("prompt") or ""),
                      str(row.get("log_path") or ""), ctx),
                kwargs={"summarize": summarize},
                daemon=True,
            ).start()
            print(f"[agents] 对账：任务 {task_id}（pid {pid}）仍在运行，挂探活收尾线程", file=sys.stderr)
            continue
        try:
            ctx.db.update("tasks", task_id, {"status": "interrupted", "finished_at": int(time.time())})
        except Exception as e:
            print(f"[agents] 任务 {task_id} 对账落库失败：{e}", file=sys.stderr)
        print(f"[agents] 对账：任务 {task_id} 进程已不在，落 interrupted", file=sys.stderr)


def make_tools(ctx: Any) -> list[Tool]:
    _reconcile_orphans(ctx)
    return [DispatchTaskTool(), TaskStatusTool(), TaskStopTool()]
