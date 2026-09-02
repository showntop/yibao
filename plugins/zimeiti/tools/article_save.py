"""zimeiti.article_save：稿件进入 content-addressed BlobStore，库只保存稳定 BlobRef。

生成归 agent（LLM 写稿），本 tool 只做确定性的落盘+版本+状态流转（代码 vs Agent）。
保存时按权威口径（_wordcount.py，与桌面 Focus docWordsOf 同步）算好文档总字数/口播字数
落 articles 行并放进返回 data：模型报字数以返回为准、不自估（9-01 报告 P1-08）。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import；_wordcount 经
_load_wordcount 按路径加载的共享 helper，非跨文件 import 业务码）。
数据目录从插件 scoped ctx 的 db.path 推导，不 import config（保持插件可搬运）。
老库相对/绝对路径仍由读取侧兼容；新版本使用 blob://sha256/<hash>，版本治理只删关系，
共享内容由 Host 根据 Work Graph 引用集合延迟 GC。
"""
import importlib.util
import os
import sys
import time
from pathlib import Path

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_KEEP_VERSIONS = 20  # 每选题保留的版本数上限（超出删最旧的行与文件）


def _load_wordcount():
    """按路径加载同目录共享 helper _wordcount.py 并缓存进 sys.modules（全插件同一实例）。

    文件名以 _ 开头 = 插件加载器跳过；兄弟模块非包内模块，普通 import 拿不到，
    只能 spec_from_file_location（仿 coding/agents 的 _sibling 先例）。
    """
    name = "yibao_plugin_zimeiti__wordcount"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("_wordcount.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod  # 先挂再 exec：重复触发加载也拿到同一实例
        spec.loader.exec_module(mod)
    return mod


_wordcount = _load_wordcount()


class ArticleSave(Tool):
    id = "zimeiti.article_save"
    label = "保存稿件"
    description = (
        "把写好的稿件落盘为选题的下一个版本（v1/v2/…），选题状态顺带从「候选」变为「写作中」。"
        "初稿完成或改稿完成后调用；note 记一句本版改了什么。"
        "返回带权威字数（word_count 文档总字数 / narration_count 口播字数），向用户报字数以返回为准、不要自估"
    )
    default_risk = RiskLevel.L2_MEDIUM
    work_outputs = ({
        "kind": "artifact",
        "artifact_type": "video.script",
        "ref_from": "data.id",
        "content_ref_from": "data.content_ref",
        "metadata_fields": ["data.version", "params.note"],
    },)

    def __init__(self, data_dir: str):
        self._plugin_root = Path(data_dir)
        self.refresh = "zimeiti.get"  # 写后详情面板拿刷新数据（加载器会校验它已注册）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "选题 id"},
                    "content": {"type": "string", "description": "稿件全文（markdown，原样落盘）"},
                    "note": {"type": "string", "description": "本版说明（如初稿 / 改了开头）"},
                },
                "required": ["id", "content"],
            },
        }

    def run(self, params: dict, ctx) -> ActionResult:
        tid = str(params.get("id", "")).strip()
        content = params.get("content")
        note = str(params.get("note", "")).strip()
        if not tid or content is None or not str(content).strip():
            return ActionResult(success=False, error="id 和 content 均不能为空")
        # 先查行：既给「选题不存在」明确报错，也保证拼目录名的 id 是库里真实 id（uuid hex，路径安全）
        rows = ctx.db.query("topics", where={"id": tid})
        if not rows:
            return ActionResult(success=False, error=f"选题不存在：{tid}")
        latest = ctx.db.query("articles", where={"topic_id": tid}, order="version DESC", limit=1)
        version = (int(latest[0]["version"]) if latest else 0) + 1
        blobs = getattr(ctx, "blobs", None)
        if blobs is None:
            return ActionResult(success=False, error="底座未提供 blobs capability")
        try:
            # promote 先于 PluginDb commit：崩溃最多留下可 GC 的孤儿 blob，绝不会让已提交
            # content_ref 指向不存在的文件。
            staged = blobs.stage_text(str(content))
            content_ref = staged.finalize()
            path = blobs.resolve(content_ref)
        except OSError as e:
            return ActionResult(success=False, error=f"写文件失败：{e}")
        now = int(time.time())
        # 权威字数（_wordcount 口径）：随版本落库，get 聚合/详情面板/模型报数都读存储值
        word_count = _wordcount.doc_words(str(content))
        narration_count = _wordcount.narration_words(str(content))
        ctx.db.insert(
            "articles",
            {"topic_id": tid, "version": version, "content_path": content_ref, "note": note,
             "word_count": word_count, "narration_count": narration_count, "created_at": now},
        )
        fields = {"updated_at": now}
        if rows[0].get("status") == "候选":  # 有稿即进入写作中；已流转的状态不回退
            fields["status"] = "写作中"
        ctx.db.update("topics", tid, fields)
        self._prune(ctx, tid)
        result = ActionResult(success=True, data={
            "id": tid, "version": version, "path": str(path), "content_ref": content_ref,
            "word_count": word_count, "narration_count": narration_count,
        })
        result.panel = "zimeiti:detail"
        return result

    def _prune(self, ctx, tid: str) -> None:
        """版本治理：保留最近 _KEEP_VERSIONS 版，超出的删行删文件（失败不阻塞保存）。"""
        try:
            rows = ctx.db.query("articles", where={"topic_id": tid}, order="version DESC")
            for row in rows[_KEEP_VERSIONS:]:
                ctx.db.delete("articles", str(row["id"]))
                raw = str(row.get("content_path") or "")
                if raw.startswith("blob://sha256/"):
                    continue
                cp = Path(raw)
                old = cp if cp.is_absolute() else self._plugin_root / cp
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception:
            pass


def make_tools(ctx):
    return [ArticleSave(os.path.dirname(ctx.db.path))]
