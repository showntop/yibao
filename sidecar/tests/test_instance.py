"""单实例锁：flock 获取/互斥/持锁进程死后可再取。"""
from __future__ import annotations

import os
import subprocess

import pytest

import yibao_brain.instance as inst
from yibao_brain.instance import ensure_single_instance


def _fake_ps(cmdline: str):
    """伪造 subprocess.run：ps 返回指定 cmdline。"""

    def _run(args, **_kwargs):
        assert args[:3] == ["ps", "-p", "1234"]
        return subprocess.CompletedProcess(args, 0, stdout=f"{cmdline}\n", stderr="")

    return _run


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


def test_is_brain_process_module_form(monkeypatch):
    """python -m yibao_brain.server 形态判 True。"""
    monkeypatch.setattr(
        inst.subprocess, "run",
        _fake_ps("/Users/x/.venv/bin/python -m yibao_brain.server --port 9"),
    )
    assert inst._is_brain_process(1234) is True


def test_is_brain_process_entrypoint_form(monkeypatch):
    """uv run 入口点形态（连字符）也判 True——字面匹配漏认会致孤儿漏杀。

    uv run 拉起的大脑常驻进程是 venv python 子进程，ps 形如
    `.venv/bin/python .../.venv/bin/yibao-brain-server`。
    """
    monkeypatch.setattr(
        inst.subprocess, "run",
        _fake_ps("/x/sidecar/.venv/bin/python /x/sidecar/.venv/bin/yibao-brain-server"),
    )
    assert inst._is_brain_process(1234) is True


def test_is_brain_process_rejects_unrelated(monkeypatch):
    """无关进程（名字撞字符串但非 python/.venv）判 False。"""
    monkeypatch.setattr(
        inst.subprocess, "run",
        _fake_ps("/usr/local/bin/node yibao_brain.server.js"),
    )
    assert inst._is_brain_process(1234) is False
    monkeypatch.setattr(
        inst.subprocess, "run",
        _fake_ps("/usr/bin/python -m http.server"),
    )
    assert inst._is_brain_process(1234) is False
