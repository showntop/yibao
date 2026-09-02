"""Workflow-attached durable execution runtime.

The runtime deliberately knows nothing about video, slides, shell commands, or
provider SDKs.  A provider implements one capability, receives a persisted
checkpoint, and may atomically advance that checkpoint through ``control``.
WorkGraphStore remains the authority for lifecycle, attempts, and outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Callable

from .work_graph import WorkGraphStore


class DurableProviderError(RuntimeError):
    """A provider failure that may or may not be safe to fall back from."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class DurableCancelled(RuntimeError):
    """Raised at a provider-defined safe point after cancellation is requested."""


@dataclass(frozen=True)
class DurableOutcome:
    result: dict = field(default_factory=dict)
    work_events: tuple[dict, ...] = ()


DurableHandler = Callable[[dict, dict, "DurableExecutionControl"], DurableOutcome | dict]


@dataclass(frozen=True)
class DurableProvider:
    capability_id: str
    provider_id: str
    handler: DurableHandler
    supports_resume: bool = True


class DurableExecutionControl:
    """The only mutable handle exposed to provider code."""

    def __init__(
        self, store: WorkGraphStore, execution: dict,
        on_update: Callable[[dict], None] | None = None,
    ):
        self._store = store
        self.execution_id = str(execution["id"])
        self._version = int(execution.get("checkpoint_version") or 0)
        self._checkpoint = dict(execution.get("checkpoint") or {})
        self._lock = threading.Lock()
        self._on_update = on_update

    @property
    def checkpoint_value(self) -> dict:
        with self._lock:
            return dict(self._checkpoint)

    @property
    def checkpoint_version(self) -> int:
        with self._lock:
            return self._version

    def checkpoint(self, value: dict, *, progress: float) -> dict:
        """Persist execution + StageInstance progress with optimistic CAS."""
        self.raise_if_cancelled()
        with self._lock:
            updated = self._store.checkpoint_durable_execution(
                self.execution_id,
                value,
                progress=progress,
                expected_version=self._version,
            )
            self._version = int(updated["checkpoint_version"])
            self._checkpoint = dict(updated.get("checkpoint") or {})
        if self._on_update is not None:
            self._on_update(updated)
        self.raise_if_cancelled()
        return updated

    def cancel_requested(self) -> bool:
        return self._store.durable_execution_cancel_requested(self.execution_id)

    def raise_if_cancelled(self) -> None:
        if self.cancel_requested():
            raise DurableCancelled("用户取消")


class DurableExecutionEngine:
    """In-process dispatcher backed by durable WorkGraph state.

    Threads are only workers; they are not the source of truth.  If the process
    exits, WorkGraphStore converts active executions to ``interrupted`` and a
    new engine can recover them from their last checkpoint.
    """

    def __init__(
        self, store: WorkGraphStore,
        on_update: Callable[[dict], None] | None = None,
    ):
        self.store = store
        self._providers: dict[tuple[str, str], DurableProvider] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._closed = False
        self.on_update = on_update

    def register_provider(
        self,
        *,
        capability_id: str,
        provider_id: str,
        handler: DurableHandler,
        supports_resume: bool = True,
    ) -> None:
        key = (str(capability_id).strip(), str(provider_id).strip())
        if not all(key) or not callable(handler):
            raise ValueError("durable provider 缺少 capability/provider/handler")
        with self._lock:
            if key in self._providers:
                raise ValueError(f"durable provider 重复注册：{key[0]}/{key[1]}")
            self._providers[key] = DurableProvider(
                capability_id=key[0],
                provider_id=key[1],
                handler=handler,
                supports_resume=bool(supports_resume),
            )

    def start(
        self,
        *,
        workspace_id: str,
        stage_id: str,
        capability_id: str,
        provider_candidates: list[str],
        request: dict,
        idempotency_key: str,
        invocation_id: str | None = None,
        cancel_mode: str = "checkpoint",
        resume_supported: bool = True,
    ) -> dict:
        execution = self.store.create_durable_execution(
            workspace_id=workspace_id,
            stage_id=stage_id,
            capability_id=capability_id,
            provider_candidates=provider_candidates,
            request=request,
            idempotency_key=idempotency_key,
            invocation_id=invocation_id,
            cancel_mode=cancel_mode,
            resume_supported=resume_supported,
        )
        if execution["status"] not in ("completed", "failed", "cancelled"):
            self._spawn(str(execution["id"]))
        self._notify(execution)
        return self.store.durable_execution_view(str(execution["id"])) or execution

    def recover(self) -> list[str]:
        recovered: list[str] = []
        for execution in self.store.resumable_durable_executions():
            execution_id = str(execution["id"])
            if execution["status"] == "cancel_requested":
                self.store.finish_durable_execution(
                    execution_id, status="cancelled", error="用户取消",
                )
                continue
            if not any(
                (str(execution["capability_id"]), str(provider_id)) in self._providers
                for provider_id in execution.get("provider_candidates", [])
            ):
                # The owning plugin may be disabled or not loaded yet. Keep the
                # durable record resumable instead of treating absence as failure.
                continue
            if self._spawn(execution_id):
                recovered.append(execution_id)
        return recovered

    def cancel(self, execution_id: str) -> bool:
        changed = self.store.request_cancel_durable_execution(execution_id)
        if changed:
            self._notify(self.store.durable_execution_view(execution_id))
        return changed

    def resume(self, execution_id: str) -> bool:
        execution = self.store.durable_execution_view(execution_id)
        if execution is None or execution["status"] not in ("queued", "interrupted"):
            return False
        if not any(
            (str(execution["capability_id"]), str(provider_id)) in self._providers
            for provider_id in execution.get("provider_candidates", [])
        ):
            return False
        return self._spawn(execution_id)

    def wait(self, execution_id: str, timeout: float | None = None) -> dict:
        with self._lock:
            worker = self._workers.get(execution_id)
        if worker is not None:
            worker.join(timeout=timeout)
        return self.store.durable_execution_view(execution_id) or {}

    def shutdown(self, *, wait: bool = False, timeout: float = 1.0) -> None:
        """Stop dispatching. Running work is intentionally not marked cancelled."""
        with self._lock:
            self._closed = True
            workers = list(self._workers.values())
        if wait:
            for worker in workers:
                worker.join(timeout=timeout)

    def _spawn(self, execution_id: str) -> bool:
        with self._lock:
            if self._closed:
                return False
            current = self._workers.get(execution_id)
            if current is not None and current.is_alive():
                return False
            worker = threading.Thread(
                target=self._run,
                args=(execution_id,),
                name=f"durable-{execution_id[-8:]}",
                daemon=True,
            )
            self._workers[execution_id] = worker
            worker.start()
            return True

    def _notify(self, execution: dict | None) -> None:
        callback = self.on_update
        if callback is None or execution is None:
            return
        try:
            callback(execution)
        except Exception:
            pass

    def _run(self, execution_id: str) -> None:
        try:
            execution = self.store.durable_execution_view(execution_id)
            if execution is None or execution["status"] in ("completed", "failed", "cancelled"):
                return
            if execution["status"] == "cancel_requested":
                self.store.finish_durable_execution(
                    execution_id, status="cancelled", error="用户取消",
                )
                self._notify(self.store.durable_execution_view(execution_id))
                return

            attempted = {str(item["provider_id"]) for item in execution.get("attempts", [])}
            candidates = list(execution.get("provider_candidates") or [])
            remaining = [provider_id for provider_id in candidates if provider_id not in attempted]
            # A process restart may have interrupted the currently selected provider.
            # Retry it once from checkpoint before falling through to later providers.
            if execution["status"] == "interrupted" and execution.get("provider_id"):
                previous = str(execution["provider_id"])
                if previous in candidates:
                    remaining.insert(0, previous)

            last_error = "无可用 provider"
            for provider_id in list(dict.fromkeys(remaining)):
                provider = self._providers.get((str(execution["capability_id"]), provider_id))
                if provider is None:
                    last_error = f"provider 未注册：{provider_id}"
                    continue
                latest = self.store.durable_execution_view(execution_id) or execution
                checkpoint = dict(latest.get("checkpoint") or {})
                if checkpoint and not provider.supports_resume:
                    last_error = f"provider 不支持从 checkpoint 续跑：{provider_id}"
                    continue
                try:
                    claimed = self.store.claim_durable_execution(execution_id, provider_id)
                    self._notify(claimed)
                    control = DurableExecutionControl(self.store, claimed, self._notify)
                    control.raise_if_cancelled()
                    outcome = provider.handler(
                        dict(claimed.get("request") or {}),
                        dict(claimed.get("checkpoint") or {}),
                        control,
                    )
                    control.raise_if_cancelled()
                    if isinstance(outcome, DurableOutcome):
                        result = outcome.result
                        events = list(outcome.work_events)
                    elif isinstance(outcome, dict):
                        result, events = outcome, []
                    else:
                        raise DurableProviderError("provider 返回值必须是 dict 或 DurableOutcome", retryable=False)
                    self.store.finish_durable_execution(
                        execution_id,
                        status="completed",
                        result=result,
                        work_events=events,
                    )
                    self._notify(self.store.durable_execution_view(execution_id))
                    return
                except DurableCancelled as exc:
                    self.store.finish_durable_execution(
                        execution_id, status="cancelled", error=str(exc),
                    )
                    self._notify(self.store.durable_execution_view(execution_id))
                    return
                except DurableProviderError as exc:
                    last_error = str(exc)
                    failed = self.store.fail_durable_attempt(
                        execution_id, last_error, retryable=exc.retryable,
                    )
                    self._notify(failed)
                    if not exc.retryable or failed["status"] == "failed":
                        return
                except Exception as exc:  # provider bugs are safe fallback candidates
                    last_error = f"{type(exc).__name__}: {exc}"
                    failed = self.store.fail_durable_attempt(
                        execution_id, last_error, retryable=True,
                    )
                    self._notify(failed)

            latest = self.store.durable_execution_view(execution_id)
            if latest and latest["status"] not in ("completed", "failed", "cancelled"):
                self.store.finish_durable_execution(
                    execution_id, status="failed", error=last_error,
                )
                self._notify(self.store.durable_execution_view(execution_id))
        finally:
            with self._lock:
                current = self._workers.get(execution_id)
                if current is threading.current_thread():
                    self._workers.pop(execution_id, None)
