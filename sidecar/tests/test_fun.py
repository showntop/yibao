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
    for name in ("fun.list", "fun.open", "fun.videos", "fun.music", "fun.quote", "fun.joke"):
        api = get_api(name)
        assert api is not None and api.direct, name
    assert get_api("fun.open").panel == "fun:main"
    # 插件页/启动器点击娱乐卡片调 <pid>.list 打开主面板（惯例入口）
    assert get_api("fun.list").handler == "fun.open"
    assert get_api("fun.list").panel == "fun:main"


def test_module_panel_registered(env):
    """R4 内嵌面板：manifest type="module"，引用式登记入口，不读全文进内存。"""
    from yibao_brain.plugins import get_panel

    env[0]
    panel = get_panel("fun:main")
    assert panel is not None and panel.get("type") == "module"
    assert panel.get("entry") == "panel/index.html"
    assert "html" not in panel


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
            "pic": "http://i0.hdslb.com/bfs/archive/abc.jpg",
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
    # 封面 http 直链统一转 https（面板 CSP 只放行 https 图片）
    assert rows[0]["pic"] == "https://i0.hdslb.com/bfs/archive/abc.jpg"
    assert rows[1]["pic"] == ""  # 没封面的不补
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


def test_videos_keyword_returns_search_page(env, monkeypatch):
    """精准搜索：keyword 分支返回 B站官方搜索页 URL（面板内嵌），不发网络请求不依赖风控 API。"""
    reg, _ = env
    # keyword 分支不调 _fetch_json——若误发网络请求会真连 api.bilibili.com（测试环境连不通即报错）
    r = _run(reg, "fun.videos", {"keyword": "牛来 电影解说"})
    assert r.success and r.panel == "fun:main"
    assert r.data["mode"] == "search" and r.data["keyword"] == "牛来 电影解说"
    assert r.data["rows"] == []
    assert r.data["search_url"] == (
        "https://search.bilibili.com/all?keyword=%E7%89%9B%E6%9D%A5%20%E7%94%B5%E5%BD%B1%E8%A7%A3%E8%AF%B4"
    )
    # 空关键词走热门分支（不误入搜索模式）
    _fake_bili(monkeypatch, reg)
    hot = _run(reg, "fun.videos", {"keyword": "   "})
    assert hot.data["mode"] == "hot" and hot.data["rows"]


# ---------- music（平台入口 + 热歌榜 + 网易云歌曲搜索 + 平台搜索链接） ----------

# 网易云原始接口响应（搜索字段 artists/album；歌单详情字段 ar/al）
_SEARCH_RAW = {"result": {"songs": [
    {"id": 186016, "name": "晴天", "artists": [{"name": "周杰伦"}], "album": {"name": "叶惠美"}, "duration": 269000},
]}}
_CHART_RAW = {"playlist": {"tracks": [
    {"id": 186016, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}, "duration": 269000},
]}}
_CHART_ID = "3778678"


def _fake_netease(monkeypatch, reg, songs=None, chart=None, broken=()):
    """按 url 分发网易云接口响应；broken 里的关键字抛异常（模拟网络挂/结构变）。"""
    g = type(reg.get("fun.music")).run.__globals__

    def _fake(url):
        for key, payload in (("search/get", songs), ("playlist/detail", chart)):
            if key in url:
                if key in broken:
                    raise OSError("boom")
                return payload if payload is not None else {}
        raise AssertionError(f"未预期的 url：{url}")

    monkeypatch.setitem(g, "_get_json", _fake)


def test_music_platforms_without_kw(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg, chart=_CHART_RAW)
    r = _run(reg, "fun.music", {})
    assert r.success and r.panel == "fun:main"
    names = [p["name"] for p in r.data["platforms"]]
    assert "网易云音乐" in names and "QQ音乐" in names
    assert r.data["search"] == [] and r.data["keywords"] and r.data["songs"] == []
    assert not r.data["chart_failed"] and r.data["chart"]


def test_music_chart_returns_songs(env, monkeypatch):
    """热歌榜：v6 歌单详情（ar/al 字段）归一化成歌曲，点播 auto=1 自动播。"""
    reg, _ = env
    _fake_netease(monkeypatch, reg, chart=_CHART_RAW)
    r = _run(reg, "fun.music", {})
    chart = r.data["chart"]
    assert len(chart) == 1
    s = chart[0]
    assert s["name"] == "晴天" and s["artist"] == "周杰伦" and s["album"] == "叶惠美"
    assert s["duration"] == "4:29"
    assert s["embed_url"] == "https://music.163.com/outchain/player?type=2&id=186016&auto=1&height=66"
    assert s["page_url"] == "https://music.163.com/#/song?id=186016"


def test_music_chart_failure_isolated(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg, broken=("playlist/detail",))
    r = _run(reg, "fun.music", {})
    assert r.success and r.data["chart"] == [] and r.data["chart_failed"]
    assert r.data["platforms"]  # 榜单挂了不拖垮：平台入口照常给


def test_music_search_generates_encoded_links(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg)
    r = _run(reg, "fun.music", {"kw": "周杰伦"})
    assert r.success and r.data["kw"] == "周杰伦"
    links = {s["name"]: s["url"] for s in r.data["search"]}
    assert "网易云音乐" in links
    assert "https://music.163.com/#/search/m/?s=%E5%91%A8%E6%9D%B0%E4%BC%A6" in links["网易云音乐"]
    assert "https://y.qq.com/n/ryqq/search?w=%E5%91%A8%E6%9D%B0%E4%BC%A6" in links["QQ音乐"]
    assert len(links) == 4  # 四个平台都给
    assert r.data["songs"] == [] and not r.data["songs_failed"]


def test_music_search_returns_netease_songs(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg, songs=_SEARCH_RAW)
    r = _run(reg, "fun.music", {"kw": "晴天"})
    assert r.success and not r.data["songs_failed"]
    songs = r.data["songs"]
    assert len(songs) == 1 and songs[0]["id"] == "186016" and songs[0]["name"] == "晴天"
    assert songs[0]["embed_url"] == "https://music.163.com/outchain/player?type=2&id=186016&auto=1&height=66"
    assert songs[0]["page_url"] == "https://music.163.com/#/song?id=186016"


def test_music_search_failure_isolated(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg, broken=("search/get",))
    r = _run(reg, "fun.music", {"kw": "周杰伦"})
    assert r.success and r.data["songs"] == [] and r.data["songs_failed"]
    assert r.data["search"]  # 网易云挂了不拖垮：平台入口照常给


def test_music_blank_kw_treated_as_home(env, monkeypatch):
    reg, _ = env
    _fake_netease(monkeypatch, reg, chart=_CHART_RAW)
    r = _run(reg, "fun.music", {"kw": "   "})
    assert r.success and r.data["kw"] == "" and r.data["search"] == [] and r.data["songs"] == []
    assert r.data["chart"]  # 空关键词等同无 kw：给热歌榜


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


# ---------- joke（AI 讲段子：LLM 生成 + hitokoto 降级） ----------


def _joke_globals(reg):
    return type(reg.get("fun.joke")).run.__globals__


def test_joke_registered(env):
    from yibao_brain.plugins import get_api

    env[0]
    api = get_api("fun.joke")
    assert api is not None and api.direct and api.handler == "fun.joke"


def test_joke_via_llm(env, monkeypatch):
    reg, _ = env
    monkeypatch.setitem(_joke_globals(reg), "_llm_joke",
                        lambda ctx: "为什么手机要贴膜？因为怕划伤桌面。")
    r = _run(reg, "fun.joke", {})
    assert r.success and r.panel == "fun:main"
    assert r.data["via"] == "llm" and "贴膜" in r.data["text"] and r.data["from"] == "AI 段子手"


def test_joke_llm_unavailable_falls_back(env, monkeypatch):
    """LLM 不可用/失败 → 降级 hitokoto 抖机灵（不抛错，段子照看）。"""
    reg, _ = env
    monkeypatch.setitem(_joke_globals(reg), "_llm_joke", lambda ctx: None)
    monkeypatch.setitem(_joke_globals(reg), "_fetch_one",
                        lambda url: {"text": "今天和明天先睡觉。", "from": "网络"})
    r = _run(reg, "fun.joke", {})
    assert r.success and r.data["via"] == "hitokoto" and r.data["text"] == "今天和明天先睡觉。"


def test_joke_all_failed(env, monkeypatch):
    reg, _ = env
    monkeypatch.setitem(_joke_globals(reg), "_llm_joke", lambda ctx: None)
    monkeypatch.setitem(_joke_globals(reg), "_fetch_one", lambda url: None)
    r = _run(reg, "fun.joke", {})
    assert not r.success
