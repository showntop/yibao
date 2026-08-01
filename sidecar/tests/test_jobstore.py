"""JobsStore：watch_command 任务持久化（FeedStore 模式）。"""


def test_add_finish_running_roundtrip(tmp_path):
    from yibao_brain.jobstore import JobsStore

    store = JobsStore(str(tmp_path / "jobs.db"))
    store.add({"task_id": "job_a", "command": "sleep 5", "cwd": "/tmp", "name": "测试",
               "timeout": 600.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 100.0, "finished_at": None})
    assert [j["task_id"] for j in store.running()] == ["job_a"]
    store.finish("job_a", status="completed", exit_code=0, output_tail="done")
    assert store.running() == []
    store.close()


def test_mark_interrupted(tmp_path):
    from yibao_brain.jobstore import JobsStore

    store = JobsStore(str(tmp_path / "jobs.db"))
    store.add({"task_id": "job_b", "command": "x", "cwd": "/tmp", "name": "n",
               "timeout": 1.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 1.0, "finished_at": None})
    store.mark_interrupted("job_b")
    assert store.running() == []
    store.close()


def test_store_failure_degrades_silently(tmp_path):
    """DB 路径不可写 → 只 print 不抛（Feed 增强面原则）。"""
    from yibao_brain.jobstore import JobsStore

    store = JobsStore("/nope/no/such/dir/jobs.db")  # 构造时建目录失败也不抛
    store.add({"task_id": "j", "command": "x", "cwd": "/tmp", "name": "n",
               "timeout": 1.0, "status": "running", "exit_code": None,
               "output_tail": "", "started_at": 1.0, "finished_at": None})
    assert store.running() == []
    store.close()
