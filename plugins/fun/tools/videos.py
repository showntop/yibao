"""fun.videos：B站视频雷达——热门榜（免登录公开接口）+ 关键词精准搜索（内嵌 B站官方搜索页）。

热门榜：主源全站热门（api.bilibili.com popular），备源排行榜（ranking/v2）——单源挂了自动切换，
两个源都失败才报错，返回卡片（标题/UP主/播放/时长/分区）供面板内嵌播放器直接看。
关键词搜索：B站搜索 API 已被风控（v_voucher 拦截，匿名不可用），正路是返回官方搜索页
URL（search.bilibili.com/all?keyword=...），面板 iframe 内嵌官方搜索页——用户点结果即在
B站视频页内直接播放（B站搜索页/视频页无 X-Frame-Options 可嵌入）。精准、免 API、版权正路。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）。
"""
from __future__ import annotations

import html
import json
import urllib.parse
import urllib.request
from typing import Any, Callable

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_FETCH_TIMEOUT = 10
_MAX_LIMIT = 20
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 分区 tid → 中文名（0=全站）。B站分区代码，面板下拉用
_REGIONS = {
    "0": "全站",
    "1": "动画",
    "3": "音乐",
    "4": "游戏",
    "5": "影视",
    "13": "番剧",
    "36": "科技",
    "119": "鬼畜",
    "129": "舞蹈",
    "155": "娱乐",
    "160": "生活",
    "188": "知识",
}


def _fetch_json(url: str) -> dict:
    """GET 一个 JSON 端点（module-level，测试 monkeypatch 它，不真发网络）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def _fmt_count(raw: Any) -> str:
    """播放/点赞数归一化：数字转「x万」「x亿」中文读法；空则空串。
    万档四舍五入进位到整亿时直接显示「N亿」（如 99999999 → 1亿），避免「10000万」。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        n = float(s)
    except ValueError:
        return s
    if n >= 100000000:
        return _trim(n / 100000000) + "亿"
    if n >= 10000:
        v = n / 10000
        if v >= 9999.95:
            return _trim(n / 100000000) + "亿"
        return _trim(v) + "万"
    return str(int(n))


def _trim(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _fmt_duration(sec: Any) -> str:
    """秒 → mm:ss 或 h:mm:ss；非法/空给空串。"""
    try:
        n = int(sec)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    h, rem = divmod(n, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _pick_video(item: dict) -> dict | None:
    """把 B站两种接口的单条视频归一化；缺 bvid/标题的丢弃。"""
    bvid = str(item.get("bvid") or "").strip()
    title = str(item.get("title") or "").strip()
    if not bvid or not title:
        return None
    owner = item.get("owner") or {}
    stat = item.get("stat") or {}
    # 封面：接口给 http 直链，CDN 支持 https（面板 CSP 只放行 https 图片），统一转一下
    pic = str(item.get("pic") or "").strip()
    if pic.startswith("http://"):
        pic = "https://" + pic[len("http://"):]
    return {
        "title": html.unescape(title),
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "pic": pic,
        "author": str(owner.get("name") or "").strip(),
        "views": _fmt_count(stat.get("view")),
        "likes": _fmt_count(stat.get("like")),
        "duration": _fmt_duration(item.get("duration")),
        "region": str(item.get("tname") or "").strip(),
    }


def _parse_popular(data: dict, limit: int) -> list[dict]:
    """全站热门 popular 接口：data.list[]。code!=0 或结构不符 → 空列表（触发备源）。"""
    if data.get("code") != 0:
        return []
    out: list[dict] = []
    for item in data.get("data", {}).get("list") or []:
        v = _pick_video(item)
        if v:
            out.append(v)
        if len(out) >= limit:
            break
    return out


def _parse_ranking(data: dict, limit: int) -> list[dict]:
    """排行榜 ranking/v2 接口：data.list[]。"""
    if data.get("code") != 0:
        return []
    out: list[dict] = []
    for item in data.get("data", {}).get("list") or []:
        v = _pick_video(item)
        if v:
            out.append(v)
        if len(out) >= limit:
            break
    return out


class VideosSkill(Skill):
    id = "fun.videos"
    label = "看视频"
    description = (
        "拉取 B站视频：给了 keyword 则按关键词在面板内嵌 B站官方搜索页精准找视频（点结果即播）；"
        "没给 keyword 拉 B站热门（全站/分区）卡片列表（面板内嵌播放器直接看）。"
        "用户说「看看有什么好看的视频」「B站热门」「推荐个视频」时用热门；"
        "「想看 XX」「找 XX 的视频」「XX 的电影解说」时传 keyword；想看某分区传 tid"
        "（如 5=影视、160=生活、188=知识）。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "精准搜索关键词（如「牛来 电影解说」「猫meme」「美食探店」）；不传则拉热门榜",
                    },
                    "tid": {
                        "type": "string",
                        "enum": list(_REGIONS),
                        "description": "热门榜分区 id（0=全站/1=动画/3=音乐/4=游戏/5=影视/13=番剧/36=科技/119=鬼畜/129=舞蹈/155=娱乐/160=生活/188=知识）",
                    },
                    "limit": {"type": "integer", "description": "热门榜返回条数（默认 10，上限 20）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        keyword = str(params.get("keyword") or "").strip()
        if keyword:
            # 精准搜索：B站官方搜索页内嵌（免 API、免登录、无风控），面板 iframe 加载
            q = urllib.parse.quote(keyword)
            result = ActionResult(success=True, data={
                "mode": "search",
                "keyword": keyword,
                "search_url": f"https://search.bilibili.com/all?keyword={q}",
                "rows": [],
                "region": "搜索",
                "tid": "",
                "failed": [],
            })
            result.panel = "fun:main"
            result.explicit = True  # 对话点名要看 → 直接弹面板
            return result

        try:
            limit = int(params.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, _MAX_LIMIT))
        tid = str(params.get("tid") or "0").strip()
        if tid not in _REGIONS:
            return ActionResult(
                success=False,
                error=f"不认识的分区：{tid}（可选 {'/'.join(_REGIONS)}）",
            )

        rows: list[dict] = []
        errors: list[str] = []
        # 主源：全站热门（分区热门接口无公开版，分区时直接用排行榜备源）
        sources: list[tuple[str, str, Callable[[dict, int], list[dict]]]] = []
        if tid == "0":
            sources.append(("全站热门", "https://api.bilibili.com/x/web-interface/popular?ps=50", _parse_popular))
        sources.append(("排行榜", f"https://api.bilibili.com/x/web-interface/ranking/v2?rid={tid}&type=all", _parse_ranking))
        for name, url, parser in sources:
            if rows:
                break
            try:
                rows = parser(_fetch_json(url), limit)
            except Exception:
                rows = []
            if not rows:
                errors.append(name)
        if not rows:
            return ActionResult(
                success=False,
                error=f"视频拉取失败（{'、'.join(errors) or '未知原因'}）：网络不通或 B站改版，稍后重试",
            )

        result = ActionResult(success=True, data={
            "rows": rows,
            "mode": "hot",
            "region": _REGIONS[tid],
            "tid": tid,
            "failed": errors,
        })
        result.panel = "fun:main"
        result.explicit = True  # 对话点名要看 → 直接弹面板
        return result


def make_tools(ctx: Any) -> list[Skill]:
    return [VideosSkill()]
