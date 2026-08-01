"""win 路径诊断（一次性）：看拒答场景裁窗后模型的原始返回。
用法：uv run python scripts/debug_win.py <场景名...>
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    from PIL import Image
    from yibao_brain.llm import ComputerUseClient
    import yibao_brain.llm as llm_mod
    from eval_click import _window_rect  # scripts 同目录复用

    sc_dir = Path(__file__).resolve().parent / "eval_scenarios"
    client = ComputerUseClient()
    print(f"model = {client.model}")
    for name in sys.argv[1:]:
        sc = json.loads((sc_dir / f"{name}.json").read_text())
        scale = float(sc.get("scale") or 1.0)
        rect = _window_rect(sc.get("tree") or {})
        with Image.open(sc["screenshot"]) as _raw:
            im = _raw.convert("RGB")
            box = [int(round(v * scale)) for v in rect]
            box[0], box[1] = max(0, box[0]), max(0, box[1])
            box[2], box[3] = min(im.width, box[2]), min(im.height, box[3])
            buf = io.BytesIO()
            im.crop(box).save(buf, format="PNG")
        b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        resp = llm_mod._vision_create_with_retry(lambda: client.client.chat.completions.create(
            model=client.model,
            extra_body={"thinking": {"type": "enabled"}},
            messages=[
                {"role": "system", "content": client.SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": b64}},
                    {"type": "text", "text": f"任务：{sc['target']}\n请给出下一步动作 JSON。"},
                ]},
            ],
        ))
        content = (resp.choices[0].message.content or "") if resp.choices else ""
        print(f"\n== {name}: target={sc['target']!r} crop={box}")
        print(f"   原始返回: {content[:400]!r}")


if __name__ == "__main__":
    main()
