"""沙盒真实 LLM 端到端验收：distill_now 全链路（虚构数据 + 真实 GLM）。

绝不用真实数据目录；观察内容全虚构。跑完自清理由调用方负责。
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

DATA = tempfile.mkdtemp(prefix="yibao-distill-e2e-")
os.environ["YIBAO_DATA_DIR"] = DATA

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cryptography.fernet import Fernet  # noqa: E402
from yibao_brain.distiller import yesterday_window  # noqa: E402
from yibao_brain.perception import PerceptionStore  # noqa: E402
from yibao_brain.server import serve_async  # noqa: E402


def make_reader(msgs):
    it = iter(msgs + [None])
    return lambda: next(it)


def seed_store():
    p = PerceptionStore(os.path.join(DATA, "obs.db"), key=Fernet.generate_key())
    day, start, end = yesterday_window()
    p.append("app", "frontmost", {"app": "VSCode", "title": "distiller.py"}, "S1", ts=start + 9 * 3600)
    p.append("app", "frontmost", {"app": "Chrome", "title": "SQLite 文档"}, "S1", ts=start + 11 * 3600)
    p.append("app", "frontmost", {"app": "VSCode", "title": "server.py"}, "S1", ts=start + 14 * 3600)
    p.append("activity", "active", {"idle_seconds": 0}, "S1", ts=start + 9 * 3600)
    p.append("activity", "idle", {"idle_seconds": 120}, "S1", ts=start + 23 * 3600)
    p.append("screen", "tree", {"app": "VSCode", "title": "distiller.py",
             "text": "def run_yesterday(self): 提炼昨日观察"}, "S3", ts=start + 9 * 3600 + 60)
    p.append("screen", "tree", {"app": "Chrome", "title": "SQLite 文档",
             "text": "CREATE TABLE 语法参考"}, "S3", ts=start + 11 * 3600 + 60)
    return p, day


def query_db(path, sql):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close()
    return rows


async def main():
    p, day = seed_store()
    (open(os.path.join(DATA, "settings.json"), "w")
     .write(json.dumps({"perception.master": True, "perception.distill": True})))

    out = []
    await serve_async(make_reader([{"type": "distill_now"}]), out.append,
                      use_real=True, db_path=os.path.join(DATA, "a.db"), perception_store=p)

    replies = [m for m in out if m.get("type") == "distill_now"]
    print("=== 回包 ===")
    print(json.dumps(replies, ensure_ascii=False, indent=2))

    print("\n=== distill.db distillations ===")
    rows = query_db(os.path.join(DATA, "distill.db"),
                    "SELECT day, kind, text, confidence, projected FROM distillations ORDER BY id")
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print("=== distill.db runs ===")
    for r in query_db(os.path.join(DATA, "distill.db"),
                      "SELECT run_day, target_day, source, status, error FROM runs"):
        print(json.dumps(r, ensure_ascii=False))

    print("\n=== feed.db 投影 ===")
    for r in query_db(os.path.join(DATA, "feed.db"),
                      "SELECT kind, text, meta FROM feed ORDER BY id"):
        print(json.dumps(r, ensure_ascii=False))

    # 负路径：master 关闭 → disabled
    (open(os.path.join(DATA, "settings.json"), "w")
     .write(json.dumps({"perception.master": False, "perception.distill": True})))
    out2 = []
    await serve_async(make_reader([{"type": "distill_now"}]), out2.append,
                      use_real=True, db_path=os.path.join(DATA, "a2.db"), perception_store=p)
    print("\n=== 负路径回包（master 关）===")
    print(json.dumps([m for m in out2 if m.get("type") == "distill_now"], ensure_ascii=False))

    p.close()
    print(f"\nDATA_DIR={DATA}")


asyncio.run(main())
