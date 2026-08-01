"""SoM 诊断工具：看各组件边界的真实数据（手动跑，非 CI）。
对指定场景：保存标记图、打印 marks 清单、找 GT 对应的正确 mark、打印模型原始回答。
用法：uv run python scripts/debug_som.py calc_eq [更多场景名...]
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    names = sys.argv[1:] or ["calc_eq"]
    sc_dir = Path(__file__).resolve().parent / "eval_scenarios"
    out_dir = Path(__file__).resolve().parent / "eval_reports" / "debug_marks"
    out_dir.mkdir(parents=True, exist_ok=True)

    from yibao_brain.grounding import SoMGrounding
    from yibao_brain.llm import ComputerUseClient

    som = SoMGrounding()
    client = ComputerUseClient()
    print(f"model = {client.model}")

    for name in names:
        sc = json.loads((sc_dir / f"{name}.json").read_text())
        scale = float(sc.get("scale") or 1.0)
        marked, marks, _zones = som.build_marks(sc["screenshot"], sc.get("tree") or {}, scale)
        if not marked:
            print(f"== {name}: build_marks 失败"); continue

        # 1) 标记图落盘（人工目验）
        img_path = out_dir / f"{name}.jpg"
        img_path.write_bytes(base64.b64decode(marked.split(",", 1)[1]))

        # 2) marks 清单 + GT 对应的正确 mark
        gt = sc["gt"]["rect"]
        gx1, gy1, gx2, gy2 = gt
        gcx, gcy = (gx1 + gx2) / 2, (gy1 + gy2) / 2
        correct = None
        for m in marks:
            x1, y1, x2, y2 = m["rect"]
            if x1 <= gcx <= x2 and y1 <= gcy <= y2:
                correct = m["id"]
                break
        a11y_n = sum(1 for m in marks if m["source"] == "a11y")
        print(f"\n== {name}: target={sc['target']!r} marks={len(marks)} (a11y {a11y_n} + grid {len(marks)-a11y_n})")
        print(f"   GT center=({gcx:.0f},{gcy:.0f}) → 正确 mark id = {correct}")
        for m in marks:
            x1, y1, x2, y2 = (round(v) for v in m["rect"])
            tag = " ← 正确" if m["id"] == correct else ""
            print(f"   #{m['id']:>2} {m['source']:<5} rect=[{x1},{y1},{x2},{y2}]{tag}")

        # 3) 真实调用，打印模型原始回答
        import yibao_brain.llm as llm_mod
        resp = llm_mod._vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            messages=[
                {"role": "system", "content": client.MARK_SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": marked}},
                    {"type": "text", "text": f"任务：{sc['target']}\n共有 {len(marks)} 个编号标记(1-{len(marks)})。给出下一个动作。"},
                ]},
            ],
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        parsed = client._parse_marked_action(content, len(marks))
        print(f"   模型原始回答: {content[:300]!r}")
        print(f"   解析结果: {parsed}")
        print(f"   标记图: {img_path}")


if __name__ == "__main__":
    main()
