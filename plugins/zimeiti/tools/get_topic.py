"""zimeiti.get：查选题——声明式 get 的代码承接（2026-08-25 v2 #14），按 id 查时拼聚合字段。

聚合（详情页用）：draft = 最新稿版本+字数（「v3 · 2100 字」/「还没写稿」）、
materials = 关联素材数（「2 条」/「—」）。不带 id 的全量查询不拼聚合（避免逐条读稿）。
2026-08-31 视频 workflow S0：hkrr/cover_concepts（JSON 文本）拆平成展示行，
project_id 反解项目名给「已立项 → 项目名」反馈；解析一律防御降级，不炸。
2026-09-02 P1-08 字数口径统一：字数读 article_save 落库的 word_count/narration_count
（权威口径见 _wordcount.py），并分列 draft_words/draft_narration 供详情分别显示；
存量稿（存储值 0）读到时按同一口径懒计算回写补齐，不写迁移脚本扫盘。
文件自包含（禁止跨文件 import；_wordcount 经 _load_wordcount 按路径加载的共享 helper）；
数据目录从 ctx.db.path 推导（同 article_save 姿势）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool


def _load_wordcount():
    """按路径加载同目录共享 helper _wordcount.py（同 article_save 姿势，全插件同一实例）。"""
    name = "yibao_plugin_zimeiti__wordcount"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("_wordcount.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # 先挂再 exec：重复触发加载也拿到同一实例
        spec.loader.exec_module(mod)
    return mod


_wordcount = _load_wordcount()


class GetTopicTool(Tool):
    id = "zimeiti.get"
    label = "查看选题"
    description = (
        "查看选题详情：传 id 查单条（带稿件版本/字数/关联素材数聚合字段）；"
        "也接受 where 条件字典做等值过滤，不传则列全部。"
    )
    default_risk = RiskLevel.L0_READONLY

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id（传了按 id 查单条）"},
                    "where": {"type": "object", "description": "等值过滤条件（如 {\"status\": \"候选\"}）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        tid = str(params.get("id") or "").strip()
        where = params.get("where") if isinstance(params.get("where"), dict) else None
        if tid:
            rows = db.query("topics", where={"id": tid})
        elif where:
            rows = db.query("topics", where=where)
        else:
            rows = db.query("topics")
        if tid and rows:
            self._enrich(ctx, rows[0], tid)
        result = ActionResult(success=True, data={"rows": rows})
        result.panel = "zimeiti:detail"
        return result

    def _enrich(self, ctx, row: dict, tid: str) -> None:
        """单条详情聚合：最新稿版本+权威字数（存储值；存量懒补）、关联素材数。失败降级为占位文案。"""
        db = ctx.db
        latest = db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        if latest:
            version = int(latest[0]["version"])
            words = self._article_words(ctx, db, latest[0])
            row["draft"] = f"v{version} · {words[0]} 字"
            row["draft_words"], row["draft_narration"] = words
        else:
            row["draft"] = "还没写稿"
            row["draft_words"] = row["draft_narration"] = ""
        mats = db.query("materials", where={"topic_id": tid})
        row["materials"] = f"{len(mats)} 条" if mats else "—"
        self._enrich_s0(db, row)

    def _article_words(self, ctx, db, article: dict) -> tuple[int, int]:
        """最新稿 (文档总字数, 口播字数)：读 article_save 落库的存储值；
        存量行（word_count=0）按同一权威口径懒计算并回写补齐；读不到文件降级 0。"""
        words = int(article.get("word_count") or 0)
        narration = int(article.get("narration_count") or 0)
        if words > 0:
            return words, narration
        try:
            raw = str(article.get("content_path") or "")
            if raw.startswith("blob://sha256/") and getattr(ctx, "blobs", None) is not None:
                p = ctx.blobs.resolve(raw, require_exists=False)
            else:
                cp = Path(raw)
                p = cp if cp.is_absolute() else Path(os.path.dirname(db.path)) / cp
            content = p.read_text(encoding="utf-8")
        except OSError:
            return 0, 0
        words = _wordcount.doc_words(content)
        narration = _wordcount.narration_words(content)
        try:  # 回写补齐（下次直接读存储值）；写失败不阻塞读
            db.update("articles", str(article["id"]),
                      {"word_count": words, "narration_count": narration})
        except Exception:
            pass
        return words, narration

    def _enrich_s0(self, db, row: dict) -> None:
        """视频 workflow S0 展示派生：hkrr 四项/封面概念拆平成行，project_id 反解项目名。

        hkrr/cover_concepts 是 JSON 文本列：坏 JSON/类型不对一律降级空串（老选题天然全空）。
        """
        hkrr = _load_json(row.get("hkrr"))
        if not isinstance(hkrr, dict):
            hkrr = {}
        for key in ("happy", "knowledge", "resonance", "rhythm"):
            row[f"hkrr_{key}"] = str(hkrr.get(key) or "")
        covers = _load_json(row.get("cover_concepts"))
        if not isinstance(covers, list):
            covers = []
        for i in range(3):
            row[f"cover_{i + 1}"] = str(covers[i]) if i < len(covers) else ""
        row["project"] = _project_label(db, str(row.get("project_id") or ""))


def _load_json(raw) -> Any:
    """JSON 文本解析：空串/坏 JSON → None（调用方按类型降级，不炸）。"""
    try:
        return json.loads(str(raw or ""))
    except ValueError:
        return None


def _project_label(db, pid: str) -> str:
    """立项状态文案：已立项 → 项目名（projects.json 只读反解；读不到保底显示 id）。"""
    if not pid:
        return ""
    # 插件库在 <data_dir>/plugins/zimeiti/data.db，项目注册表在 <data_dir>/projects.json
    try:
        raw = json.loads((Path(db.path).parents[2] / "projects.json").read_text(encoding="utf-8"))
        for p in raw.get("projects") or []:
            if p.get("id") == pid:
                return f"已立项 → {p.get('name') or pid}"
    except (OSError, ValueError, AttributeError):
        pass
    return f"已立项 → {pid}"


def make_tools(ctx: Any) -> list[Tool]:
    return [GetTopicTool()]
