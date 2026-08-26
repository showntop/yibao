"""zimeiti.night_brief：守夜人夜间内容流水线——抓热点 → 定选题 → 起稿 → 生成晨报文本。

写死的 pipeline（不要 agentic 自由串），每步完成即落盘到 night/<YYYY-MM-DD>.json，
重跑同日期跳过已完成步（断点续跑：半夜进程被杀，补跑不重复抓热点/不重复转选题）。
铁律：永不自动发布——只到「初稿 + 晨报」，发布永远等用户拍板。
文件自包含（加载器按文件独立 importlib 加载，禁止跨文件 import）：
wewrite CLI helper 与 wewrite.py 保持同一份抄本。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date as _date
from pathlib import Path
from typing import Any

from yibao_brain.ipc import ActionResult, RiskLevel
from yibao_brain.tools import Tool

_TIMEOUT = 60          # wewrite 子进程超时（秒）
_ITEM_FIELDS = ("title", "source", "hot", "hot_normalized", "url", "description")
_MAX_PICKS = 3         # 每晚最多入选选题数（宁缺毋滥）
_MIN_DRAFT_CJK = 600   # 初稿硬闸门：正文中文字数下限
_CJK = re.compile(r"[一-鿿]")


# ---------- wewrite CLI helper（抄自 wewrite.py，自包含约定） ----------

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


# ---------- 断点续跑状态（每步完成即落盘） ----------

def _state_path(root: Path, day: str) -> Path:
    return root / "night" / f"{day}.json"


def _load_state(root: Path, day: str) -> dict:
    try:
        raw = json.loads(_state_path(root, day).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("steps"), dict):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return {"date": day, "steps": {}}


def _save_state(root: Path, state: dict) -> None:
    """原子写（tmp+rename）：半夜被杀也不能留半个 JSON，否则下次断点续跑读到烂状态。"""
    path = _state_path(root, str(state["date"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# ---------- LLM 输出解析 ----------

def _parse_picks(raw: str) -> list[dict]:
    """解析选题 JSON 数组：```json 围栏或裸数组都接；结构不对（非 dict 列表）抛 ValueError。"""
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    else:
        i, j = s.find("["), s.rfind("]")
        if 0 <= i < j:
            s = s[i:j + 1]
    arr = json.loads(s)  # JSONDecodeError 是 ValueError 子类，调用方统一捕获
    if not isinstance(arr, list) or any(not isinstance(it, dict) for it in arr):
        raise ValueError("不是选题对象数组")
    return arr


def _pick_prompt(items: list[dict], board: list[dict]) -> str:
    hot_lines = "\n".join(
        f"{i}. {it.get('title') or ''}（{it.get('source') or '?'}，热度 {it.get('hot') or '?'}）{it.get('url') or ''}"
        for i, it in enumerate(items, 1))
    board_lines = "\n".join(
        f"- {row.get('title') or ''}（{row.get('status') or ''}）" for row in board) or "（空）"
    # 方法论要点吸收自 skills/topics/SKILL.md：每个选题必须有「为什么现在写」；宁缺毋滥
    return f"""你是自媒体选题助手。下面是今夜热榜和选题看板现有选题。

热榜：
{hot_lines}

看板现有选题（不要重复这些题材）：
{board_lines}

要求：
- 给出最多 {_MAX_PICKS} 个选题建议，每个都必须有「为什么现在写」的理由（挂住哪条热点）；没理由的热点不提
- 目标平台限：公众号 / 知乎 / 小红书，挑最适配的一个
- 避开与看板已有选题重复的题材
- 宁缺毋滥：2 个准的胜过 5 个凑的；没有值得跟的就给空数组

严格输出 JSON 数组（不要任何额外文字），每项形如：
{{"title": "选题标题", "angle": "切入角度", "platform": "公众号", "reason": "为什么现在写", "url": "热点原文链接"}}"""


def _draft_prompt(pick: dict) -> str:
    # 成文框架要点吸收自 skills/write/SKILL.md：钩子 3 行内 / 五段式 / 口语短句 / 克制
    return f"""你是自媒体写手。为下面这个选题写一篇初稿（markdown）。

选题：{pick.get('title') or ''}
切入角度：{pick.get('angle') or ''}
目标平台：{pick.get('platform') or '公众号'}
背景（为什么现在写）：{pick.get('reason') or ''}（来源：{pick.get('url') or ''}）

成文要求：
- 开头 3 行内给出钩子（一个具体场景 / 一个反常识判断 / 一个尖锐问题），不要从定义和背景开始
- 五段式：钩子 → 痛点共鸣（用「你」而不是「大家」）→ 核心内容（3 个以内要点，要点 = 观点 + 一个具体例子）→ 转折或深化 → 收尾（一个可带走的结论，不要总结全文）
- 口语优先、短句，一段不超过 4 行；不堆形容词，不用「非常」「极其」，惊叹号全文不超过 2 个
- 具体：数字、名字、场景优于抽象判断
- 正文中文字数不少于 800 字

直接输出稿件全文，不要任何解释。"""


def _cjk_count(text: str) -> int:
    return len(_CJK.findall(text))


class NightBriefTool(Tool):
    id = "zimeiti.night_brief"
    label = "夜间流水线"
    description = (
        "跑一次夜间内容流水线：抓热点 → 定选题（落入看板）→ 给头名选题起初稿 → 生成晨报文本。"
        "用户说「现在跑一次夜间流水线」「今晚的选题跑一下」时用；布置每天定时跑用 night_set。"
        "只到初稿为止，永不自动发布。"
    )
    default_risk = RiskLevel.L1_LOW  # 可被 night_set 调度（夜间无人值守上限 L1）

    def __init__(self, data_dir: str):
        self._root = Path(data_dir)  # 插件数据根（db.path 的目录；稿件/状态都落这里）

    def openai_schema(self) -> dict:
        return {
            "name": self.id,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string",
                             "description": "流水线归属日期（YYYY-MM-DD，缺省今天；补跑/测试用）"},
                },
            },
        }

    def run(self, params: dict, ctx: Any) -> ActionResult:
        db = getattr(ctx, "db", None)
        llm = getattr(ctx, "llm", None)
        if db is None:
            return ActionResult(success=False, error="底座未提供数据库")
        if llm is None:
            return ActionResult(success=False, error="底座未提供 LLM")
        day = str((params or {}).get("date") or "").strip() or _date.today().isoformat()
        try:
            _date.fromisoformat(day)
        except ValueError:
            return ActionResult(success=False, error=f"日期格式不对：{day}（要 YYYY-MM-DD）")

        state = _load_state(self._root, day)
        steps: dict = state["steps"]

        # 1. 抓热点：失败整 run 报错（没热点不硬编选题）
        if "hotspots" not in steps:
            try:
                data = _parse_json(_run_cli(ctx, ["hotspots", "--limit", "20"]), "hotspots")
            except RuntimeError as e:
                return ActionResult(success=False, error=f"夜间流水线失败（抓热点）：{e}")
            items = [{k: it.get(k) for k in _ITEM_FIELDS} for it in (data.get("items") or [])]
            if not items:
                return ActionResult(success=False, error="夜间流水线失败（抓热点）：一条热点都没抓到")
            steps["hotspots"] = {"items": items}
            _save_state(self._root, state)
        items = steps["hotspots"]["items"]

        # 2. 定选题（LLM）：解析失败/结构不对重试一次，再失败整步报错
        if "pick" not in steps:
            board = db.query("topics", order="updated_at DESC", limit=100)
            prompt = _pick_prompt(items, board)
            picks: list[dict] | None = None
            for attempt in range(2):
                try:
                    picks = _parse_picks(llm.chat(prompt))
                    break
                except (ValueError, RuntimeError) as e:
                    last_err = str(e)
            if picks is None:
                return ActionResult(success=False,
                                    error=f"夜间流水线失败（定选题）：LLM 输出两次都解析不了（{last_err}）")
            # 硬闸门：标题非空、与看板及本批互不重复、最多 _MAX_PICKS 个
            existing = {str(row.get("title") or "").strip() for row in board}
            seen: set[str] = set()
            kept: list[dict] = []
            for p in picks:
                title = str(p.get("title") or "").strip()
                if not title or title in existing or title in seen:
                    continue
                seen.add(title)
                kept.append({"title": title,
                             "angle": str(p.get("angle") or ""),
                             "platform": str(p.get("platform") or "公众号"),
                             "reason": str(p.get("reason") or ""),
                             "url": str(p.get("url") or "")})
                if len(kept) >= _MAX_PICKS:
                    break
            steps["pick"] = {"topics": kept}
            _save_state(self._root, state)
        picked = steps["pick"]["topics"]

        # 3. 转选题：落 topics 表（source=守夜人，可回溯是夜里的活）
        if "convert" not in steps:
            ids: list[str] = []
            now = int(time.time())
            for p in picked:
                ids.append(db.insert("topics", {
                    "title": p["title"], "angle": p["angle"], "platform": p["platform"],
                    "source": "守夜人", "url": p["url"], "status": "候选",
                    "created_at": now, "updated_at": now,
                }))
            steps["convert"] = {"ids": ids}
            _save_state(self._root, state)
        ids = steps["convert"]["ids"]

        # 4. 起稿：只给排名第 1 的选题（控制夜间成本）；字数不够重试一次，
        #    再不行该选题标「起稿失败」继续（不整 run 失败）
        if "draft" not in steps:
            if not picked:
                steps["draft"] = {"skipped": "没有入选选题"}
            else:
                top, tid = picked[0], ids[0]
                draft_text = None
                for _attempt in range(2):
                    text = llm.chat(_draft_prompt(top)).strip()
                    if _cjk_count(text) >= _MIN_DRAFT_CJK:
                        draft_text = text
                        break
                if draft_text is None:
                    steps["draft"] = {"topic_id": tid, "title": top["title"],
                                      "failed": f"初稿中文字数不足 {_MIN_DRAFT_CJK}（重试一次仍不够）"}
                else:
                    dest = self._root / "articles" / tid
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "v1.md").write_text(draft_text, encoding="utf-8")
                    now = int(time.time())
                    rel_path = f"articles/{tid}/v1.md"  # 相对插件数据根落库（照 article_save 约定）
                    db.insert("articles", {"topic_id": tid, "version": 1, "content_path": rel_path,
                                           "note": "守夜人初稿", "created_at": now})
                    db.update("topics", tid, {"status": "写作中", "updated_at": now})
                    steps["draft"] = {"topic_id": tid, "title": top["title"],
                                      "chars": _cjk_count(draft_text), "path": rel_path}
            _save_state(self._root, state)

        # 5. 晨报文本：口语化、简短、不用表格（系统提示词要求）
        human = self._build_brief(day, steps["hotspots"]["items"], picked, steps["draft"])
        return ActionResult(success=True, data={
            "human": human,
            "topics": [{**p, "id": tid} for p, tid in zip(picked, ids)],
            "drafted": bool(steps["draft"].get("chars")),
        })  # 不带 panel：夜里不弹任何面板

    @staticmethod
    def _build_brief(day: str, items: list[dict], picked: list[dict], draft: dict) -> str:
        lines = [f"🌙 守夜人晨报 · {day}", ""]
        if items:
            lines.append("昨夜热点 Top3：")
            for i, it in enumerate(items[:3], 1):
                lines.append(f"{i}. {it.get('title') or ''}（{it.get('source') or '?'} · 热度 {it.get('hot') or '?'}）")
            lines.append("")
        if picked:
            lines.append(f"定了 {len(picked)} 个选题：")
            for p in picked:
                lines.append(f"- 「{p['title']}」→ {p['platform']}：{p['reason'] or '挂上热点'}")
        else:
            lines.append("今晚没什么值得跟的热点，没定新选题。")
        lines.append("")
        if draft.get("chars"):
            lines.append(f"初稿：「{draft['title']}」已写好（{draft['chars']} 字），状态写作中。")
        elif draft.get("failed"):
            lines.append(f"初稿：「{draft.get('title') or ''}」起稿失败——{draft['failed']}。")
        lines.append("要哪个直接说，写稿/改稿/发前我再检测。")
        return "\n".join(lines)


def make_tools(ctx):
    return [NightBriefTool(os.path.dirname(ctx.db.path))]
