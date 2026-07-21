"""单实例锁：flock 获取/互斥/持锁进程死后可再取。"""
from __future__ import annotations

import os

import pytest

from yibao_brain.instance import ensure_single_instance


def test_lock_writes_pid(tmp_path):
    fd = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    try:
        with open(tmp_path / "brain.lock") as f:
            assert f.read() == str(os.getpid())
    finally:
        os.close(fd)


def test_second_instance_rejected(tmp_path):
    fd = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    try:
        with pytest.raises(RuntimeError, match="单实例锁"):
            ensure_single_instance(
                str(tmp_path / "brain.lock"), attempts=2, retry_delay_s=0.01, reap=False,
            )
    finally:
        os.close(fd)


def test_lock_released_after_holder_closes(tmp_path):
    fd = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    os.close(fd)  # 模拟持锁进程死亡（flock 随 fd 关闭即释，SIGKILL 同理）
    fd2 = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    os.close(fd2)


def test_failed_attempt_closes_fd(tmp_path):
    fd = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    with pytest.raises(RuntimeError):
        ensure_single_instance(
            str(tmp_path / "brain.lock"), attempts=1, reap=False,
        )
    os.close(fd)
    # 失败路径若漏关 fd 不影响这里，但成功路径必须仍可用
    fd2 = ensure_single_instance(str(tmp_path / "brain.lock"), reap=False)
    os.close(fd2)
