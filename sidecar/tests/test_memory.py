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
