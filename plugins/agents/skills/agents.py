"""agents：智能体调度——把任务派给本机 CLI 智能体（Claude Code / Codex）后台执行，完成主动播报。

dispatch_task（L3，走用户确认）：Popen spawn 子进程后登记 db 立即返回；
daemon 线程 _wait 等进程结束 → 落库 → 经 ctx.emit_event 播报（前端亮窗+气泡+TTS）。
task_status（L0）查行 + log 尾部；task_stop（L2）terminate 在跑进程。
新智能体接入：_AGENTS 加一项（bin + argv 构造 + 结果摘要解析）即可。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from typing import Any

from yibao_brain import config
from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_LOG_MAX_BYTES = 10 * 1024 * 1024  # log 超过 10MB 只读尾部
_SUMMARY_TAIL = 500  # 摘要解析失败/为空时退化为 log 尾部 500 字
_STATUS_TAIL = 2000  # task_status 返回的 log 尾部长度

# task_id → 在跑进程句柄（task_stop 用；底座重启后丢失，stop 会优雅报错）
_PROCS: dict[str, "subprocess.Popen"] = {}


def _tail_text(path: str, max_bytes: int) -> str:
    """读 log（≤max_bytes 全读，超出只读尾部）。文件不存在/读失败返回空串。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _claude_argv(bin_path: str, prompt: str) -> list[str]:
    return [bin_path, "-p", prompt, "--output-format", "json"]


def _claude_summary(log: str) -> str:
    """claude -p --output-format json：stdout 是事件数组，取最后一个 type=="result" 项的结果文本。"""
    events = json.loads(log)
    for ev in reversed(events):
        if isinstance(ev, dict) and ev.get("type") == "result":
            return str(ev.get("result") or "").strip()
    return ""


def _codex_argv(bin_path: str, prompt: str) -> list[str]:
    return [bin_path, "exec", prompt, "--json"]


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


# 适配表：bin（PATH 里找）、argv（构造命令行）、summarize（从 log 解析结果摘要，异常由调用方退化）
_AGENTS = {
    "claude": {"bin": "claude", "argv": _claude_argv, "summarize": _claude_summary},
    "codex": {"bin": "codex", "argv": _codex_argv, "summarize": _codex_summary},
}


def _summarize(agent: str, log: str) -> str:
    """适配表解析摘要；任何异常/空结果退化为 log 尾部。"""
    try:
        text = _AGENTS[agent]["summarize"](log)
    except Exception:
        text = ""
    return text or log[-_SUMMARY_TAIL:].strip()


def _wait(proc, task_id: str, agent: str, prompt: str, log_path: str, timeout_s: float, ctx) -> None:
    """daemon 线程：等进程收尾 → 落库 → emit_event 播报。任何异常只 print，不炸线程。"""
    try:
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            proc.wait()  # 等 kill 生效（收尸防僵尸）
        exit_code = proc.returncode if proc.returncode is not None else -1
        # 用户已主动停止（task_stop 先落 stopped）：保留 stopped，不被退出码翻成 failed
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
        summary = _summarize(agent, _tail_text(log_path, _LOG_MAX_BYTES))
        head = prompt[:50] + ("…" if len(prompt) > 50 else "")
        if timed_out:
            text = f"⏰ {agent} 任务超时已终止：{head}\n{summary}"
        elif stopped:
            text = f"⏹ {agent} 任务已停止：{head}"
        elif status == "done":
            text = f"✅ {agent} 任务完成：{head}\n{summary}"
        else:
            text = f"❌ {agent} 任务失败（退出码 {exit_code}）：{head}\n{summary}"
        emit = getattr(ctx, "emit_event", None)
        if emit is not None:  # 测试环境未注入时静默跳过
            emit({"kind": "reminder", "text": text})
    except Exception as e:  # 兜底：等待线程的任何意外都不许炸出来
        print(f"[agents] 任务 {task_id} 等待线程异常：{type(e).__name__}: {e}", file=sys.stderr)
    finally:
        _PROCS.pop(task_id, None)


class DispatchTaskSkill(Skill):
    id = "agents.dispatch_task"
    description = (
        "把任务派给本机 CLI 智能体（Claude Code/Codex）后台执行：立即返回不等结果，"
        "完成后译宝主动播报。适合耗时较长、上下文独立的干活任务"
        "（改代码/跑脚本/批量处理文件等）。"
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
        bin_path = shutil.which(spec["bin"])
        if bin_path is None:
            return ActionResult(
                success=False,
                error=f"{spec['bin']} 未安装或未登录（PATH 里找不到 {spec['bin']}）",
            )
        cwd = str(params.get("cwd") or "").strip()
        if not cwd or not os.path.isdir(cwd):
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
        try:
            log_file = open(log_path, "w", encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"创建日志文件失败：{e}")
        try:
            proc = subprocess.Popen(
                spec["argv"](bin_path, prompt),
                cwd=cwd, stdout=log_file, stderr=subprocess.STDOUT, text=True,
            )
        except OSError as e:
            log_file.close()
            return ActionResult(success=False, error=f"启动 {agent_name} 失败：{e}")
        log_file.close()  # 子进程已继承写 fd，父进程这份关掉
        _PROCS[task_id] = proc
        ctx.db.insert("tasks", {
            "id": task_id, "agent": agent_name, "prompt": prompt, "cwd": cwd,
            "status": "running", "exit_code": -1, "log_path": log_path,
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


class TaskStatusSkill(Skill):
    id = "agents.task_status"
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


class TaskStopSkill(Skill):
    id = "agents.task_stop"
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
        if proc is None:
            return ActionResult(success=False, error="进程句柄已丢失（可能重启过），无法停止")
        # 先落 stopped 再 terminate：_wait 被 terminate 唤醒后读到 stopped 会保留它；
        # 反过来先 terminate，_wait 可能在本 update 之前把状态翻成 failed（竞态）
        ctx.db.update("tasks", task_id, {"status": "stopped", "finished_at": int(time.time())})
        proc.terminate()
        return ActionResult(success=True, data={
            "id": task_id,
            "human": f"已停止任务 {task_id}",
        })


def make_tools(ctx: Any) -> list[Skill]:
    return [DispatchTaskSkill(), TaskStatusSkill(), TaskStopSkill()]
