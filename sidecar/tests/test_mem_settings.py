"""设置增强（OS 感 §4.4）：记忆管理（mem_list/mem_delete）+ 自主权旋钮（settings_get/set）。"""
import asyncio
import json
import os

from yibao_brain import plugins
from yibao_brain.config import load_settings, save_settings, search_api_key, settings_path
from yibao_brain.llm import FakeProvider
from yibao_brain.memory import FakeMemory
from yibao_brain.server import serve_async


def make_reader(msgs):
    it = iter(msgs + [None])  # 末尾 None = stdin 结束
    return lambda: next(it)


def _run_async(coro):
    return asyncio.run(coro)


def _serve(tmp_path, monkeypatch, msgs, mem=None, namespaces=None):
    """以受控记忆实例跑一轮 serve_async；命名空间表显式给定（防其他测试残留污染断言）。"""
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(plugins, "_PLUGIN_MEM_NS", namespaces or {})
    if mem is not None:
        monkeypatch.setattr("yibao_brain.server.FakeMemory", lambda: mem)
    out = []
    _run_async(
        serve_async(
            make_reader(msgs),
            lambda m: out.append(m),
            use_real=False,
            db_path=str(tmp_path / "a.db"),
            provider=FakeProvider(),
        )
    )
    return out


# ---------- 记忆管理 ----------


def test_mem_list_empty(tmp_path, monkeypatch):
    out = _serve(tmp_path, monkeypatch, [{"type": "mem_list"}])
    r = [m for m in out if m["type"] == "mem_list"]
    assert len(r) == 1
    assert r[0]["items"] == [] and r[0]["ready"] is True and r[0]["failed"] is False


def test_mem_list_groups_by_namespace(tmp_path, monkeypatch):
    mem = FakeMemory()
    mem.add("喜欢喝美式", "default")            # 底座（译宝）
    mem.add("偏爱短句", "notes:default")        # 插件命名空间
    out = _serve(tmp_path, monkeypatch, [{"type": "mem_list"}], mem=mem, namespaces={"notes": "闪念盘"})
    r = [m for m in out if m["type"] == "mem_list"][0]
    got = {(i["label"], i["text"], i["ns"]) for i in r["items"]}
    assert got == {("译宝", "喜欢喝美式", ""), ("闪念盘", "偏爱短句", "notes")}


def test_mem_delete_roundtrip(tmp_path, monkeypatch):
    mem = FakeMemory()
    mem.add("甲", "default")
    mem.add("乙", "default")
    out = _serve(
        tmp_path, monkeypatch,
        [{"type": "mem_delete", "mem_id": "default:0"}, {"type": "mem_list"}],
        mem=mem,
    )
    d = [m for m in out if m["type"] == "mem_deleted"]
    assert len(d) == 1 and d[0] == {"type": "mem_deleted", "id": "default:0", "ok": True}
    lst = [m for m in out if m["type"] == "mem_list"][0]
    assert [i["text"] for i in lst["items"]] == ["乙"]


def test_mem_delete_bad_id_reports_not_ok(tmp_path, monkeypatch):
    out = _serve(tmp_path, monkeypatch, [{"type": "mem_delete", "mem_id": "ghost:9"}], mem=FakeMemory())
    d = [m for m in out if m["type"] == "mem_deleted"][0]
    assert d["ok"] is False and d["error"]


def test_mem_edit_roundtrip(tmp_path, monkeypatch):
    mem = FakeMemory()
    mem.add("喜欢美式", "default")
    out = _serve(
        tmp_path, monkeypatch,
        [{"type": "mem_edit", "mem_id": "default:0", "text": "喜欢拿铁"}, {"type": "mem_list"}],
        mem=mem,
    )
    e = [m for m in out if m["type"] == "mem_edited"]
    assert len(e) == 1 and e[0] == {"type": "mem_edited", "id": "default:0", "ok": True}
    lst = [m for m in out if m["type"] == "mem_list"][0]
    assert [i["text"] for i in lst["items"]] == ["喜欢拿铁"]


def test_mem_edit_bad_id_or_empty_text_reports_not_ok(tmp_path, monkeypatch):
    out = _serve(
        tmp_path, monkeypatch,
        [{"type": "mem_edit", "mem_id": "ghost:9", "text": "x"},
         {"type": "mem_edit", "mem_id": "default:0", "text": "   "},
         {"type": "mem_edit", "text": "x"}],
        mem=FakeMemory(),
    )
    es = [m for m in out if m["type"] == "mem_edited"]
    assert len(es) == 3
    assert all(e["ok"] is False and e["error"] for e in es)


def test_mem_list_all_uses_filters_not_top_level_user_id():
    """mem0 2.x get_all 不再接受 top-level user_id，必须走 filters（回归：记忆管理全空 bug）。
    且按 created_at 倒序（最新在前）——mem0 get_all 不保证时间序，曾在列表中间看不到新增。"""
    from unittest.mock import MagicMock
    from yibao_brain.memory import Mem0Memory
    m = Mem0Memory.__new__(Mem0Memory)  # 跳过 from_config，直接注入 mock
    fake = MagicMock()
    fake.get_all.return_value = {"results": [
        {"id": "a", "memory": "旧偏好", "created_at": "2026-07-01T00:00:00+00:00"},
        {"id": "b", "memory": "新偏好", "created_at": "2026-07-29T00:00:00+00:00"},
    ]}
    m._m = fake
    out = m.list_all("default")
    fake.get_all.assert_called_once_with(filters={"user_id": "default"})
    assert [i["id"] for i in out] == ["b", "a"]  # 倒序：最新在前
    assert out[0]["created_at"] == "2026-07-29T00:00:00+00:00"


# ---------- 自主权旋钮（settings.json）----------


def test_settings_get_defaults(tmp_path, monkeypatch):
    out = _serve(tmp_path, monkeypatch, [{"type": "settings_get"}])
    r = [m for m in out if m["type"] == "settings"]
    assert len(r) == 1
    assert r[0]["values"]["proactive_voice"] is True
    assert r[0]["values"]["perception.model_access"] is False
    assert r[0]["values"]["dock_pinned"] == []


def test_settings_set_persists_and_ignores_unknown(tmp_path, monkeypatch):
    out = _serve(
        tmp_path, monkeypatch,
        [{"type": "settings_set", "values": {
            "proactive_voice": False,
            "perception.model_access": True,
            "hack": 1,
        }},
         {"type": "settings_get"}],
    )
    rs = [m for m in out if m["type"] == "settings"]
    assert rs[0]["values"]["proactive_voice"] is False  # set 的回执
    assert rs[1]["values"]["proactive_voice"] is False  # get 复读
    assert rs[0]["values"]["perception.model_access"] is True
    assert rs[1]["values"]["perception.model_access"] is True
    assert "hack" not in rs[0]["values"]                # 未知键不落
    disk = json.load(open(settings_path(), encoding="utf-8"))
    assert disk == {
        "proactive_voice": False,
        "proactive.level": "full",
        "perception.master": False,
        "perception.app": False,
        "perception.activity": False,
        "perception.model_access": True,
        "perception.distill": False,
        "perception.recap": False,
        "perception.screen": False,
        "perception.blacklist": [],
        "dock_pinned": [],
        "tts.provider": "edge",
        "watch.enabled": False,
        "watch.screen_enabled": False,
        "watch.cadence": 60,
        "watch.idle_warn_minutes": 45,
        "watch.quiet_hours": "23:00-07:00",
        "watch.observe_apps": [],
        "watch.look_min_gap": 300,
        "watch.look_max_per_hour": 6,
        "watch.look_max_per_day": 50,
        "http.token": "",
        "http.mobile_token": "",
        "http.public_url": "",
        "push.devices": [],
        "search.provider": "browser",
        "search.searxng_url": "",
        "search.keys": {},
    }


def test_settings_proactive_level_default_full(tmp_path, monkeypatch):
    out = _serve(tmp_path, monkeypatch, [{"type": "settings_get"}])
    r = [m for m in out if m["type"] == "settings"][0]
    assert r["values"]["proactive.level"] == "full"


def test_settings_proactive_level_validated(tmp_path, monkeypatch):
    out = _serve(
        tmp_path, monkeypatch,
        [{"type": "settings_set", "values": {"proactive.level": "quiet"}},
         {"type": "settings_set", "values": {"proactive.level": "loud"}},
         {"type": "settings_get"}],
    )
    rs = [m for m in out if m["type"] == "settings"]
    assert rs[0]["values"]["proactive.level"] == "quiet"  # 合法值生效
    assert rs[1]["values"]["proactive.level"] == "quiet"  # 非法枚举值拒收，保持原值
    assert rs[2]["values"]["proactive.level"] == "quiet"


def test_watch_settings_validate_quiet_hours_numbers_and_bundle_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    save_settings({
        "watch.quiet_hours": "25:00-07:00",
        "watch.cadence": -1,
        "watch.observe_apps": ["", "com.example.App"],
    })
    values = load_settings()
    assert values["watch.quiet_hours"] == "23:00-07:00"
    assert values["watch.cadence"] == 60
    assert values["watch.observe_apps"] == []

    save_settings({
        "watch.quiet_hours": "22:30-06:15",
        "watch.cadence": 15,
        "watch.observe_apps": ["com.example.App", "com.example.App"],
    })
    values = load_settings()
    assert values["watch.quiet_hours"] == "22:30-06:15"
    assert values["watch.cadence"] == 15
    assert values["watch.observe_apps"] == ["com.example.App"]


def test_settings_bad_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as f:
        f.write("{bad json")
    assert load_settings() == {
        "proactive_voice": True,
        "proactive.level": "full",
        "perception.master": False,
        "perception.app": False,
        "perception.activity": False,
        "perception.model_access": False,
        "perception.distill": False,
        "perception.recap": False,
        "perception.screen": False,
        "perception.blacklist": [],
        "dock_pinned": [],
        "tts.provider": "edge",
        "watch.enabled": False,
        "watch.screen_enabled": False,
        "watch.cadence": 60,
        "watch.idle_warn_minutes": 45,
        "watch.quiet_hours": "23:00-07:00",
        "watch.observe_apps": [],
        "watch.look_min_gap": 300,
        "watch.look_max_per_hour": 6,
        "watch.look_max_per_day": 50,
        "http.token": "",
        "http.mobile_token": "",
        "http.public_url": "",
        "push.devices": [],
        "search.provider": "browser",
        "search.searxng_url": "",
        "search.keys": {},
    }


def test_settings_search_keys_whitelist(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    save_settings({"search.keys": {"brave": "bk", "evil": "x", "tavily": 123}})
    assert load_settings()["search.keys"] == {"brave": "bk"}


def test_search_api_key_settings_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    monkeypatch.setenv("YIBAO_SEARCH_BRAVE_KEY", "env-key")
    monkeypatch.setenv("YIBAO_SEARCH_TAVILY_KEY", "t-env")
    assert search_api_key("brave") == "env-key"   # 设置无 → env 兜底
    save_settings({"search.keys": {"brave": "ui-key"}})
    assert search_api_key("brave") == "ui-key"    # 设置优先于 env
    assert search_api_key("tavily") == "t-env"    # 未设置的服务仍走 env
    assert search_api_key("serper") == ""         # 都无 → 空
