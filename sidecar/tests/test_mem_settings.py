"""设置增强（OS 感 §4.4）：记忆管理（mem_list/mem_delete）+ 自主权旋钮（settings_get/set）。"""
import asyncio
import json
import os

from yibao_brain import plugins
from yibao_brain.config import load_settings, settings_path
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


# ---------- 自主权旋钮（settings.json）----------


def test_settings_get_defaults(tmp_path, monkeypatch):
    out = _serve(tmp_path, monkeypatch, [{"type": "settings_get"}])
    r = [m for m in out if m["type"] == "settings"]
    assert len(r) == 1
    assert r[0]["values"]["proactive_voice"] is True
    assert r[0]["values"]["perception.model_access"] is False


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
        "perception.master": False,
        "perception.app": False,
        "perception.activity": False,
        "perception.model_access": True,
    }


def test_settings_bad_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    os.makedirs(tmp_path, exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as f:
        f.write("{bad json")
    assert load_settings() == {
        "proactive_voice": True,
        "perception.master": False,
        "perception.app": False,
        "perception.activity": False,
        "perception.model_access": False,
    }
