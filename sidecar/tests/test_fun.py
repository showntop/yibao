"""娱乐插件：视频热榜（B站）/ 音乐直达 / 每日一言。"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("YIBAO_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def env(data_dir):
    from yibao_brain.llm import FakeProvider
    from yibao_brain.memory import FakeMemory
    from yibao_brain.plugins import LlmChat, load_plugins
    from yibao_brain.skills import SkillRegistry

    reg = SkillRegistry()

    class _Http:
        def get(self, url, **kw):
            return {}

        def post(self, url, **kw):
            return {}

    results = load_plugins(
        REPO_ROOT / "plugins", reg,
        memory=FakeMemory(), http=_Http(), llm=LlmChat(FakeProvider()),
    )
    return reg, results


def _run(reg, sid, params):
    t = reg.get(sid)
    assert t is not None, f"技能未注册: {sid}"
    return t.run(params, t.plugin_ctx)


# ---------- 插件加载与 api 白名单 ----------


def test_plugin_loads(env):
    _, results = env
    assert results.get("fun") == "ok"


def test_api_methods_registered(env):
    from yibao_brain.plugins import get_api

    env[0]  # 触发加载
    for name in ("fun.open", "fun.videos", "fun.music", "fun.quote"):
        api = get_api(name)
        assert api is not None and api.direct, name
    assert get_api("fun.open").panel == "fun:main"


def test_open_defaults_and_tab(env):
    reg, _ = env
    r = _run(reg, "fun.open", {})
    assert r.success and r.panel == "fun:main" and r.data["tab"] == "videos"
    assert _run(reg, "fun.open", {"tab": "quote"}).data["tab"] == "quote"
    assert _run(reg, "fun.open", {"tab": "wat"}).data["tab"] == "videos"  # 未知 tab 兜底


# ---------- videos（B站热门 + 排行榜备源） ----------

_POPULAR_JSON = {
    "code": 0,
    "data": {"list": [
        {
            "bvid": "BV1xx111", "title": "K3 评测「&quot;神机&quot;」",
            "duration": 250, "tname": "科技",
            "owner": {"name": "UP主甲"},
            "stat": {"view": 123456, "like": 7890},
        },
        {
            "bvid": "BV1xx222", "title": "猫猫翻车现场",
            "duration": 31, "tname": "生活",
            "owner": {"name": "UP主乙"},
            "stat": {"view": 99999999, "like": 12},
        },
    ]},
}
_RANKING_JSON = {
    "code": 0,
    "data": {"list": [
        {
            "bvid": "BV1rr333", "title": "影视区第一名",
            "duration": 5400, "tname": "影视",
            "owner": {"name": "UP主丙"},
            "stat": {"view": 150000000, "like": 9999},
        },
    ]},
}


def _fake_bili(monkeypatch, reg, broken=()):
    """按 url 分发假接口；broken 里的关键字抛异常（模拟网络挂/结构变）。"""
    g = type(reg.get("fun.videos")).run.__globals__

    def _fake(url):
        for key, payload in (("popular", _POPULAR_JSON), ("ranking", _RANKING_JSON)):
            if key in url:
                if key in broken:
                    raise OSError("boom")
                return payload
        raise AssertionError(f"未预期的 url：{url}")

    monkeypatch.setitem(g, "_fetch_json", _fake)


def test_videos_popular_parsed(env, monkeypatch):
    reg, _ = env
    _fake_bili(monkeypatch, reg)
    r = _run(reg, "fun.videos", {})
    assert r.success and r.panel == "fun:main"
    assert r.data["region"] == "全站" and r.data["tid"] == "0" and r.data["failed"] == []
    rows = r.data["rows"]
    assert rows[0]["title"] == 'K3 评测「"神机"」'  # HTML 实体已还原
    assert rows[0]["bvid"] == "BV1xx111"
    assert rows[0]["url"] == "https://www.bilibili.com/video/BV1xx111"
    assert rows[0]["author"] == "UP主甲"
    assert rows[0]["views"] == "12.3万"
    assert rows[0]["likes"] == "7890"
    assert rows[0]["duration"] == "04:10"
    assert rows[0]["region"] == "科技"
    # 播放量过亿 → 「亿」读法；秒数过 1h → h:mm:ss
    assert rows[1]["views"] == "1亿"
    assert rows[1]["duration"] == "00:31"


def test_videos_region_uses_ranking(env, monkeypatch):
    reg, _ = env
    _fake_bili(monkeypatch, reg)
    r = _run(reg, "fun.videos", {"tid": "5"})
    assert r.success and r.data["region"] == "影视" and r.data["failed"] == []
    rows = r.data["rows"]
    assert rows[0]["bvid"] == "BV1rr333"
    assert rows[0]["duration"] == "1:30:00" and rows[0]["views"] == "1.5亿"


def test_videos_falls_back_to_ranking(env, monkeypatch):
    reg, _ = env
    _fake_bili(monkeypatch, reg, broken=("popular",))
    r = _run(reg, "fun.videos", {})
    assert r.success and r.data["failed"] == ["全站热门"]
    assert r.data["rows"][0]["bvid"] == "BV1rr333"


def test_videos_all_sources_failed(env, monkeypatch):
    reg, _ = env
    _fake_bili(monkeypatch, reg, broken=("popular", "ranking"))
    r = _run(reg, "fun.videos", {})
    assert not r.success and "排行榜" in r.error


def test_videos_unknown_region_and_limit(env, monkeypatch):
    reg, _ = env
    _fake_bili(monkeypatch, reg)
    assert not _run(reg, "fun.videos", {"tid": "999"}).success
    r = _run(reg, "fun.videos", {"tid": "0", "limit": 999})
    assert r.success and len(r.data["rows"]) <= 2  # 上限截断到 20


# ---------- music（平台入口 + 搜索链接生成，纯本地无网络） ----------


def test_music_platforms_without_kw(env):
    reg, _ = env
    r = _run(reg, "fun.music", {})
    assert r.success and r.panel == "fun:main"
    names = [p["name"] for p in r.data["platforms"]]
    assert "网易云音乐" in names and "QQ音乐" in names
    assert r.data["search"] == [] and r.data["keywords"]


def test_music_search_generates_encoded_links(env):
    reg, _ = env
    r = _run(reg, "fun.music", {"kw": "周杰伦"})
    assert r.success and r.data["kw"] == "周杰伦"
    links = {s["name"]: s["url"] for s in r.data["search"]}
    assert "网易云音乐" in links
    assert "https://music.163.com/#/search/m/?s=%E5%91%A8%E6%9D%B0%E4%BC%A6" in links["网易云音乐"]
    assert "https://y.qq.com/n/ryqq/search?w=%E5%91%A8%E6%9D%B0%E4%BC%A6" in links["QQ音乐"]
    assert len(links) == 4  # 四个平台都给


def test_music_blank_kw_treated_as_home(env):
    reg, _ = env
    r = _run(reg, "fun.music", {"kw": "   "})
    assert r.success and r.data["kw"] == "" and r.data["search"] == []


# ---------- quote（一言） ----------


def _fake_hitokoto(monkeypatch, reg, rows=None):
    rows = list(rows) if rows is not None else ["星影落九天，鱼雁舞千弦。", "少年与爱永不老去。"]
    g = type(reg.get("fun.quote")).run.__globals__

    def _fake(url):
        if rows:
            return {"text": rows.pop(0), "from": "测试出处"}
        return None

    monkeypatch.setitem(g, "_fetch_one", _fake)


def test_quote_multiple_and_dedupe(env, monkeypatch):
    reg, _ = env
    _fake_hitokoto(monkeypatch, reg)
    r = _run(reg, "fun.quote", {"count": 2})
    assert r.success and r.panel == "fun:main"
    assert [q["text"] for q in r.data["rows"]] == ["星影落九天，鱼雁舞千弦。", "少年与爱永不老去。"]
    assert r.data["cat"] == "" and r.data["cat_cn"] == "随机"


def test_quote_with_category(env, monkeypatch):
    reg, _ = env
    _fake_hitokoto(monkeypatch, reg, rows=("哲学句子",))
    r = _run(reg, "fun.quote", {"cat": "k"})
    assert r.success and r.data["cat_cn"] == "哲学"
    # 分类透传到 url
    g = type(reg.get("fun.quote")).run.__globals__
    urls = []

    def _fake(url):
        urls.append(url)
        return {"hitokoto": "x", "from": ""}

    monkeypatch.setitem(g, "_fetch_one", _fake)
    _run(reg, "fun.quote", {"cat": "h", "count": 1})
    assert urls and "?c=h" in urls[0]


def test_quote_unknown_cat_and_all_failed(env, monkeypatch):
    reg, _ = env
    assert not _run(reg, "fun.quote", {"cat": "z"}).success
    _fake_hitokoto(monkeypatch, reg, rows=())
    r = _run(reg, "fun.quote", {})
    assert not r.success and "一言拉取失败" in r.error


def test_quote_count_clamped(env, monkeypatch):
    reg, _ = env
    seen = {"n": 0}

    def _fake(url):
        seen["n"] += 1
        return {"hitokoto": f"第{seen['n']}句", "from": ""}

    g = type(reg.get("fun.quote")).run.__globals__
    monkeypatch.setitem(g, "_fetch_one", _fake)
    r = _run(reg, "fun.quote", {"count": 99})
    assert r.success and len(r.data["rows"]) == 10  # 上限 10
