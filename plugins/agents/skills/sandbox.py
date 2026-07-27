"""agents.code_exec：LLM 生成脚本在 macOS Seatbelt 沙箱里执行（读全放 / 写限根 / 默认断网）。

流程：校验 → 脚本 + policy.sb 落盘（可审计）→ sandbox-exec spawn → 登记 tasks 表
（kind="script"）→ 同步等一个短窗口（_SYNC_WAIT_SEC）：窗口内完成就把输出直接作为
工具结果返回（LLM 当轮给用户答案，不播报）；窗口装不下（长任务）才转 daemon 线程
_common._wait 后台收尾（与 dispatch_task 同一套：落库 + emit_event 播报）。
task_stop 经共享 _PROCS 两种路径都能停。

macOS 15.6 实测坑：deny-default + process-exec 组合下 file-read 若加 subpath 过滤，
dyld 在沙箱内加载被拒直接 SIGABRT——所以 file-read 必须全放，不能加过滤器。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from yibao_brain import config
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill


def _sibling(stem: str):
    """按路径加载同目录兄弟模块并缓存进 sys.modules：全插件共享同一实例（_PROCS 唯一）。"""
    name = f"yibao_plugin_agents_{stem}"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{stem}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # 先挂再 exec：重复触发加载也拿到同一实例
        spec.loader.exec_module(mod)
    return mod


_common = _sibling("_common")
_PROCS = _common._PROCS

_LANGS = {"python": "script.py", "node": "script.js"}
_TIMEOUT_DEFAULT = 120
_TIMEOUT_MAX = 600
_SYNC_WAIT_SEC = 25  # 同步等待窗口：短任务当轮出答案，不让 LLM 轮询 task_status 烧步数
_SYNC_LOG_READ = 64 * 1024  # 同步路径读 log 的上限
_SYNC_OUTPUT_TAIL = 6000  # 喂给 LLM 的输出尾部上限


def _build_profile(write_roots: list[str], allow_network: bool) -> str:
    """生成 Seatbelt profile：读全放（加过滤会 SIGABRT，见模块 docstring）、写限根、默认断网。

    写根一律 realpath 规范化（subpath 按 realpath 匹配，/tmp 是 /private/tmp 的 symlink，
    两者都固定写进 allow 行）；每个写根追加一条该根 /.git 的 deny（脚本改不动仓库历史）。
    """
    roots = [os.path.realpath(r) for r in write_roots]
    subs = " ".join(f'(subpath "{r}")' for r in roots)
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-exec process-fork signal)",
        "(allow file-read*)",
        "(allow mach-lookup)",
        "(allow sysctl-read)",
        "(allow ipc-posix-sem)",
        '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/tty"))',
        f'(allow file-write* {subs} (subpath "/private/tmp") (subpath "/tmp"))',
    ]
    for r in roots:
        lines.append(f'(deny file-write* (subpath "{r}/.git"))')
    if allow_network:
        lines.append("(allow network-outbound)")
    return "\n".join(lines) + "\n"


def _find_interpreter(lang: str) -> str | None:
    """python 优先 /usr/bin/python3（macOS 系统 3.9.6，沙箱兼容实测 ok）再 PATH；node 走 PATH。"""
    if lang == "python":
        if os.path.exists("/usr/bin/python3"):
            return "/usr/bin/python3"
        return shutil.which("python3")
    return shutil.which("node")


class CodeExecSkill(Skill):
    id = "agents.code_exec"
    label = "运行沙箱脚本"
    description = (
        "你（译宝）自己编写一段 python/node 脚本，并在 macOS 沙箱（Seatbelt）里运行它："
        "默认断网、只能写工作目录（沙箱强制，.git 写保护）。"
        f"{_SYNC_WAIT_SEC}s 内跑完的任务输出直接作为工具结果返回给你，当轮给用户答案；"
        "更长的任务转后台，跑完主动播报。"
        "用户说「写个脚本做 X / 用脚本统计 X / 帮我处理这批文件」就是在叫你走我——"
        "步骤明确的本地加工一律你自己写脚本走我，不要派 agents.dispatch_task"
        "（那是给需要自主多轮决策的开放任务用的，又慢又烧额度）。"
    )
    default_risk = RiskLevel.L3_HIGH

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lang": {"type": "string", "description": "脚本语言：python 或 node"},
                        "code": {"type": "string",
                                 "description": "自包含脚本：结果 print 到 stdout；不要交互式输入；"
                                                "只能写 cwd 目录内的文件（沙箱强制）；"
                                                "不要试图联网，除非 network=true"},
                        "cwd": {"type": "string",
                                "description": "工作目录：脚本在这里跑，也是唯一可写目录"},
                        "timeout_sec": {"type": "integer",
                                        "description": f"超时秒数，默认 {_TIMEOUT_DEFAULT}，上限 {_TIMEOUT_MAX}"},
                        "network": {"type": "boolean",
                                    "description": "确需联网才 true，审批时会向用户展示"},
                    },
                    "required": ["lang", "code", "cwd"],
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        lang = str(params.get("lang") or "").strip().lower()
        script_name = _LANGS.get(lang)
        if script_name is None:
            return ActionResult(success=False, error=f"不支持的语言：{lang or '(空)'}（可用：python、node）")
        code = str(params.get("code") or "")
        if not code.strip():
            return ActionResult(success=False, error="脚本内容（code）不能为空")
        cwd = str(params.get("cwd") or "").strip()
        cwd_real = os.path.realpath(cwd) if cwd else ""
        if not cwd_real or not os.path.isdir(cwd_real):
            return ActionResult(success=False, error=f"工作目录不存在：{cwd or '(空)'}")
        sandbox_exec = shutil.which("sandbox-exec")
        if sandbox_exec is None:
            return ActionResult(success=False, error="当前环境不支持沙箱执行（找不到 sandbox-exec，仅 macOS 可用）")
        interp = _find_interpreter(lang)
        if interp is None:
            name = "python3" if lang == "python" else "node"
            return ActionResult(success=False, error=f"找不到 {name} 解释器（请先安装）")
        try:
            timeout_sec = int(params.get("timeout_sec") or _TIMEOUT_DEFAULT)
        except (TypeError, ValueError):
            return ActionResult(success=False, error="timeout_sec 必须是整数（秒）")
        if timeout_sec <= 0:
            return ActionResult(success=False, error="timeout_sec 必须大于 0")
        timeout_sec = min(timeout_sec, _TIMEOUT_MAX)
        network = bool(params.get("network"))

        task_id = uuid.uuid4().hex[:12]
        task_dir = os.path.realpath(os.path.join(config.plugin_data_dir("agents"), "sandbox", task_id))
        os.makedirs(task_dir, exist_ok=True)
        script_path = os.path.join(task_dir, script_name)
        policy_path = os.path.join(task_dir, "policy.sb")
        log_path = os.path.join(task_dir, "output.log")
        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            with open(policy_path, "w", encoding="utf-8") as f:  # policy 落盘可审计
                f.write(_build_profile([cwd_real, task_dir], network))
            log_file = open(log_path, "w", encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"准备沙箱任务目录失败：{e}")
        try:
            proc = subprocess.Popen(
                [sandbox_exec, "-f", policy_path, interp, script_path],
                cwd=cwd_real, stdout=log_file, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as e:
            log_file.close()
            return ActionResult(success=False, error=f"启动沙箱失败：{e}")
        log_file.close()  # 子进程已继承写 fd，父进程这份关掉
        _PROCS[task_id] = proc  # 先登记再等：同步窗口内 task_stop 也能停
        ctx.db.insert("tasks", {
            "id": task_id, "kind": "script", "agent": lang, "prompt": code[:200], "cwd": cwd_real,
            "status": "running", "exit_code": -1, "log_path": log_path,
            "created_at": int(time.time()), "finished_at": 0,
        })

        wait_window = min(_SYNC_WAIT_SEC, timeout_sec)
        timed_out = False
        try:
            proc.wait(timeout=wait_window)
        except subprocess.TimeoutExpired:
            timed_out = True

        if timed_out and timeout_sec > wait_window:
            # 长任务：转后台 daemon 收尾（落库 + 播报），立即返回
            threading.Thread(
                target=_common._wait,
                args=(proc, task_id, "沙箱脚本", code, log_path, timeout_sec - wait_window, ctx),
                daemon=True,
            ).start()
            return ActionResult(success=True, data={
                "task_id": task_id,
                "human": f"已在沙箱中运行（{lang}，{'允许联网' if network else '断网'}），"
                         f"任务 {task_id}，完成后我会主动汇报",
            })

        # 同步收尾：窗口内完成 / 超时预算耗尽（自己杀）/ 被 task_stop 停掉
        if timed_out:
            proc.kill()
            proc.wait()  # 等 kill 生效（收尸防僵尸）
        exit_code = proc.returncode if proc.returncode is not None else -1
        prev = ctx.db.query("tasks", where={"id": task_id})
        stopped = bool(prev and prev[0]["status"] == "stopped")
        if timed_out:
            status = "failed"
        elif stopped:
            status = "stopped"
        else:
            status = "done" if exit_code == 0 else "failed"
        try:
            ctx.db.update("tasks", task_id, {
                "status": status, "exit_code": exit_code, "finished_at": int(time.time()),
            })
        except Exception as e:
            print(f"[agents] 任务 {task_id} 落库失败：{e}", file=sys.stderr)
        _PROCS.pop(task_id, None)
        output = _common._tail_text(log_path, _SYNC_LOG_READ)[-_SYNC_OUTPUT_TAIL:].strip()
        if timed_out:
            return ActionResult(success=False,
                                error=f"沙箱脚本超时被终止（{timeout_sec}s，任务 {task_id}）。"
                                      f"输出尾部：\n{output or '(无输出)'}")
        if stopped:
            return ActionResult(success=False, error=f"沙箱脚本已被用户停止（任务 {task_id}）")
        if status == "done":
            return ActionResult(success=True, data={
                "task_id": task_id,
                "human": f"✅ 沙箱脚本完成（任务 {task_id}）",
                "output": output or "(无输出)",
            })
        return ActionResult(success=False,
                            error=f"沙箱脚本失败（退出码 {exit_code}，任务 {task_id}）。"
                                  f"输出尾部：\n{output or '(无输出)'}")


def make_tools(ctx: Any) -> list[Skill]:
    return [CodeExecSkill()]
