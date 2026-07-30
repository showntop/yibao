"""点击精度评测：baseline(raw-bbox) vs SoM。手动跑，非 CI。

用法：
  python scripts/eval_click.py --scenarios scripts/eval_scenarios
  python scripts/eval_click.py --capture --name calc_eq --target "等号按钮"   # 采集场景

场景 JSON（scripts/eval_scenarios/<name>.json）：
  {"screenshot":"<abs png path>", "tree":{...a11y frontmost_tree...},
   "target":"目标描述", "gt":{"kind":"region","rect":[x1,y1,x2,y2]}  // 或 {"kind":"point","xy":[x,y]}
  }
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _b64_png(path: str) -> str:
    import base64
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def hit(point, gt) -> bool:
    if gt["kind"] == "region":
        x1, y1, x2, y2 = gt["rect"]
        return x1 <= point[0] <= x2 and y1 <= point[1] <= y2
    if gt["kind"] == "point":
        return _dist(point, gt["xy"]) <= gt.get("tolerance", 12)
    return False


def center_distance(point, gt) -> float:
    if gt["kind"] == "region":
        x1, y1, x2, y2 = gt["rect"]
        return _dist(point, ((x1 + x2) / 2, (y1 + y2) / 2))
    if gt["kind"] == "point":
        return _dist(point, gt["xy"])
    return float("inf")


def run_baseline(client, som, sc, scale):
    action = client.next_action(_b64_png(sc["screenshot"]), sc["target"], [])
    if not action or action.get("action") != "click" or len(action.get("box") or []) != 4:
        return None
    x1, y1, x2, y2 = (float(v) for v in action["box"])
    return ((x1 + x2) / 2 / scale, (y1 + y2) / 2 / scale)


def run_som(client, som, sc, scale):
    marked, marks = som.build_marks(sc["screenshot"], sc.get("tree") or {}, scale)
    if not marked:
        return None
    action = client.choose_action(marked, sc["target"], len(marks), [])
    if not action or action.get("action") != "click":
        return None
    return som.predict(action.get("mark"), marks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", required=False, help="场景目录（每文件一 JSON）")
    ap.add_argument("--capture", action="store_true", help="交互采集一个场景")
    ap.add_argument("--name", default="scene")
    ap.add_argument("--target", default="")
    args = ap.parse_args()

    if args.capture:
        _capture(args)
        return

    from yibao_brain.llm import ComputerUseClient
    from yibao_brain.grounding import SoMGrounding, _physical_scale

    client = ComputerUseClient()
    som = SoMGrounding()
    scs = [json.loads(p.read_text()) for p in sorted(Path(args.scenarios).glob("*.json"))]
    if not scs:
        print("无场景，先 --capture 采集。"); return
    rows = []
    for sc in scs:
        scale = _physical_scale(sc["screenshot"])
        b = run_baseline(client, som, sc, scale)
        s = run_som(client, som, sc, scale)
        rows.append({
            "name": sc.get("name", "?"),
            "baseline": b, "som": s, "gt": sc["gt"],
        })
    _report(rows)


def _report(rows):
    n = len(rows)
    b_hit = sum(1 for r in rows if r["baseline"] and hit(r["baseline"], r["gt"]))
    s_hit = sum(1 for r in rows if r["som"] and hit(r["som"], r["gt"]))
    b_d = [center_distance(r["baseline"], r["gt"]) for r in rows if r["baseline"]]
    s_d = [center_distance(r["som"], r["gt"]) for r in rows if r["som"]]
    print(f"{'场景':<16}{'baseline':<22}{'SoM':<22}")
    for r in rows:
        print(f"{r['name']:<16}{_fmt(r['baseline']):<22}{_fmt(r['som']):<22}")
    print("-" * 60)
    print(f"hit-rate:  baseline {b_hit}/{n} = {b_hit/n:.0%}   SoM {s_hit}/{n} = {s_hit/n:.0%}")
    b_avg = sum(b_d) / len(b_d) if b_d else float("inf")
    s_avg = sum(s_d) / len(s_d) if s_d else float("inf")
    print(f"平均距离:  baseline {b_avg:.1f}px   SoM {s_avg:.1f}px  (仅成功预测)")


def _fmt(p):
    return "—" if not p else f"{p[0]:.0f},{p[1]:.0f}"


def _capture(args):
    """截主屏 + 抓 a11y 树 + 落 JSON（gt 手填）。"""
    from mss import mss
    from yibao_brain.mac.a11y_mac import MacA11yReader
    out = Path("scripts/eval_scenarios"); out.mkdir(parents=True, exist_ok=True)
    shot = out / f"{args.name}.png"
    with mss() as s:
        s.shot(mon=-1, output=str(shot))
    tree = MacA11yReader().frontmost_tree()
    (out / f"{args.name}.json").write_text(json.dumps({
        "name": args.name, "screenshot": str(shot.resolve()), "tree": tree,
        "target": args.target,
        "gt": {"kind": "region", "rect": [0, 0, 0, 0]},  # TODO 手填真实目标框
    }, ensure_ascii=False, indent=2))
    print(f"已采集 {args.name}，请手填 {args.name}.json 的 gt.rect")


if __name__ == "__main__":
    main()
