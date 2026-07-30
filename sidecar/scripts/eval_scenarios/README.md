# 点击评测场景

每个 `<name>.json` 一场景：`{screenshot, tree, target, gt}`。
- 采集：`python scripts/eval_click.py --capture --name <n> --target "<目标描述>"`，再手填 `gt`（`region.rect=[x1,y1,x2,y2]` 逻辑坐标，或 `point.xy=[x,y]`）。
- 目标 ~12 个，覆盖：计算器/系统设置（a11y 易）、Safari 链接（a11y 中）、Canvas/自绘 UI、Electron（a11y 盲）。
- 跑：`python scripts/eval_click.py --scenarios scripts/eval_scenarios`。
