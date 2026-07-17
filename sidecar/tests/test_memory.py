from yibao_brain.memory import FakeMemory


def test_fake_add_and_recall():
    m = FakeMemory()
    m.add("用户喜欢深色模式", user_id="u1")
    hits = m.recall("深色", user_id="u1")  # FakeMemory 是子串匹配，用真实子串
    assert "用户喜欢深色模式" in hits
    assert m.recall("x", user_id="other") == []  # 隔离不同用户


def test_mem0_recall_uses_filters_for_isolation(monkeypatch):
    # mem0 2.x：search 用 filters={"user_id":...}，不支持 top-level user_id（回归防护）
    import mem0

    seen = {}

    class _FakeMem0:
        def add(self, messages, user_id=None, **kw):
            seen["add_user"] = user_id

        def search(self, query, filters=None, **kw):
            seen["search_filters"] = filters
            return [{"memory": "命中"}]

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _FakeMem0())

    from yibao_brain.memory import Mem0Memory

    m = Mem0Memory()
    m.add("hi", user_id="u1")
    assert seen["add_user"] == "u1"
    assert m.recall("q", user_id="u1") == ["命中"]
    assert seen["search_filters"] == {"user_id": "u1"}  # 关键：filters 而非 top-level user_id


def test_mem0_recall_tolerates_search_error(monkeypatch):
    # search 抛异常时优雅返回空，不阻断回路
    import mem0

    class _BoomMem0:
        def add(self, messages, user_id=None, **kw):
            pass

        def search(self, query, filters=None, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _BoomMem0())

    from yibao_brain.memory import Mem0Memory

    m = Mem0Memory()
    assert m.recall("q", user_id="u1") == []


# ---------- LazyMem0Memory：后台懒加载 ----------


def _wait(pred, timeout=5.0):
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_lazy_memory_buffers_then_replays():
    import threading

    from yibao_brain.memory import LazyMem0Memory

    gate = threading.Event()
    real = FakeMemory()

    def factory():
        gate.wait(5)
        return real

    m = LazyMem0Memory(factory=factory)
    assert m.recall("深色", "u") == []  # 未就绪：空召回，不阻塞
    m.add("用户喜欢深色模式", "u")  # 未就绪：进缓冲
    assert not m.ready
    gate.set()
    assert _wait(lambda: m.ready)
    assert "用户喜欢深色模式" in real.recall("深色", "u")  # 缓冲已回放
    m.add("第二条记忆", "u")  # 就绪后直通真实实例
    assert "第二条记忆" in real.recall("第二", "u")


def test_lazy_memory_failure_degrades():
    from yibao_brain.memory import LazyMem0Memory

    def factory():
        raise RuntimeError("no torch")

    m = LazyMem0Memory(factory=factory)
    assert _wait(lambda: m.failed)
    assert not m.ready
    assert m.recall("x", "u") == []
    m.add("y", "u")  # 降级后静默丢弃，不抛异常


def test_lazy_memory_buffer_cap():
    import threading

    from yibao_brain.memory import LazyMem0Memory

    gate = threading.Event()
    real = FakeMemory()

    def factory():
        gate.wait(5)
        return real

    m = LazyMem0Memory(factory=factory, buffer_max=2)
    for i in range(5):
        m.add(f"m{i}", "u")
    gate.set()
    assert _wait(lambda: m.ready)
    assert real.recall("m", "u") == ["m0", "m1"]  # 只回放前 buffer_max 条
