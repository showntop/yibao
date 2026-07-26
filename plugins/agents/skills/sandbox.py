"""agents.code_exec：LLM 生成脚本在 macOS Seatbelt 沙箱里执行（读全放 / 写限根 / 默认断网）。

流程：校验 → 脚本 + policy.sb 落盘（可审计）→ sandbox-exec spawn → 登记 tasks 表
（kind="script"）立即返回；daemon 线程收尾复用 _common._wait（与 dispatch_task 同一套：
落库 + emit_event 播报），task_stop 经共享 _PROCS 自然可停。

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
    description = (
        "在 macOS 沙箱（Seatbelt）里运行一段自包含脚本（python/node）：默认断网、"
        "只能写工作目录（沙箱强制，.git 写保护），立即返回不等结果，跑完主动播报输出。"
        "适合一次性的数据处理/文件整理/小计算。"
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
        _PROCS[task_id] = proc
        ctx.db.insert("tasks", {
            "id": task_id, "kind": "script", "agent": lang, "prompt": code[:200], "cwd": cwd_real,
            "status": "running", "exit_code": -1, "log_path": log_path,
            "created_at": int(time.time()), "finished_at": 0,
        })
        threading.Thread(
            target=_common._wait,
            args=(proc, task_id, "沙箱脚本", code, log_path, timeout_sec, ctx),
            daemon=True,
        ).start()
        return ActionResult(success=True, data={
            "task_id": task_id,
            "human": f"已在沙箱中运行（{lang}，{'允许联网' if network else '断网'}），"
                     f"任务 {task_id}，完成后我会主动汇报",
        })


def make_tools(ctx: Any) -> list[Skill]:
    return [CodeExecSkill()]
