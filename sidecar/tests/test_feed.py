"""FeedStore 单测 + serve_async 的 feed 查询集成（OS 感 §4.2：主屏动态/问候统计）。"""
import asyncio
import time

from yibao_brain.feed import FeedStore
from yibao_brain.llm import FakeProvider
from yibao_brain.server import serve_async


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def _run_async(coro):
    return asyncio.run(coro)


# ---------- FeedStore 单测 ----------


def test_add_recent_desc_and_limit(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("task", "第一条")
    s.add("reminder", "第二条")
    s.add("event", "第三条")
    items = s.recent()
    assert [i["text"] for i in items] == ["第三条", "第二条", "第一条"]  # 时间倒序
    assert [i["kind"] for i in items] == ["event", "reminder", "task"]
    assert all(i["id"] and i["ts"] > 0 for i in items)
    limited = s.recent(limit=2)
    assert [i["text"] for i in limited] == ["第三条", "第二条"]
    s.close()


def test_recent_since_filters(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("event", "旧")
    time.sleep(0.01)
    since = time.time()
    time.sleep(0.01)
    s.add("event", "新")
    assert [i["text"] for i in s.recent(since=since)] == ["新"]
    s.close()


def test_count_since_by_kind(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("task", "t1")
    s.add("task", "t2")
    s.add("reminder", "r1")
    assert s.count_since("task", 0) == 2
    assert s.count_since("reminder", 0) == 1
    assert s.count_since("event", 0) == 0
    assert s.count_since("task", time.time() + 60) == 0  # 起点在未来 → 0
    s.close()


def test_unknown_kind_falls_back_to_event(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("ghost", "x")
    assert s.recent()[0]["kind"] == "event"
    s.close()


def test_meta_roundtrip_and_bad_json_degrades(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("task", "带 meta", {"id": "t1", "label": "写稿"})
    item = s.recent()[0]
    assert item["meta"] == {"id": "t1", "label": "写稿"}
    s.add("task", "无 meta")
    assert s.recent()[0]["meta"] == {}  # None → {}
    # meta 列被写坏 → 查询退化为 {} 而不是炸
    s._conn.execute("UPDATE feed SET meta = '{bad' WHERE id = ?", (item["id"],))
    s._conn.commit()
    assert [i["meta"] for i in s.recent()] == [{}, {}]
    s.close()


def test_add_failure_never_raises(tmp_path):
    s = FeedStore(str(tmp_path / "feed.db"))
    s.close()
    s.add("task", "库已关")  # 写失败只 print，不许抛（Feed 是增强面）
    s.add("task", "再试")  # 连续失败也不抛


def test_recent_includes_read_flag_and_mark_read(tmp_path):
    from yibao_brain.feed import FeedStore
    f = FeedStore(str(tmp_path / "f.db"))
    f.add("reminder", "提醒A", {"rid": "1"})
    rows = f.recent()
    assert rows[0]["read"] == 0
    assert f.count_unread() == 1
    assert f.mark_read(rows[0]["id"]) is True
    assert f.count_unread() == 0
    assert f.recent()[0]["read"] == 1


def test_mark_all_read(tmp_path):
    from yibao_brain.feed import FeedStore
    f = FeedStore(str(tmp_path / "f.db"))
    f.add("task", "t1", {}); f.add("task", "t2", {})
    assert f.count_unread() == 2
    n = f.mark_all_read()
    assert n == 2 and f.count_unread() == 0


def test_append_hourly_merges_same_hour(tmp_path):
    from yibao_brain.feed import FeedStore
    f = FeedStore(str(tmp_path / "f.db"))
    h = 1700000000 // 3600 * 3600
    f.append_hourly("event", "记住了：喜欢美式", {"type": "memory", "hour": h}, h)
    f.append_hourly("event", "记住了：住北京", {"type": "memory", "hour": h}, h)
    rows = f.recent()
    assert len(rows) == 1                       # 同小时合并
    assert "美式" in rows[0]["text"] and "北京" in rows[0]["text"]


def test_append_hourly_new_when_hour_differs(tmp_path):
    from yibao_brain.feed import FeedStore
    f = FeedStore(str(tmp_path / "f.db"))
    h1 = 1700000000 // 3600 * 3600
    h2 = h1 + 3600
    f.append_hourly("event", "A", {"type": "memory", "hour": h1}, h1)
    f.append_hourly("event", "B", {"type": "memory", "hour": h2}, h2)
    assert len(f.recent()) == 2


# ---------- serve_async 集成：{"type":"feed"} → items + stats ----------


def test_serve_async_feed_query(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))  # stats 不碰真实数据目录
    # 预置动态（serve_async 的 feed 库落在 dirname(db_path)/feed.db）
    s = FeedStore(str(tmp_path / "feed.db"))
    s.add("task", "任务完成", {"id": "t1"})
    s.add("reminder", "提醒触发")
    s.close()

    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed", "limit": 10}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    feeds = [m for m in out if m["type"] == "feed"]
    assert len(feeds) == 1
    assert [i["text"] for i in feeds[0]["items"]] == ["提醒触发", "任务完成"]
    stats = feeds[0]["stats"]
    assert set(stats) == {"pending_reminders", "running_tasks", "done_24h", "unread"}
    assert stats["running_tasks"] == 0  # tmp 数据目录下无 agents 库
    assert stats["done_24h"] == 1  # 一条 task 动态落在 24h 内
    assert stats["unread"] == 2  # 两条预置动态均未读


def test_serve_async_feed_query_empty(tmp_path, monkeypatch):
    """全新库：items 空、stats 四键齐全且全 0（主屏「今天安安静静」态）。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    out = []
    _run_async(
        serve_async(
            make_reader([{"type": "feed"}]),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    feeds = [m for m in out if m["type"] == "feed"]
    assert len(feeds) == 1
    assert feeds[0]["items"] == []
    assert feeds[0]["stats"] == {"pending_reminders": 0, "running_tasks": 0, "done_24h": 0, "unread": 0}
