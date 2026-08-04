"""Distiller：离线深加工层（感知 v3）。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from yibao_brain.distiller import DistillerStore, auto_run_due  # noqa: E402

from cryptography.fernet import Fernet  # noqa: E402

from yibao_brain.distiller import gather_summary, yesterday_window  # noqa: E402
from yibao_brain.perception import PerceptionStore  # noqa: E402


def _store(tmp_path):
    return DistillerStore(str(tmp_path / "distill.db"))


def test_store_add_and_day_items(tmp_path):
    s = _store(tmp_path)
    did = s.add("2026-08-01", "insight", "下午同个报错查了 3 次",
                data={"apps": ["VSCode"]}, confidence=0.8)
    assert isinstance(did, int) and did > 0
    s.add("2026-08-01", "pattern", "工作日上午深度使用 VSCode", confidence=0.9)
    items = s.day_items("2026-08-01")
    assert len(items) == 2
    first = items[0]
    assert first["kind"] == "insight"
    assert first["text"] == "下午同个报错查了 3 次"
    assert first["data"] == {"apps": ["VSCode"]}
    assert first["confidence"] == 0.8
    assert first["projected"] == 0
    assert s.day_items("2026-07-31") == []
    s.close()


def test_store_mark_projected(tmp_path):
    s = _store(tmp_path)
    a = s.add("2026-08-01", "insight", "甲")
    b = s.add("2026-08-01", "insight", "乙")
    s.mark_projected([a])
    items = {it["id"]: it for it in s.day_items("2026-08-01")}
    assert items[a]["projected"] == 1
    assert items[b]["projected"] == 0
    s.close()


def test_store_runs_and_last_auto_run_day(tmp_path):
    s = _store(tmp_path)
    assert s.last_auto_run_day() is None
    s.record_run("2026-08-02", "2026-08-01", "auto", "ok")
    s.record_run("2026-08-02", "2026-08-01", "manual", "ok")
    assert s.last_auto_run_day() == "2026-08-02"
    s.close()


def test_store_purge_keeps_14_days(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    old = now - 15 * 86400
    fresh = now - 1 * 86400
    conn = s._conn  # 直接插行控制 created_at
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-17', 'event', '旧', ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO distillations (day, kind, text, created_at) VALUES ('2026-07-31', 'event', '新', ?)",
        (fresh,),
    )
    conn.execute(
        "INSERT INTO runs (run_day, target_day, source, status, created_at) VALUES ('2026-07-17', '2026-07-16', 'auto', 'ok', ?)",
        (old,),
    )
    conn.commit()
    deleted = s.purge(now=now)
    assert deleted == 2  # 1 条旧 distillation + 1 条旧 run
    assert [it["text"] for it in s.day_items("2026-07-31")] == ["新"]
    s.close()


def test_auto_run_due():
    # 2026-08-02 是本地时间；用 mktime 构造本地时间戳避免时区陷阱
    morning = time.mktime((2026, 8, 2, 5, 0, 0, 0, 0, -1))
    early = time.mktime((2026, 8, 2, 4, 10, 0, 0, 0, -1))
    assert auto_run_due(morning, None) is True
    assert auto_run_due(morning, "2026-08-01") is True   # 上次跑是昨天
    assert auto_run_due(morning, "2026-08-02") is False  # 今日已跑
    assert auto_run_due(early, None) is False            # 还没到 04:17


def _pstore(tmp_path):
    return PerceptionStore(str(tmp_path / "obs.db"), key=Fernet.generate_key())


def test_yesterday_window():
    now = time.mktime((2026, 8, 2, 15, 30, 0, 0, 0, -1))
    day, start, end = yesterday_window(now)
    assert day == "2026-08-01"
    assert end - start == 86400
    assert time.localtime(start).tm_hour == 0
    assert time.localtime(end) == time.localtime(time.mktime((2026, 8, 2, 0, 0, 0, 0, 0, -1)))


def test_gather_summary_basic(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("app", "frontmost", {"app": "Chrome", "title": "文档"}, "S1", ts=start + 3700)
    p.append("activity", "active", {"idle_seconds": 0}, "S1", ts=start + 100)
    p.append("activity", "idle", {"idle_seconds": 90}, "S1", ts=start + 4000)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "def main() ..."}, "S3",
             ts=start + 200)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "def main() ..."}, "S3",
             ts=start + 300)  # 重复条目应去重
    summary, stats = gather_summary(p, start, end)
    assert stats["app_count"] == 2
    assert stats["screen_count"] == 1
    assert "VSCode" in summary
    assert "Chrome" in summary
    assert summary.count("def main()") == 1
    assert "应用使用" in summary
    p.close()


def test_gather_summary_empty(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    summary, stats = gather_summary(p, start, end)
    assert stats["app_count"] == 0
    assert stats["screen_count"] == 0
    p.close()


def test_gather_summary_budget_keeps_recent(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 10)
    for i in range(50):
        p.append("screen", "tree", {"app": "Chrome", "title": f"p{i}", "text": f"第{i}条 " + "x" * 100},
                 "S3", ts=start + 100 + i * 60)
    summary, stats = gather_summary(p, start, end, char_budget=3000)
    assert len(summary) <= 3100
    assert "第49条" in summary   # 保最近
    assert "第0条" not in summary  # 弃最旧
    assert "VSCode" in summary   # 头部统计不丢
    p.close()


def test_gather_summary_context_evidence(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 10)
    summary, _ = gather_summary(
        p, start, end,
        memories=["用户通常在 23 点后休息"],
        history=[{"role": "user", "content": "帮我看看这个报错"}, {"role": "tool", "content": "x"}],
    )
    assert "近期记忆" in summary and "23 点后休息" in summary
    assert "近期对话" in summary and "帮我看看这个报错" in summary
    assert "- tool:" not in summary  # tool 消息不进佐证
    p.close()


from yibao_brain.distiller import parse_distill_output  # noqa: E402


def test_parse_valid_output():
    text = '{"patterns": [{"text": "上午深度用 VSCode", "confidence": 0.9}],' \
           ' "insights": [{"text": "同个报错查了 3 次", "confidence": 0.75, "data": {"app": "Chrome"}}],' \
           ' "events": []}'
    out = parse_distill_output(text)
    assert out is not None
    assert out["patterns"][0]["text"] == "上午深度用 VSCode"
    assert out["patterns"][0]["confidence"] == 0.9
    assert out["patterns"][0]["data"] == {}
    assert out["insights"][0]["data"] == {"app": "Chrome"}
    assert out["events"] == []


def test_parse_fenced_output():
    text = "```json\n{\"patterns\": [], \"insights\": [], \"events\": [{\"text\": \"凌晨 2 点仍活跃\"}]}\n```"
    out = parse_distill_output(text)
    assert out is not None
    assert out["events"][0]["text"] == "凌晨 2 点仍活跃"
    assert out["events"][0]["confidence"] == 0.5  # 缺省置信度


def test_parse_invalid_outputs():
    assert parse_distill_output("") is None
    assert parse_distill_output(None) is None
    assert parse_distill_output("这不是 JSON") is None
    assert parse_distill_output("[1,2,3]") is None                      # 不是 dict
    assert parse_distill_output('{"patterns": "oops"}') is None         # 键类型错
    # 坏条目被丢弃而不是整体失败
    out = parse_distill_output('{"patterns": [{"no_text": 1}, {"text": "好"}], "insights": 5, "events": []}')
    # patterns 里无 text 的被丢；insights 类型错 → 整体 None
    assert out is None
    out2 = parse_distill_output('{"patterns": [{"no_text": 1}, {"text": "好", "confidence": 3}], "insights": [], "events": []}')
    assert [p["text"] for p in out2["patterns"]] == ["好"]
    assert out2["patterns"][0]["confidence"] == 1.0  # 钳到 0-1


from yibao_brain.distiller import Distiller  # noqa: E402
from yibao_brain.feed import FeedStore  # noqa: E402
from yibao_brain.llm import FakeProvider  # noqa: E402

_GOOD_JSON = (
    '{"patterns": [{"text": "上午深度用 VSCode", "confidence": 0.9}],'
    ' "insights": [{"text": "低置信洞察", "confidence": 0.4},'
    ' {"text": "洞察A", "confidence": 0.9}, {"text": "洞察B", "confidence": 0.8},'
    ' {"text": "洞察C", "confidence": 0.7}, {"text": "洞察D", "confidence": 0.65}],'
    ' "events": [{"text": "凌晨 2 点仍活跃", "confidence": 0.9}]}'
)


class _Mem:
    def __init__(self):
        self.added: list[str] = []

    def add(self, text, user_id):
        self.added.append(text)
        return True

    def recall(self, query, user_id):
        return []


def _distiller(tmp_path, provider, p=None):
    p = p or _pstore(tmp_path)
    feed = FeedStore(str(tmp_path / "feed.db"))
    mem = _Mem()
    d = Distiller(
        store=_store(tmp_path), pstore=p, provider=provider,
        memory=mem, feed=feed, user_id="default",
    )
    return d, mem, feed


def test_run_yesterday_end_to_end(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "代码"}, "S3", ts=start + 200)
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider, p)

    result = d.run_yesterday("manual")
    assert result["status"] == "ok"
    assert result["day"] == day

    # 原料全落库：1 pattern + 5 insight + 1 event
    items = d.store.day_items(day)
    assert len([i for i in items if i["kind"] == "pattern"]) == 1
    assert len([i for i in items if i["kind"] == "insight"]) == 5
    assert len([i for i in items if i["kind"] == "event"]) == 1

    # pattern → mem0（只写 pattern）
    assert mem.added == ["上午深度用 VSCode"]

    # insight 投影：置信度 ≥0.6 的前 3 条（D 0.65 落选，低置信 0.4 过滤）
    feed_items = feed.recent(limit=20)  # ts 倒序；reversed 恢复写入顺序
    insights = [f for f in reversed(feed_items) if f["meta"].get("type") == "distill_insight"]
    assert [f["text"] for f in insights] == ["洞察A", "洞察B", "洞察C"]
    assert all(f["meta"]["distill_id"] for f in insights)

    # event 走 append_hourly 合并写
    events = [f for f in feed_items if f["meta"].get("type") == "distill_event"]
    assert any("凌晨 2 点仍活跃" in f["text"] for f in events)

    # runs 表记录
    assert d.store.last_auto_run_day() is None  # manual 不算 auto
    d.store.close()
    p.close()


def test_run_yesterday_no_data_skips_llm(tmp_path):
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider)
    result = d.run_yesterday("auto")
    assert result["status"] == "no_data"
    assert provider.calls == []            # 空数据零出站
    assert feed.recent() == []
    assert d.store.last_auto_run_day() is not None  # 但运行记录落库（防重跑）
    d.store.close()


def test_run_yesterday_bad_llm_output(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text="模型抽风输出")
    d, mem, feed = _distiller(tmp_path, provider, p)
    result = d.run_yesterday("auto")
    assert result["status"] == "failed"
    assert feed.recent() == []             # 未解析文本绝不投影
    assert mem.added == []
    d.store.close()
    p.close()


def test_run_yesterday_llm_exception(tmp_path):
    class _Boom:
        def chat(self, messages, tools=None, timeout=None):
            raise RuntimeError("网络炸了")

    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    d, mem, feed = _distiller(tmp_path, _Boom(), p)
    result = d.run_yesterday("auto")       # 不抛异常
    assert result["status"] == "failed"
    assert "网络炸了" in result["error"]
    assert feed.recent() == []
    d.store.close()
    p.close()


def test_run_yesterday_memory_failure_still_projects_feed(tmp_path):
    class _BadMem(_Mem):
        def add(self, text, user_id):
            raise RuntimeError("mem0 挂了")

    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text=_GOOD_JSON)
    feed = FeedStore(str(tmp_path / "feed.db"))
    d = Distiller(store=_store(tmp_path), pstore=p, provider=provider,
                  memory=_BadMem(), feed=feed, user_id="default")
    result = d.run_yesterday("manual")
    assert result["status"] == "ok"        # mem0 挂不影响整体
    assert any(f["meta"].get("type") == "distill_insight" for f in feed.recent())
    d.store.close()
    p.close()


def test_run_yesterday_mutex(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    provider = FakeProvider(text=_GOOD_JSON)
    d, mem, feed = _distiller(tmp_path, provider, p)
    assert d._run_lock.acquire(blocking=False)  # 模拟进行中的任务
    try:
        result = d.run_yesterday("manual")
        assert result["status"] == "already_running"
    finally:
        d._run_lock.release()
    d.store.close()
    p.close()


def test_run_yesterday_passes_timeout_60(tmp_path):
    """离线提炼的 LLM 调用必须带 60s 超时（防僵死连接挂住调度循环）。"""
    from yibao_brain.llm import LLMResponse

    class _Rec:
        def __init__(self):
            self.chat_kwargs: dict | None = None

        def chat(self, messages, tools=None, **kwargs):
            self.chat_kwargs = kwargs
            return LLMResponse(text=_GOOD_JSON)

    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    rec = _Rec()
    d, mem, feed = _distiller(tmp_path, rec, p)
    result = d.run_yesterday("manual")
    assert result["status"] == "ok"
    assert rec.chat_kwargs == {"timeout": 60}
    d.store.close()
    p.close()


# ---------- serve_async 集成：{"type":"distill_now"} IPC 端到端（感知 v3） ----------

import asyncio  # noqa: E402
import json  # noqa: E402

from yibao_brain.memory import FakeMemory  # noqa: E402
from yibao_brain.server import serve_async  # noqa: E402


def _make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def _run_async(coro):
    return asyncio.run(coro)


def _write_settings(tmp_path, values: dict):
    (tmp_path / "settings.json").write_text(json.dumps(values), encoding="utf-8")


def test_distill_now_disabled_when_distill_off_or_master_off(tmp_path, monkeypatch):
    """出站闸门 = master AND distill（与设置页从属语义对齐）：任一关闭 → disabled 回包，零出站。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    p = _pstore(tmp_path)
    for overrides in (
        {"perception.master": True, "perception.distill": False},   # distill 关
        {"perception.master": False, "perception.distill": True},   # master 关（从属语义）
    ):
        _write_settings(tmp_path, overrides)
        provider = FakeProvider(text=_GOOD_JSON)
        out = []
        _run_async(serve_async(
            _make_reader([{"type": "distill_now"}]),
            out.append,
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=provider,
            perception_store=p,
        ))
        replies = [m for m in out if m["type"] == "distill_now"]
        assert replies == [{"type": "distill_now", "ok": False, "reason": "disabled"}]
        assert provider.calls == []  # 零出站
    p.close()


def test_distill_now_end_to_end(tmp_path, monkeypatch):
    """master+distill 双开：昨日 A 源观察 → LLM 提炼 → distill.db 落原料 + Feed 投影 + mem0 收 pattern。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    _write_settings(tmp_path, {"perception.master": True, "perception.distill": True})
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("screen", "tree", {"app": "VSCode", "title": "a.py", "text": "代码"}, "S3", ts=start + 200)
    mem = FakeMemory()
    # 注入受控记忆实例（test_mem_settings 同款手法），断言 pattern 真的进了 mem0
    monkeypatch.setattr("yibao_brain.server.FakeMemory", lambda: mem)
    provider = FakeProvider(text=_GOOD_JSON)

    out = []
    _run_async(serve_async(
        _make_reader([{"type": "distill_now"}]),
        out.append,
        use_real=False,
        db_path=str(tmp_path / "a.db"),
        provider=provider,
        perception_store=p,
    ))

    replies = [m for m in out if m["type"] == "distill_now"]
    assert len(replies) == 1
    assert replies[0]["ok"] is True
    assert replies[0]["result"]["status"] == "ok"
    assert replies[0]["result"]["day"] == day

    # 原料落 distill.db（serve_async 把库建在 dirname(db_path) 下）
    store = DistillerStore(str(tmp_path / "distill.db"))
    kinds = {i["kind"] for i in store.day_items(day)}
    assert kinds == {"pattern", "insight", "event"}
    store.close()

    # insight（置信度 ≥0.6 的前 3 条）投影 Feed
    feed = FeedStore(str(tmp_path / "feed.db"))
    insights = [f for f in feed.recent(limit=50) if f["meta"].get("type") == "distill_insight"]
    assert [f["text"] for f in reversed(insights)] == ["洞察A", "洞察B", "洞察C"]
    feed.close()

    # pattern 写 mem0
    assert "上午深度用 VSCode" in [m["text"] for m in mem.list_all("default")]
    p.close()


def test_gather_summary_returns_activity_stats(tmp_path):
    p = _pstore(tmp_path)
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "a.py"}, "S1", ts=start + 100)
    p.append("app", "frontmost", {"app": "VSCode", "title": "b.py"}, "S2", ts=start + 2000)
    _summary, stats = gather_summary(p, start, end)
    assert "app_seconds" in stats and "active_ranges" in stats
    assert stats["app_seconds"]["VSCode"] > 0
    assert all(isinstance(r, list) and len(r) == 2 for r in stats["active_ranges"])
