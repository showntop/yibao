"""zimeiti.ww_hotspots / zimeiti.ww_score：wewrite CLI 薄封装——热点聚合 + 稿件质量检测。

subprocess 调本机 wewrite（PyPI 包，pipx/pip --user 装在 ~/.local/bin）：hotspots 拉多平台
热榜（百度/头条/微博）返回结构化热点；score 对选题最新稿跑 11 项写作质量检测。两次调用都设
WEWRITE_HOME 到插件数据目录 wewrite/ 子目录（CLI 的缓存/状态不落默认 ~/.wewrite，迁移跟库走）。
CLI 缺失/超时/非零退出/输出非 JSON 都给清晰错误。文件自包含（禁止跨文件 import）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 60          # wewrite 子进程超时（秒）
_MAX_LIMIT = 50        # hotspots 条数上限
_ITEM_FIELDS = ("title", "source", "hot", "hot_normalized", "url", "description")


def _find_cli() -> str:
    """wewrite 可执行文件：PATH 优先，退 ~/.local/bin（pipx/pip --user 常见落点）。"""
    return shutil.which("wewrite") or os.path.expanduser("~/.local/bin/wewrite")


def _wewrite_home(ctx: Any) -> Path:
    """插件数据目录下的 wewrite/ 子目录（不存在则创建），作 WEWRITE_HOME 传给 CLI。"""
    home = Path(os.path.dirname(ctx.db.path)) / "wewrite"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _run_cli(ctx: Any, args: list[str]) -> str:
    """跑 wewrite 子命令返回 stdout；CLI 缺失/启动失败/超时/非零退出 → RuntimeError（友好文案）。"""
    env = {**os.environ, "WEWRITE_HOME": str(_wewrite_home(ctx))}
    try:
        proc = subprocess.run(
            [_find_cli(), *args],
            capture_output=True, text=True, timeout=_TIMEOUT, env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("未找到 wewrite CLI（先 pipx install wewrite 或 pip install wewrite）")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"wewrite 执行超时（>{_TIMEOUT}s）已终止，稍后重试")
    except OSError as e:
        raise RuntimeError(f"wewrite 启动失败：{e}")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"wewrite 执行失败（退出码 {proc.returncode}）：{tail}")
    return proc.stdout


def _parse_json(out: str, what: str) -> dict:
    """解析 CLI 的 stdout JSON；非 JSON → RuntimeError（与 _run_cli 同一错误通道）。"""
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"wewrite {what} 输出解析失败（非 JSON）：{out.strip()[:200]}")


class WwHotspotsTool(Tool):
    id = "zimeiti.ww_hotspots"
    label = "查 wewrite 热点"
    description = (
        "用 wewrite CLI 拉取多平台热榜（百度/头条/微博），返回结构化热点"
        "（title/source/hot/hot_normalized/url/description）与抓取失败的源列表。"
        "用户说「今天写什么」「有什么热点能写」「看看微博/头条热榜」时用它"
        "（选题推荐的完整流程见 skills/topics 技能）；挑中后用 zimeiti.add 转选题。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "热点条数（默认 20，上限 50）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        if getattr(ctx, "db", None) is None:
            return ActionResult(success=False, error="底座未提供数据库")
        try:
            limit = int(params.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, _MAX_LIMIT))
        try:
            data = _parse_json(_run_cli(ctx, ["hotspots", "--limit", str(limit)]), "hotspots")
        except RuntimeError as e:
            return ActionResult(success=False, error=str(e))
        items = [{k: it.get(k) for k in _ITEM_FIELDS} for it in (data.get("items") or [])]
        return ActionResult(success=True, data={
            "items": items,
            "sources_failed": data.get("sources_failed") or [],
        })


class WwScoreTool(Tool):
    id = "zimeiti.ww_score"
    label = "稿件质量检测"
    description = (
        "对选题的最新稿跑 wewrite score：返回质量分/综合分与 11 项检测结果"
        "（tier1 六项节奏/词汇/副词 + tier2 五项违禁词/句完整性/信源/语域/插入控制）。"
        "没有稿件时报错引导先写稿。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "topic_id": {"type": "string", "description": "选题 id"},
                },
                "required": ["topic_id"],
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("topic_id") or "").strip()
        if not tid:
            return ActionResult(success=False, error="没给选题 id")
        if not db.query("topics", where={"id": tid}):
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        latest = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        if not latest:
            return ActionResult(success=False, error="还没有稿件：先写初稿再检测")
        try:
            raw = str(latest[0]["content_path"])
            if raw.startswith("blob://sha256/"):
                if getattr(ctx, "blobs", None) is None:
                    return ActionResult(success=False, error="底座未提供 blobs capability")
                path = ctx.blobs.resolve(raw, require_exists=False)
            else:
                cp = Path(raw)
                path = cp if cp.is_absolute() else Path(os.path.dirname(db.path)) / cp
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            return ActionResult(success=False, error=f"读稿失败：{e}")
        version = int(latest[0]["version"])
        # 稿内容落插件数据目录临时文件给 CLI 读（wewrite score 只收文件路径），跑完即删
        tmp = _wewrite_home(ctx) / f"score_{tid}_v{version}.md"
        try:
            tmp.write_text(content, encoding="utf-8")
            data = _parse_json(_run_cli(ctx, ["score", str(tmp), "--json"]), "score")
        except (RuntimeError, OSError) as e:
            return ActionResult(success=False, error=str(e))
        finally:
            tmp.unlink(missing_ok=True)
        checks = []
        for tier in ("tier1", "tier2"):  # tier1 六项 + tier2 五项 = 11 项检测（_summary 汇总项跳过）
            for name, item in (data.get(tier) or {}).items():
                if name.startswith("_"):
                    continue
                checks.append({
                    "tier": tier,
                    "name": name,
                    "score": item.get("score"),
                    "detail": item.get("detail"),
                })
        t3 = data.get("tier3") or {}
        return ActionResult(success=True, data={
            "topic_id": tid,
            "version": version,
            "quality_score": data.get("quality_score"),
            "composite_score": data.get("composite_score"),
            "char_count": data.get("char_count"),
            "tier3_score": t3.get("score"),
            "checks": checks,
        })


def make_tools(ctx: Any) -> list[Tool]:
    return [WwHotspotsTool(), WwScoreTool()]
