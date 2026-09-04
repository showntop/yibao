"""Core 自投表面：内核对象（产物图谱）经同一面板协议投影给人。

与插件面板同一合同（schema+data 内联、kind="panel" 事件、explicit/presentation 提示），
desktop 不为 core 开特例。唯一例外是行动作走 native:* 旁路（本机打开/亮出文件由
宿主本地执行，不过 sidecar、无闸门——语义同 WebviewPanel 的 native: 白名单）。
"""
from __future__ import annotations

import time

_ARTIFACTS_SCHEMA = {
    "version": 1,
    "type": "list",
    "bind": {"items": "$data.rows"},
    "item": {"title": "$item.title", "subtitle": "$item.meta"},
    "empty": {"title": "还没有产物", "hint": "让译宝做点东西，产物会出现在这里"},
}


def artifacts_panel(rows: list[dict]) -> dict:
    """core:artifacts 产物列表面板载荷（kind="panel" 事件的 payload）。

    rows 来自 WorkGraphStore.list_artifact_views；人点工作语境卡触发，恒 explicit+stage。
    """
    return {
        "panel": "core:artifacts",
        "title": "产物",
        "schema": _ARTIFACTS_SCHEMA,
        "data": {"rows": [_row(view) for view in rows]},
        "explicit": True,
        "presentation": "stage",
    }


def _row(view: dict) -> dict:
    meta_parts = [str(view.get("type") or "")]
    version = view.get("version")
    if version is not None:
        meta_parts.append(f"v{version}")
    updated = float(view.get("updated_at") or 0)
    if updated > 0:
        meta_parts.append(time.strftime("%m-%d %H:%M", time.localtime(updated)))
    row: dict = {
        "id": str(view.get("id") or ""),
        "title": str(view.get("ref") or view.get("type") or "产物"),
        "meta": " · ".join(part for part in meta_parts if part),
    }
    path = str(view.get("path") or "")
    if path:
        # 文件型产物给本机动作；params 直接带字面路径（不依赖 $item 绑定）。
        # 行级 actions 覆盖 schema 模板（SchemaPanel 行数据优先）。
        row["path"] = path
        row["actions"] = [
            {"label": "在 Finder 显示", "method": "native:reveal", "params": {"path": path}},
            {"label": "打开", "method": "native:open", "params": {"path": path}},
        ]
    return row
