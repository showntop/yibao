"""大脑单实例：flock + 孤儿自愈。

问题：壳被强杀/崩溃时，旧大脑可能存活成孤儿，独占 qdrant .lock，
新大脑 mem0 初始化失败 → 整个会话「记不住事」（此前只靠 ppid 看门狗
事后自杀，10s 轮询窗口内新大脑已初始化失败，且失败是永久的）。

方案：大脑启动时先回收其他存活的大脑进程（单实例语义下任何他者都是孤儿），
再对数据目录 brain.lock 取排他 flock（OS 级，进程死即释，不怕强杀），
并把 PID 写进锁文件便于排查。锁 fd 须存活期保持打开。
"""
from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import time

_BRAIN_PATTERN = "yibao_brain.server"


def _is_brain_process(pid: int) -> bool:
    """确认 pid 确实是 yibao 大脑（pgrep -f 可能误匹配到含同名字符串的无关进程）。"""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    cmd = r.stdout.strip()
    return _BRAIN_PATTERN in cmd and ("python" in cmd or ".venv" in cmd)


def _kill_pid(pid: int) -> None:
    """SIGTERM → 最多等 3s → SIGKILL → 最多等 2s。flock 随进程死亡即释。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(30):
        try:
            os.kill(pid, 0)
        except OSError:
            return  # 已退出
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def _reap_orphan_brains() -> None:
    """回收除自己外所有存活的大脑进程（单实例语义：他者即孤儿）。"""
    try:
        r = subprocess.run(
            ["pgrep", "-f", _BRAIN_PATTERN],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        print(f"[yibao] 孤儿大脑扫描失败（跳过）：{e}", file=sys.stderr)
        return
    me = os.getpid()
    for token in r.stdout.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid == me or not _is_brain_process(pid):
            continue
        print(f"[yibao] 回收存活的其他大脑进程 pid={pid}（单实例）", file=sys.stderr)
        _kill_pid(pid)


def ensure_single_instance(
    lock_path: str,
    *,
    attempts: int = 6,
    retry_delay_s: float = 0.8,
    reap: bool = True,
) -> int:
    """取大脑单实例锁；成功返回持有锁的 fd（须活到进程结束），失败抛 RuntimeError。

    reap=True 时先回收存活的其他大脑（等其退出最久 ~5s），再带重试取锁——
    覆盖「孤儿还没来得及死透新大脑已启动」的竞态。
    """
    if reap:
        _reap_orphan_brains()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        for attempt in range(attempts):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if attempt + 1 < attempts:
                    time.sleep(retry_delay_s)
        else:
            raise RuntimeError("另一个大脑实例仍持有单实例锁")
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except Exception:
        os.close(fd)
        raise
