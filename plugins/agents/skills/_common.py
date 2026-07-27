"""agents 插件内部共享：任务进程登记 + 等待收尾播报（agents.py 与 sandbox.py 共用）。

文件名以下划线开头 = 插件加载器跳过（不当 tool 模块加载）；兄弟模块经各自的 _sibling()
按路径加载并缓存进 sys.modules，保证全插件范围内单实例——_PROCS 唯一，task_stop 才能
停到任何一种任务（CLI 智能体 / 沙箱脚本）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

_LOG_MAX_BYTES = 10 * 1024 * 1024  # log 超过 10MB 只读尾部
_SUMMARY_TAIL = 500  # 无解析器时的摘要：log 尾部 500 字

# task_id → 在跑进程句柄（task_stop 用；底座重启后丢失，stop 会优雅报错）
_PROCS: dict[str, "subprocess.Popen"] = {}


def _pid_alive(pid: int) -> bool:
    """pid 是否存活（用于大脑重启后对账在跑任务；kill 0 不发信号只探活）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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


def _tail_summary(log: str) -> str:
    """默认摘要：log 尾部 500 字。"""
    return log[-_SUMMARY_TAIL:].strip()


def _wait(proc, task_id: str, label: str, prompt: str, log_path: str, timeout_s: float, ctx, summarize=None) -> None:
    """daemon 线程：等进程收尾 → 落库 → emit_event 播报。任何异常只 print，不炸线程。

    label：播报里的执行者（如 "claude 任务" / "沙箱脚本"）；
    summarize：log 全文 → 结果摘要的 callable，异常/空结果退化为 log 尾部 500 字。
    """
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
        log = _tail_text(log_path, _LOG_MAX_BYTES)
        try:
            summary = (summarize or _tail_summary)(log)
        except Exception:
            summary = ""
        summary = summary or _tail_summary(log)
        head = prompt[:50] + ("…" if len(prompt) > 50 else "")
        if timed_out:
            text = f"⏰ {label}超时已终止：{head}\n{summary}"
        elif stopped:
            text = f"⏹ {label}已停止：{head}"
        elif status == "done":
            text = f"✅ {label}完成：{head}\n{summary}"
        else:
            text = f"❌ {label}失败（退出码 {exit_code}）：{head}\n{summary}"
        emit = getattr(ctx, "emit_event", None)
        if emit is not None:  # 测试环境未注入时静默跳过
            emit({"kind": "reminder", "text": text})
    except Exception as e:  # 兜底：等待线程的任何意外都不许炸出来
        print(f"[agents] 任务 {task_id} 等待线程异常：{type(e).__name__}: {e}", file=sys.stderr)
    finally:
        _PROCS.pop(task_id, None)


_REAP_INTERVAL_S = 2.0


def _reap_detached(pid: int, task_id: str, label: str, prompt: str, log_path: str, ctx, summarize=None) -> None:
    """daemon 线程：对账用——大脑重启后没有 Popen 句柄，改为每 2s kill(pid,0) 探活；
    pid 死后读 log 收尾落库（status=done，exit_code 保持 -1 未知）并播报。任何异常只 print。
    """
    try:
        while _pid_alive(pid):
            time.sleep(_REAP_INTERVAL_S)
        # 用户已主动停止（task_stop 先落 stopped）：保留 stopped，不翻成 done
        prev = ctx.db.query("tasks", where={"id": task_id})
        stopped = bool(prev and prev[0]["status"] == "stopped")
        try:
            ctx.db.update("tasks", task_id, {
                "status": "stopped" if stopped else "done", "finished_at": int(time.time()),
            })
        except Exception as e:
            print(f"[agents] 任务 {task_id} 对账落库失败：{e}", file=sys.stderr)
        log = _tail_text(log_path, _LOG_MAX_BYTES)
        try:
            summary = (summarize or _tail_summary)(log)
        except Exception:
            summary = ""
        summary = summary or _tail_summary(log)
        head = prompt[:50] + ("…" if len(prompt) > 50 else "")
        if stopped:
            text = f"⏹ {label}已停止：{head}"
        else:
            text = f"✅ {label}完成：{head}\n{summary}"
        emit = getattr(ctx, "emit_event", None)
        if emit is not None:  # 测试环境未注入时静默跳过
            emit({"kind": "reminder", "text": text})
    except Exception as e:  # 兜底：对账线程的任何意外都不许炸出来
        print(f"[agents] 任务 {task_id} 对账线程异常：{type(e).__name__}: {e}", file=sys.stderr)
