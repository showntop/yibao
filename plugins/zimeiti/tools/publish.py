"""zimeiti.publish：发布最新稿——标题+正文复制到剪贴板，选题标为「已发布」并记发布时间。

服务对话/详情面板场景（用户说「发布这篇 / 复制成稿」）；编辑器 webview 前端的复制链路
（公众号富文本/小红书纯文本）不变，各管各的场景。剪贴板走 macOS pbcopy（桌面应用平台固定
macOS）；open_platform=true 顺手用默认浏览器打开平台后台。文件自包含（禁止跨文件 import）。
"""
from __future__ import annotations

import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.skills import Skill

# 平台 → 后台首页（open_platform 用）；子串包含匹配（platform 是自由文本，如「公众号+小红书」）
_PLATFORM_URLS = (
    ("公众号", "https://mp.weixin.qq.com/"),
    ("小红书", "https://creator.xiaohongshu.com/"),
    ("知乎", "https://www.zhihu.com/"),
    ("B站", "https://member.bilibili.com/"),
    ("视频号", "https://channels.weixin.qq.com/"),
)


class PublishSkill(Skill):
    id = "zimeiti.publish"
    label = "发布内容"
    description = (
        "发布选题的最新稿：标题+正文复制到剪贴板（用户去平台后台粘贴即可），选题标为「已发布」"
        "并记发布时间；open_platform=true 时顺手打开平台后台。没有稿件时报错引导先写稿。"
    )
    default_risk = RiskLevel.L2_MEDIUM

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id"},
                    "open_platform": {
                        "type": "boolean",
                        "description": "顺手用浏览器打开平台后台（默认 false）",
                    },
                },
                "required": ["id"],
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        if not tid:
            return ActionResult(success=False, error="没给选题 id")
        rows = db.query("topics", where={"id": tid})
        if not rows:
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        topic = rows[0]
        latest = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        if not latest:
            return ActionResult(success=False, error="还没有稿件：先写初稿再发布")
        try:
            content = Path(latest[0]["content_path"]).read_text(encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"读稿失败：{e}")
        title = str(topic.get("title") or "").strip()
        payload = f"{title}\n\n{content}" if title else content
        try:
            subprocess.run(["pbcopy"], input=payload.encode("utf-8"), check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            return ActionResult(success=False, error=f"复制剪贴板失败：{e}")
        # 顺手开平台后台：打不开不致命（稿已复制、状态照走），回执里带没带上 url 而已
        opened = ""
        if params.get("open_platform"):
            platform = str(topic.get("platform") or "")
            url = next((u for k, u in _PLATFORM_URLS if k in platform), "")
            if url:
                try:
                    webbrowser.open(url)
                    opened = url
                except Exception:
                    pass
        now = int(time.time())
        db.update("topics", tid, {"status": "已发布", "published_at": now, "updated_at": now})
        return ActionResult(
            success=True,
            data={
                "id": tid,
                "title": title,
                "version": int(latest[0]["version"]),
                "chars": len(payload),
                "published_at": now,
                "opened_url": opened,
            },
        )


def make_tools(ctx: Any) -> list[Skill]:
    return [PublishSkill()]
