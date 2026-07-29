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

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "dummy")  # key 前置检查（不真发请求）
    seen = {}

    class _FakeMem0:
        def add(self, messages, user_id=None, **kw):
            seen["add_user"] = user_id

        def search(self, query, filters=None, **kw):
            seen["search_filters"] = filters
            return [{"memory": "命中"}]

    def _from_config(cfg):
        return _FakeMem0()

    monkeypatch.setattr(mem0.Memory, "from_config", _from_config)

    from yibao_brain.memory import Mem0Memory

    m = Mem0Memory()
    m.add("hi", user_id="u1")
    assert seen["add_user"] == "u1"
    assert m.recall("q", user_id="u1") == ["命中"]
    assert seen["search_filters"] == {"user_id": "u1"}  # 关键：filters 而非 top-level user_id


def test_mem0_config_wiring(monkeypatch):
    # from_config 收到的配置：llm 复用主 provider + fastembed 本地 + 中文事实抽取指令
    import mem0

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "dummy")
    monkeypatch.setenv("YIBAO_MEM0_VECTOR_PATH", "/tmp/yibao-test-cfg-store")
    seen = {}

    class _FakeMem0:
        pass

    def _from_config(cfg):
        seen["cfg"] = cfg
        return _FakeMem0()

    monkeypatch.setattr(mem0.Memory, "from_config", _from_config)

    from yibao_brain.memory import Mem0Memory

    Mem0Memory()
    cfg = seen["cfg"]
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["config"]["api_key"] == "dummy"  # 复用主 LLM key，不是 OPENAI_API_KEY
    assert cfg["embedder"]["provider"] == "fastembed"
    assert cfg["vector_store"]["config"]["path"] == "/tmp/yibao-test-cfg-store"
    assert "中文" in cfg["custom_instructions"]  # 事实保留用户原话语言


def test_mem0_missing_key_raises_human_error(monkeypatch):
    # key 缺失：拦在 mem0 原生「Missing credentials…OPENAI_API_KEY」前，给人话
    monkeypatch.delenv("YIBAO_LLM_API_KEY", raising=False)
    monkeypatch.delenv("YIBAO_GLM_API_KEY", raising=False)

    from yibao_brain.memory import Mem0Memory

    try:
        Mem0Memory()
        raise AssertionError("应抛错")
    except RuntimeError as e:
        assert "未配置模型 key" in str(e) and "OPENAI_API_KEY" not in str(e)


def test_mem0_recall_tolerates_search_error(monkeypatch):
    # search 抛异常时优雅返回空，不阻断回路
    import mem0

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "dummy")

    class _BoomMem0:
        def add(self, messages, user_id=None, **kw):
            pass

        def search(self, query, filters=None, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _BoomMem0())

    from yibao_brain.memory import Mem0Memory

    m = Mem0Memory()
    assert m.recall("q", user_id="u1") == []


# ---------- update：编辑单条记忆（OS 感 §4.4 可改）----------


def test_fake_update_replaces_text_keeps_id():
    m = FakeMemory()
    m.add("用户喜欢美式咖啡", user_id="u1")
    mid = m.list_all("u1")[0]["id"]
    assert m.update(mid, "用户喜欢拿铁") is True
    items = m.list_all("u1")
    assert [it["text"] for it in items] == ["用户喜欢拿铁"]
    assert items[0]["id"] == mid  # id 不变


def test_fake_update_missing_id_returns_false():
    m = FakeMemory()
    assert m.update("u1:9", "x") is False


def test_mem0_update_delegates_by_memory_id(monkeypatch):
    import mem0

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "dummy")
    seen = {}

    class _FakeMem0:
        def update(self, memory_id, text=None, **kw):
            seen["memory_id"] = memory_id
            seen["text"] = text

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _FakeMem0())

    from yibao_brain.memory import Mem0Memory

    m = Mem0Memory()
    assert m.update("abc-123", "改后文本") is True
    assert seen == {"memory_id": "abc-123", "text": "改后文本"}


def test_mem0_update_error_returns_false(monkeypatch):
    import mem0

    monkeypatch.setenv("YIBAO_LLM_API_KEY", "dummy")

    class _BoomMem0:
        def update(self, memory_id, text=None, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(mem0.Memory, "from_config", lambda cfg: _BoomMem0())

    from yibao_brain.memory import Mem0Memory

    assert Mem0Memory().update("x", "y") is False


def test_humanize_err():
    from yibao_brain.memory import _humanize_err

    assert "另一个译宝实例" in _humanize_err(RuntimeError(
        "Storage folder /x is already accessed by another instance of Qdrant client."))
    assert "未配置模型 key" in _humanize_err(RuntimeError(
        "Missing credentials. Please set the OPENAI_API_KEY environment variable."))
    assert _humanize_err(RuntimeError("别的错")) == "别的错"  # 未识别原文透传


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

    m = LazyMem0Memory(factory=factory, init_attempts=1)  # 测降级路径，不等重试
    assert _wait(lambda: m.failed)
    assert not m.ready
    assert m.recall("x", "u") == []
    m.add("y", "u")  # 降级后静默丢弃，不抛异常


def test_lazy_memory_retries_before_failure():
    from yibao_brain.memory import LazyMem0Memory

    calls = []

    def factory():
        calls.append(1)
        raise RuntimeError("lock held")

    m = LazyMem0Memory(factory=factory, init_attempts=3, init_delay_s=0.05)
    assert _wait(lambda: m.failed)
    assert len(calls) == 3  # 按次数重试后才降级


def test_lazy_memory_retry_recovers():
    from yibao_brain.memory import LazyMem0Memory

    calls = []
    real = FakeMemory()

    def factory():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("lock held")  # 前两次模拟旧实例锁未释
        return real

    m = LazyMem0Memory(factory=factory, init_attempts=3, init_delay_s=0.05)
    assert _wait(lambda: m.ready)
    assert not m.failed
    m.add("恢复后的记忆", "u")
    assert "恢复后的记忆" in real.recall("恢复", "u")


def test_lazy_memory_failure_notifies_callback():
    import threading

    from yibao_brain.memory import LazyMem0Memory

    gate = threading.Event()
    seen: list[str] = []

    def factory():
        gate.wait(5)
        raise RuntimeError("no torch")

    m = LazyMem0Memory(factory=factory, init_attempts=1)
    m.set_status_callback(seen.append)  # 先注入：失败时回调
    gate.set()
    assert _wait(lambda: bool(seen))
    assert "no torch" in seen[0]


def test_lazy_memory_callback_set_after_failure_fires_immediately():
    from yibao_brain.memory import LazyMem0Memory

    def factory():
        raise RuntimeError("no torch")

    m = LazyMem0Memory(factory=factory, init_attempts=1)
    assert _wait(lambda: m.failed)
    seen: list[str] = []
    m.set_status_callback(seen.append)  # 失败后才注入：立即补发，不错过
    assert seen and "no torch" in seen[0]


def test_lazy_update_not_ready_raises_human_error():
    import threading

    from yibao_brain.memory import LazyMem0Memory

    gate = threading.Event()
    m = LazyMem0Memory(factory=lambda: gate.wait(5) or FakeMemory())
    try:
        m.update("u:0", "x")
        raise AssertionError("应抛错")
    except RuntimeError as e:
        assert "尚未就绪" in str(e)


def test_lazy_update_delegates_when_ready():
    from yibao_brain.memory import LazyMem0Memory

    real = FakeMemory()
    real.add("旧文本", "u")
    m = LazyMem0Memory(factory=lambda: real)
    assert _wait(lambda: m.ready)
    mid = real.list_all("u")[0]["id"]
    assert m.update(mid, "新文本") is True
    assert real.list_all("u")[0]["text"] == "新文本"


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


# ---------- add 返回是否新增事实（Task 3：Feed 主屏化铺路）----------


def test_fake_memory_add_returns_true(tmp_path):
    from yibao_brain.memory import FakeMemory
    m = FakeMemory()
    assert m.add("x", "u") is True


def test_mem0_add_returns_whether_added(monkeypatch):
    from yibao_brain.memory import Mem0Memory
    m = Mem0Memory.__new__(Mem0Memory)
    class _FakeM:
        def add(self, messages, user_id): return {"results": [{"event": "ADD"}]}
    m._m = _FakeM()
    assert m.add("x", "u") is True
    class _FakeM2:
        def add(self, messages, user_id): return {"results": []}
    m._m = _FakeM2()
    assert m.add("x", "u") is False


def test_mem0_add_noop_event_returns_false(monkeypatch):
    # NOOP/UPDATE（去重/更新）不算新增事实
    from yibao_brain.memory import Mem0Memory
    m = Mem0Memory.__new__(Mem0Memory)
    class _FakeM:
        def add(self, messages, user_id): return {"results": [{"event": "NOOP"}]}
    m._m = _FakeM()
    assert m.add("x", "u") is False


def test_mem0_add_malformed_result_returns_false(monkeypatch):
    # 返回结构异常/抛异常时安全降级为 False，不阻断回路
    from yibao_brain.memory import Mem0Memory
    m = Mem0Memory.__new__(Mem0Memory)
    class _BoomM:
        def add(self, messages, user_id): raise RuntimeError("boom")
    m._m = _BoomM()
    assert m.add("x", "u") is False


def test_lazy_add_returns_false_when_buffered():
    # 未就绪进缓冲：返回 False（非新增）
    import threading
    from yibao_brain.memory import LazyMem0Memory

    gate = threading.Event()
    m = LazyMem0Memory(factory=lambda: gate.wait(5) or FakeMemory())
    try:
        assert m.add("x", "u") is False
        assert not m.ready
    finally:
        gate.set()


def test_lazy_add_returns_false_when_degraded():
    from yibao_brain.memory import LazyMem0Memory

    def factory():
        raise RuntimeError("no torch")

    m = LazyMem0Memory(factory=factory, init_attempts=1)
    assert _wait(lambda: m.failed)
    assert m.add("y", "u") is False


def test_lazy_add_propagates_bool_when_ready():
    # 就绪后透传 real.add 的 bool（True）
    from yibao_brain.memory import LazyMem0Memory

    real = FakeMemory()
    m = LazyMem0Memory(factory=lambda: real)
    assert _wait(lambda: m.ready)
    assert m.add("新事实", "u") is True
    assert "新事实" in real.recall("新", "u")
