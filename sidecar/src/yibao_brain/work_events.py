"""Tool result → Work Graph 事件适配。

Tool 只声明自己产出什么；本模块从安全结果投影事件，并由唯一 ToolInvoker 即时提交。
插件不知道 WorkGraphStore 实现，也拿不到其他 Workspace 的写权限。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .log import log


def normalize_work_output(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("work_output 必须是对象")
    value = dict(raw)
    kind = str(value.get("kind") or "artifact").strip()
    if kind not in ("artifact", "evidence"):
        raise ValueError(f"未知 work_output kind：{kind!r}")
    if not str(value.get("artifact_type") or "").strip():
        raise ValueError("work_output artifact_type 不能为空")
    if not str(value.get("ref_from") or value.get("ref") or "").strip():
        raise ValueError("work_output ref_from/ref 不能为空")
    if kind == "evidence" and not str(value.get("claim_from") or value.get("claim") or "").strip():
        raise ValueError("evidence work_output claim_from/claim 不能为空")
    fields = value.get("metadata_fields") or []
    if not isinstance(fields, list) or not all(isinstance(item, str) for item in fields):
        raise ValueError("work_output metadata_fields 必须是字符串数组")
    value["kind"] = kind
    return value


def _value(root: dict, path: str | None, default: Any = "") -> Any:
    if not path:
        return default
    current: Any = root
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def materialize_work_events(
    specs: tuple[dict, ...] | list[dict], *, params: dict, data: dict, tool_id: str,
    strict: bool = False,
) -> list[dict]:
    """把声明式 work_output 转成内核事件。

    普通兼容路径可跳过运行期缺字段的声明；PluginDb 事务路径必须 strict，避免
    业务数据已经提交、领域事件却因结果契约不完整而永久丢失。
    """
    root = {"params": params or {}, "data": data or {}}
    events: list[dict] = []
    for raw in specs or []:
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(f"{tool_id} 的 work_output 必须是对象")
            continue
        kind = str(raw.get("kind") or "artifact").strip()
        if kind not in ("artifact", "evidence"):
            if strict:
                raise ValueError(f"{tool_id} 的 work_output kind 非法：{kind!r}")
            continue
        artifact_type = str(raw.get("artifact_type") or "").strip()
        ref = str(_value(root, raw.get("ref_from"), raw.get("ref") or "") or "").strip()
        if not artifact_type or not ref:
            if strict:
                raise ValueError(
                    f"{tool_id} 的 work_output 缺少运行期 artifact_type/ref"
                )
            continue
        metadata: dict = {"tool_id": tool_id}
        for path in raw.get("metadata_fields") or []:
            value = _value(root, str(path), None)
            if value is not None:
                metadata[str(path).split(".")[-1]] = value
        if kind == "artifact":
            content_ref = str(
                _value(root, raw.get("content_ref_from"), raw.get("content_ref") or "") or ""
            ).strip()
            events.append({
                "event_type": "artifact.upsert",
                "payload": {
                    "artifact_type": artifact_type,
                    "ref": ref,
                    "content_ref": content_ref,
                    "lifecycle": str(raw.get("lifecycle") or "draft"),
                    "metadata": metadata,
                },
            })
        elif kind == "evidence":
            claim = str(_value(root, raw.get("claim_from"), raw.get("claim") or "") or "").strip()
            if not claim:
                if strict:
                    raise ValueError(f"{tool_id} 的 evidence work_output 缺少运行期 claim")
                continue
            events.append({
                "event_type": "evidence.capture",
                "payload": {
                    "artifact_type": artifact_type,
                    "ref": ref,
                    "claim": claim,
                    "source_uri": str(_value(root, raw.get("source_uri_from"), "") or "").strip(),
                    "source_title": str(_value(root, raw.get("source_title_from"), "") or "").strip(),
                    "publisher": str(_value(root, raw.get("publisher_from"), "") or "").strip(),
                    "confidence": float(raw.get("confidence") or 0.5),
                    "metadata": metadata,
                },
            })
    return events


class WorkGraphInvocationSink:
    """ToolInvoker 的持久化接收器与 PluginDb outbox 协调器。"""

    def __init__(self, graph, workspace_for_conversation: Callable[[str], str]):
        self._graph = graph
        self._workspace_for_conversation = workspace_for_conversation

    def begin(self, action, params: dict, meta: dict) -> str | None:
        try:
            conversation_id = str(meta.get("conversation_id") or "")
            workspace_id = str(meta.get("workspace_id") or "")
            if not workspace_id:
                workspace_id = self._workspace_for_conversation(conversation_id)
            return self._graph.begin_invocation(
                action_id=str(action.id), workspace_id=workspace_id or None,
                conversation_id=conversation_id, surface=str(meta.get("surface") or ""),
                tool_id=str(action.tool_id), params=params,
            )
        except Exception as exc:
            log(f"Invocation 开始记录失败（不阻断 tool）：{exc}")
            return None

    def complete(
        self, invocation_id: str | None, action, result, specs, *,
        plugin_db=None, domain_events_persisted: bool = False,
    ) -> None:
        if not invocation_id:
            return
        try:
            events = []
            if result.success and not domain_events_persisted:
                events = materialize_work_events(
                    specs or (), params=action.params, data=result.data or {},
                    tool_id=action.tool_id, strict=True,
                )
            self._graph.complete_invocation(
                invocation_id, success=bool(result.success),
                safe_result={"success": result.success, "data": result.data, "error": result.error},
                error=str(result.error or ""), work_events=events,
            )
            if result.success and domain_events_persisted and plugin_db is not None:
                self.reconcile_plugin_db(plugin_db)
        except Exception as exc:
            log(f"Invocation 完成记录失败（不阻断 tool）：{exc}")

    def reconcile_plugin_db(self, plugin_db) -> int:
        """把一个插件的持久 outbox 幂等交给 Host；可安全重复调用。"""
        rows = plugin_db.pending_work_events()
        if not rows:
            return 0
        by_invocation: dict[str, list[dict]] = {}
        for row in rows:
            by_invocation.setdefault(str(row["invocation_id"]), []).append(row)
        acknowledged = 0
        for invocation_id, events in by_invocation.items():
            ids = [str(event["id"]) for event in events]
            try:
                statuses = self._graph.ingest_plugin_events(invocation_id, events)
                accepted = [
                    event_id for event_id in ids
                    if statuses.get(event_id) in ("applied", "blocked")
                ]
                if accepted:
                    plugin_db.acknowledge_work_events(accepted)
                    acknowledged += len(accepted)
                retry = [event_id for event_id in ids if event_id not in accepted]
                if retry:
                    plugin_db.fail_work_events(retry, "Host 尚未应用 work event")
            except Exception as exc:
                plugin_db.fail_work_events(ids, str(exc))
                log(f"PluginDb work outbox 对账失败（等待重试）：{exc}")
        return acknowledged

    def reconcile_registry(self, registry) -> int:
        """启动恢复：扫描已加载插件的共享 PluginDb，去重后逐库对账。"""
        seen: set[str] = set()
        acknowledged = 0
        for skill in registry.list():
            plugin_db = getattr(getattr(skill, "plugin_ctx", None), "db", None)
            path = str(getattr(plugin_db, "path", "")) if plugin_db is not None else ""
            if plugin_db is None or path in seen:
                continue
            seen.add(path)
            acknowledged += self.reconcile_plugin_db(plugin_db)
        return acknowledged
