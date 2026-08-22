"""fun.quote：每日一言——hitokoto 开放接口，随机文学/动漫/影视等短句。

公开免登录端点（v1.hitokoto.cn），按分类串行拉 N 条，逐条失败跳过（分类临时没内容
不算错）；全部失败才报错。面板「换一句」直调刷新。文件自包含。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

_FETCH_TIMEOUT = 8
_MAX_COUNT = 10
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# 一言分类 → 中文名
_CATS = {
    "a": "动画", "b": "漫画", "c": "游戏", "d": "文学", "e": "原创",
    "f": "网络", "g": "其他", "h": "影视", "i": "诗词", "j": "网易云",
    "k": "哲学", "l": "抖机灵",
}


def _fetch_one(url: str) -> dict | None:
    """GET 一条一言（module-level，测试 monkeypatch 它，不真发网络）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        data = json.loads(resp.read().decode(charset, errors="replace"))
    if not isinstance(data, dict):
        return None
    text = str(data.get("hitokoto") or "").strip()
    if not text:
        return None
    return {"text": text, "from": str(data.get("from") or "").strip()}


class QuoteSkill(Skill):
    id = "fun.quote"
    label = "来一句"
    description = (
        "每日一言：随机返回文学/动漫/影视/诗词等短句（hitokoto）。用户说「来一句」「说个名句」"
        "「文艺一下」时用它；指定分类传 cat（如 d=文学、h=影视、k=哲学、l=抖机灵）。"
    )
    default_risk = RiskLevel.L1_LOW

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "cat": {
                        "type": "string",
                        "enum": list(_CATS),
                        "description": "分类（不传=全类型随机）",
                    },
                    "count": {"type": "integer", "description": "拉几条（默认 3，上限 10）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        cat = str(params.get("cat") or "").strip()
        if cat and cat not in _CATS:
            return ActionResult(
                success=False,
                error=f"不认识的分类：{cat}（可选 {'/'.join(_CATS)}）",
            )
        try:
            count = int(params.get("count") or 3)
        except (TypeError, ValueError):
            count = 3
        count = max(1, min(count, _MAX_COUNT))

        url = "https://v1.hitokoto.cn/"
        if cat:
            url += f"?c={cat}"
        rows: list[dict] = []
        failed = 0
        for _ in range(count):
            try:
                one = _fetch_one(url)
            except Exception:
                one = None
            if one and one not in rows:
                rows.append(one)
            else:
                failed += 1
        if not rows:
            return ActionResult(
                success=False,
                error="一言拉取失败：网络不通或接口改版，稍后重试",
            )
        result = ActionResult(success=True, data={
            "rows": rows,
            "cat": cat or "",
            "cat_cn": _CATS.get(cat, "随机"),
            "failed": failed,
        })
        result.panel = "fun:main"
        return result


def make_tools(ctx: Any) -> list[Skill]:
    return [QuoteSkill()]
