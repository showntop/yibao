from yibao_brain.memory import FakeMemory


def test_fake_add_and_recall():
    m = FakeMemory()
    m.add("用户喜欢深色模式", user_id="u1")
    hits = m.recall("深色", user_id="u1")  # FakeMemory 是子串匹配，用真实子串
    assert "用户喜欢深色模式" in hits
    assert m.recall("x", user_id="other") == []  # 隔离不同用户
