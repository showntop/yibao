"""fun.music：音乐直达——平台快捷入口 + 搜索链接生成。

版权正路：不拉第三方热榜/不内嵌播放，返回各平台的入口与「歌名 → 平台搜索页」链接，
由面板经 native:open_url 用系统浏览器打开（网页版可即点即听，无需登录下载）。
纯本地生成，无网络请求（L0）。文件自包含。
"""
from __future__ import annotations

import urllib.parse
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

# 平台 → (中文名, 首页, 搜索模板, 备注)。搜索词经 quote() 编码后填 %s
_PLATFORMS: list[tuple[str, str, str, str]] = [
    ("netease", "网易云音乐", "https://music.163.com/", "https://music.163.com/#/search/m/?s=%s", "网页版即点即听"),
    ("qq", "QQ音乐", "https://y.qq.com/", "https://y.qq.com/n/ryqq/search?w=%s", "网页版即点即听"),
    ("kugou", "酷狗音乐", "https://www.kugou.com/", "https://www.kugou.com/yy/html/search.html#searchType=song&searchKeyWord=%s", "海量翻唱"),
    ("douyin", "汽水音乐", "https://music.douyin.com/", "https://music.douyin.com/?s=%s", "抖音热歌"),
]

# 摸鱼热词（快捷按钮，省得打字；都是公开的日常娱乐向搜索词）
_HOT_KEYWORDS = ("周杰伦", "Taylor Swift", "林俊杰", "古典 纯音乐", "白噪音 助眠", "深夜电台")


class MusicSkill(Skill):
    id = "fun.music"
    label = "听音乐"
    description = (
        "音乐直达：返回网易云/QQ音乐/酷狗/汽水音乐入口；给了歌名/歌手则生成各平台搜索页链接，"
        "点开即听（用系统浏览器打开，网页版免登录）。用户说「听歌」「推荐首歌」「放个音乐」时用它。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "kw": {
                        "type": "string",
                        "description": "要听的歌名/歌手/风格关键词（如「周杰伦」「白噪音」）；不传则只给平台入口",
                    },
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        kw = str(params.get("kw") or "").strip()
        platforms = [{"id": pid, "name": name, "url": home, "note": note}
                     for pid, name, home, _tpl, note in _PLATFORMS]
        data: dict[str, Any] = {
            "platforms": platforms,
            "keywords": list(_HOT_KEYWORDS),
            "search": [],
            "kw": "",
        }
        if kw:
            q = urllib.parse.quote(kw)
            data["kw"] = kw
            data["search"] = [{"id": pid, "name": name, "url": tpl % q}
                              for pid, name, _home, tpl, _note in _PLATFORMS]
        result = ActionResult(success=True, data=data)
        result.panel = "fun:main"
        return result


def make_tools(ctx: Any) -> list[Skill]:
    return [MusicSkill()]
