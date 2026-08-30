"""SurfaceBridge（design §3 表面层桥）：发射即回执 + 上行缓存 + 不变量白名单。"""

from yibao_brain.surface import SurfaceBridge


def test_dispatch_requires_bind():
    b = SurfaceBridge()
    r = b.dispatch("zimeiti:editor", "editor.set_selection", {"start": 0, "end": 1})
    assert r["ok"] is False and "未就绪" in r["error"]


def test_dispatch_whitelist_is_the_invariant():
    b = SurfaceBridge()
    sent = []
    b.bind(sent.append)
    ok = b.dispatch("zimeiti:editor", "editor.replace_range", {"start": 1, "end": 2, "text": "新"})
    assert ok["ok"] is True and sent and sent[0]["kind"] == "surface_command"
    assert sent[0]["panel"] == "zimeiti:editor"
    assert sent[0]["params"]["sid"].startswith("sr_")
    # 白名单外拒发：agent 无权发明命令，更无权请求 stage/focus
    bad = b.dispatch("zimeiti:editor", "editor.delete_all", {})
    assert bad["ok"] is False and "白名单" in bad["error"]


def test_record_and_snapshot_cache_latest_only():
    b = SurfaceBridge()
    b.record("zimeiti", "zimeiti.selection_changed", {"start": 3})
    b.record("zimeiti", "zimeiti.doc_snapshot", {"content": "全文"})
    assert b.snapshot("zimeiti", "zimeiti.doc_snapshot") == {"content": "全文"}
    assert b.snapshot("zimeiti", "zimeiti.selection_changed") is None  # 被更新事件顶掉
    assert b.snapshot("coding", "x") is None  # 别的面板没有
