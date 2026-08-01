"""FeedStore 单测 + serve_async 的 feed 查询集成（OS 感 §4.2：主屏动态/问候统计）。"""
import asyncio
import sqlite3
import time

from yibao_brain.feed import FeedStore
from yibao_brain.llm import FakeProvider
from yibao_brain.server import serve_async


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def _run_async(coro):
    return asyncio.run(coro)


def _seed_agent_tasks(root, rows):
    path = root / "plugins" / "agents" / "data.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, kind TEXT, agent TEXT, prompt TEXT, "
        "status TEXT, created_at INTEGER)"
    )
    conn.executemany(
        "INSERT INTO tasks (id, kind, agent, prompt, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


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


def test_serve_async_feed_includes_only_running_tasks_in_desc_order(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    _seed_agent_tasks(tmp_path, [
        ("old", "agent", "claude", "旧任务", "running", 10),
        ("done", "agent", "codex", "已完成", "done", 20),
        ("new", "script", "python", "新脚本", "running", 30),
    ])
    out = []
    _run_async(serve_async(
        make_reader([{"type": "feed"}]),
        out.append,
        use_real=False,
        db_path=str(tmp_path / "a.db"),
        provider=FakeProvider(),
    ))
    feed_msg = next(m for m in out if m["type"] == "feed")
    assert feed_msg["running_tasks"] == [
        {
            "id": "new", "kind": "script", "label": "沙箱脚本",
            "prompt": "新脚本", "status": "running", "created_at": 30,
        },
        {
            "id": "old", "kind": "agent", "label": "claude 任务",
            "prompt": "旧任务", "status": "running", "created_at": 10,
        },
    ]
    assert feed_msg["stats"]["running_tasks"] == 2


def test_serve_async_feed_running_task_query_failure_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    _seed_agent_tasks(tmp_path, [])

    def fail_query(*_args, **_kwargs):
        raise sqlite3.OperationalError("broken tasks")

    monkeypatch.setattr("yibao_brain.plugindb.PluginDb.query", fail_query)
    out = []
    _run_async(serve_async(
        make_reader([{"type": "feed"}]),
        out.append,
        use_real=False,
        db_path=str(tmp_path / "a.db"),
        provider=FakeProvider(),
    ))
    feed_msg = next(m for m in out if m["type"] == "feed")
    assert feed_msg["running_tasks"] == []
    assert feed_msg["stats"]["running_tasks"] == 0


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
    assert set(stats) == {"pending_reminders", "running_tasks", "done_24h", "unread", "ignored"}
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
    assert feeds[0]["stats"] == {"pending_reminders": 0, "running_tasks": 0, "done_24h": 0, "unread": 0, "ignored": 0}
    assert feeds[0]["running_tasks"] == []


def test_set_status_and_count_ignored(tmp_path):
    """C 子项目：处置态（follow/ignore/none）与 read 正交。"""
    from yibao_brain.feed import FeedStore
    f = FeedStore(str(tmp_path / "f.db"))
    f.add("task", "t1", {})
    f.add("task", "t2", {})
    rows = f.recent()
    assert all(r["status"] == "none" for r in rows)
    assert f.set_status(rows[0]["id"], "ignore") is True
    assert f.set_status(rows[1]["id"], "follow") is True
    assert f.count_ignored() == 1
    by_id = {r["id"]: r for r in f.recent()}
    assert by_id[rows[0]["id"]]["status"] == "ignore"
    assert by_id[rows[1]["id"]]["status"] == "follow"
    assert by_id[rows[0]["id"]]["read"] == 0            # ignore 与 read 正交
    assert f.set_status(rows[0]["id"], "bogus") is False     # 非法 status 拒收
    assert f.set_status(rows[0]["id"], "none") is True       # 取消标记
    assert f.count_ignored() == 0


def test_feed_status_migration_idempotent(tmp_path):
    """status 列幂等迁移：重复打开不崩。"""
    from yibao_brain.feed import FeedStore
    db = str(tmp_path / "f.db")
    FeedStore(db)
    FeedStore(db)               # 再开：ALTER 触发 duplicate column，应被吞
    f = FeedStore(db)
    f.add("task", "x", {})
    assert f.recent()[0]["status"] == "none"


# ---------- 信任统计读模型（v1.1 Slice 0）----------
def _insert(feed, ts, kind, text="x", read=0, status="none"):
    """直接 SQL 插入（可指定 ts/read/status，feed.add 只写当前时刻）。"""
    feed._conn.execute(
        "INSERT INTO feed (ts, kind, text, meta, read, status) VALUES (?,?,?,'{}',?,?)",
        (ts, kind, text, read, status),
    )
    feed._conn.commit()


def test_stats_empty_db(tmp_path):
    from yibao_brain.feed import FeedStore

    feed = FeedStore(str(tmp_path / "f.db"))
    s = feed.stats()
    assert s["total"] == 0
    assert s["by_kind"] == {"task": 0, "reminder": 0, "event": 0}
    assert s["by_day"] == []
    assert s["read_rate"] == 0.0 and s["ignored_rate"] == 0.0
    feed.close()


def test_stats_by_kind_by_day_and_rates(tmp_path):
    import time

    from yibao_brain.feed import FeedStore

    feed = FeedStore(str(tmp_path / "f.db"))
    now = time.time()
    _insert(feed, now, "task", read=1)
    _insert(feed, now, "task")                              # 未读
    _insert(feed, now - 86400, "reminder", status="ignore")  # 恰好前一天（必跨日历日）
    s = feed.stats(days=7)
    assert s["total"] == 3
    assert s["by_kind"] == {"task": 2, "reminder": 1, "event": 0}
    assert s["read_rate"] == round(1 / 3, 4)
    assert s["ignored_rate"] == round(1 / 3, 4)
    assert len({row["day"] for row in s["by_day"]}) == 2    # 跨两天分组
    feed.close()


def test_stats_days_window_excludes_old(tmp_path):
    import time

    from yibao_brain.feed import FeedStore

    feed = FeedStore(str(tmp_path / "f.db"))
    now = time.time()
    _insert(feed, now - 8 * 86400, "task")   # 窗口外
    _insert(feed, now, "event")
    s = feed.stats(days=7)
    assert s["total"] == 1
    assert s["by_kind"]["task"] == 0 and s["by_kind"]["event"] == 1
    feed.close()


def test_set_feedback_and_count_by_type(tmp_path):
    import time

    from yibao_brain.feed import FeedStore

    feed = FeedStore(str(tmp_path / "f.db"))
    feed.add("reminder", "坐久了", {"type": "health_nudge"})
    feed.add("reminder", "又坐久了", {"type": "health_nudge"})
    feed.add("event", "任务完成", {"type": "watch_command"})
    assert feed.set_feedback(1, "down") is True
    assert feed.set_feedback(2, "down") is True
    assert feed.set_feedback(3, "up") is True
    assert feed.set_feedback(999, "down") is False
    assert feed.set_feedback(1, "bad") is False
    now = time.time()
    assert feed.count_feedback_by_type("health_nudge", "down", now - 86400) == 2
    assert feed.count_feedback_by_type("watch_command", "down", now - 86400) == 0
    # recent() 能读出 meta.feedback
    items = feed.recent()
    by_id = {it["id"]: it for it in items}
    assert by_id[1]["meta"].get("feedback") == "down"
    assert by_id[3]["meta"].get("feedback") == "up"
    feed.close()
