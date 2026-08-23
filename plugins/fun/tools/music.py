"""fun.music：音乐直达——热歌榜 + 网易云搜歌，面板内官方播放器闭环听歌。

- 热歌榜：网易云云音乐热歌榜歌单（id=3778678，v6 免登录接口），点歌即面板内嵌 outchain
  官方播放器自动播放（auto=1）。榜单/搜索任一挂了互不拖垮（各自失败标记）。
- 搜歌：公开免登录接口（UA + Referer），返回歌曲 id 构造外链播放器内嵌播放。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_FETCH_TIMEOUT = 8
_MAX_SONGS = 6
_MAX_CHART = 15
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_NETEASE_SEARCH = "https://music.163.com/api/search/get/web?type=1&limit={limit}&s={q}"
_NETEASE_CHART = "https://music.163.com/api/v6/playlist/detail?id={pid}&n={limit}"
_HOT_CHART_ID = "3778678"  # 网易云云音乐热歌榜
_QQ_SEARCH = "https://y.qq.com/n/ryqq/search?w={q}"  # QQ 音乐搜索页（无 X-Frame-Options 可内嵌）

# 平台 → (中文名, 首页, 搜索模板, 备注)。搜索词经 quote() 编码后填 %s
_PLATFORMS: list[tuple[str, str, str, str]] = [
    ("netease", "网易云音乐", "https://music.163.com/", "https://music.163.com/#/search/m/?s=%s", "网页版即点即听"),
    ("qq", "QQ音乐", "https://y.qq.com/", "https://y.qq.com/n/ryqq/search?w=%s", "网页版即点即听"),
    ("kugou", "酷狗音乐", "https://www.kugou.com/", "https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord=%s", "海量翻唱"),
    ("douyin", "汽水音乐", "https://music.douyin.com/", "https://music.douyin.com/?s=%s", "抖音热歌"),
]

# 摸鱼热词（快捷按钮，省得打字；都是公开的日常娱乐向搜索词）
_HOT_KEYWORDS = ("周杰伦", "Taylor Swift", "林俊杰", "古典 纯音乐", "白噪音 助眠", "深夜电台")


def _get_json(url: str) -> dict:
    """GET 网易云 JSON 端点（module-level，测试 monkeypatch 它，不真发网络）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json",
        "Referer": "https://music.163.com/",
    })
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _fmt_duration(sec: int) -> str:
    if sec <= 0:
        return ""
    m, s = divmod(sec, 60)
    return f"{m}:{s:02d}"


def _norm_song(sid: str, name: str, artist: str, album: str, dur_ms: Any) -> dict:
    """歌曲归一化：面板内嵌官方播放器（auto=1 点开即播）。"""
    try:
        dur = _fmt_duration(int(dur_ms) // 1000)
    except (TypeError, ValueError):
        dur = ""
    return {
        "id": sid,
        "name": name,
        "artist": artist,
        "album": album,
        "duration": dur,
        "page_url": f"https://music.163.com/#/song?id={sid}",
        "embed_url": f"https://music.163.com/outchain/player?type=2&id={sid}&auto=1&height=66",
    }


def _fetch_songs(kw: str, limit: int) -> list[dict]:
    """网易云搜索歌曲（搜索接口字段：artists/album）。"""
    data = _get_json(_NETEASE_SEARCH.format(q=urllib.parse.quote(kw), limit=limit))
    out: list[dict] = []
    for s in ((data.get("result") or {}).get("songs")) or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        name = str(s.get("name") or "").strip()
        if not sid or not name:
            continue
        artists = s.get("artists") or []
        artist = " / ".join(str(a.get("name") or "").strip() for a in artists if a.get("name"))
        album = str((s.get("album") or {}).get("name") or "").strip()
        out.append(_norm_song(sid, name, artist, album, s.get("duration")))
        if len(out) >= limit:
            break
    return out


def _fetch_chart(playlist_id: str, limit: int) -> list[dict]:
    """网易云热歌榜歌单详情（v6 接口字段：ar/al；免登录，id 即歌单 id）。"""
    data = _get_json(_NETEASE_CHART.format(pid=playlist_id, limit=limit))
    out: list[dict] = []
    for t in ((data.get("playlist") or {}).get("tracks")) or []:
        if not isinstance(t, dict):
            continue
        sid = str(t.get("id") or "").strip()
        name = str(t.get("name") or "").strip()
        if not sid or not name:
            continue
        ar = t.get("ar") or t.get("artists") or []
        artist = " / ".join(str(a.get("name") or "").strip() for a in ar if a.get("name"))
        album = str((t.get("al") or t.get("album") or {}).get("name") or "").strip()
        out.append(_norm_song(sid, name, artist, album, t.get("duration")))
        if len(out) >= limit:
            break
    return out


class MusicTool(Tool):
    id = "fun.music"
    label = "听音乐"
    description = (
        "音乐直达：用户说「听 XX」「放 XX」「来首 XX」（如「听周杰伦的七里香」）时传 kw=歌名/歌手。"
        "默认 source=netease 搜网易云、面板自动播第一首；若艺人版权在 QQ 音乐（周杰伦/陈奕迅等"
        "网易云只有翻唱、原版在 QQ），传 source=\"qq\" 让面板内嵌 QQ 音乐搜索页、点第一个即原版。"
        "不给 kw 则给网易云热歌榜（点歌即播）。另附各平台入口。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "kw": {
                        "type": "string",
                        "description": "要听的歌名/歌手/风格关键词（如「七里香」「周杰伦」「白噪音」）；不传则给热歌榜 + 平台入口",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["netease", "qq"],
                        "description": "播放源：netease=网易云（默认，内嵌播放器自动播第一首）；qq=QQ 音乐搜索页（版权在 QQ 的艺人如周杰伦/陈奕迅时用，内嵌 QQ 搜索页点第一个即原版）",
                    },
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        kw = str(params.get("kw") or "").strip()
        source = str(params.get("source") or "netease").strip()
        if source not in ("netease", "qq"):
            source = "netease"
        platforms = [{"id": pid, "name": name, "url": home, "note": note}
                     for pid, name, home, _tpl, note in _PLATFORMS]
        data: dict[str, Any] = {
            "platforms": platforms,
            "keywords": list(_HOT_KEYWORDS),
            "search": [],
            "songs": [],
            "songs_failed": False,
            "chart": [],
            "chart_failed": False,
            "qq_search_url": "",
            "mode": "hot",
            "kw": "",
        }
        if kw:
            q = urllib.parse.quote(kw)
            data["kw"] = kw
            data["search"] = [{"id": pid, "name": name, "url": tpl % q}
                              for pid, name, _home, tpl, _note in _PLATFORMS]
            data["qq_search_url"] = _QQ_SEARCH.format(q=q)
            if source == "qq":
                # 版权在 QQ 的艺人（如周杰伦）：不拉网易云翻唱，面板内嵌 QQ 音乐搜索页点第一个即原版
                data["mode"] = "qq"
            else:
                data["mode"] = "netease"
                try:
                    data["songs"] = _fetch_songs(kw, _MAX_SONGS)
                except Exception:
                    data["songs_failed"] = True  # 搜索挂了不拖垮：平台入口照常给
        else:
            try:
                data["chart"] = _fetch_chart(_HOT_CHART_ID, _MAX_CHART)
            except Exception:
                data["chart_failed"] = True  # 榜单挂了不拖垮：平台入口照常给
        result = ActionResult(success=True, data=data)
        result.panel = "fun:main"
        result.explicit = True  # 对话点名要听 → 直接弹面板
        return result


def make_tools(ctx: Any) -> list[Tool]:
    return [MusicTool()]
