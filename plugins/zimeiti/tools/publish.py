"""zimeiti.publish：发布最新稿——标题+正文复制到剪贴板，选题标为「已发布」并记发布时间。

服务对话/详情面板场景（用户说「发布这篇 / 复制成稿」）；编辑器 webview 前端的复制链路
（公众号富文本/小红书纯文本）不变，各管各的场景。剪贴板走 macOS pbcopy（桌面应用平台固定
macOS）；open_platform=true 顺手用默认浏览器打开平台后台。文件自包含（禁止跨文件 import）。
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel


def _strip_md(text: str) -> str:
    """剥 markdown 符号成纯文本（标题/粗斜体/行内代码去掉记号；列表符号换小点；
    链接留「文字（地址）」；与编辑器「小红书纯文本」复制同一语义）。"""
    out = []
    for line in text.splitlines():
        line = re.sub(r"^#{1,6}\s*", "", line)                       # 标题
        line = re.sub(r"^(\s*)[-*+]\s+", r"\1· ", line)              # 列表
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)        # 图片留 alt
        line = re.sub(r"\[([^\]]+)\]\(([^)]*)\)", r"\1（\2）", line)  # 链接留文字+地址
        line = line.replace("**", "").replace("__", "").replace("`", "")
        out.append(line)
    return "\n".join(out)
from yibao_brain.tools import Tool

# 平台 → 后台首页（open_platform 用）；子串包含匹配（platform 是自由文本，如「公众号+小红书」）
_PLATFORM_URLS = (
    ("公众号", "https://mp.weixin.qq.com/"),
    ("小红书", "https://creator.xiaohongshu.com/"),
    ("知乎", "https://www.zhihu.com/"),
    ("B站", "https://member.bilibili.com/"),
    ("视频号", "https://channels.weixin.qq.com/"),
)


class PublishTool(Tool):
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
            cp = Path(str(latest[0]["content_path"]))
            # content_path 2026-08-25 起落相对路径（相对插件数据根）；老库绝对路径兼容
            content = (cp if cp.is_absolute() else Path(os.path.dirname(ctx.db.path)) / cp).read_text(encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"读稿失败：{e}")
        title = str(topic.get("title") or "").strip()
        version = int(latest[0]["version"])
        # 剪贴板给纯文本（与编辑器「小红书纯文本」同一语义）：剥 markdown 符号，
        # 不再把 `# 标题`、`**粗体**` 原样塞给用户（详情页 publish 与编辑器富文本
        # 曾是两条输出不一致的链路；公众号富文本复制仍在编辑器内做）
        plain = _strip_md(content)
        payload = f"{title}\n\n{plain}" if title else plain
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
        db.update("topics", tid, {"status": "已发布", "published_at": now, "updated_at": now,
                                  "published_version": version})  # 发布的是哪版稿：复盘对齐数据与稿
        return ActionResult(
            success=True,
            data={
                "id": tid,
                "title": title,
                "version": version,
                "chars": len(payload),
                "published_at": now,
                "opened_url": opened,
            },
        )


def make_tools(ctx: Any) -> list[Tool]:
    return [PublishTool()]
