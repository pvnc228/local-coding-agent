"""MCP Tasks extension (``io.modelcontextprotocol/tasks``) for async delegation.

The 2026-07-28 stateless protocol moved long-running work into the Tasks
extension (SEP-2663). This module is the server side of that extension: it
declares the extension identifier, serves the ``tasks/get``, ``tasks/update``
and ``tasks/cancel`` methods, and intercepts ``tools/call`` so a
``delegate_code`` invocation can return a ``CreateTaskResult``
(``resultType: "task"``) instead of blocking.

The extension does not own policy, validation or result state. It is a thin
wire adapter over a :class:`~local_coding_agent.worker_pool.BoundedWorkerPool`,
which is the bounded in-memory reservation for a task handle.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .worker_pool import BoundedWorkerPool

try:
    from pydantic import BaseModel, ConfigDict, Field

    from mcp.server.context import ServerRequestContext
    from mcp.server.extension import Extension, MethodBinding
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        CancelTaskRequestParams,
        GetTaskRequestParams,
        INVALID_PARAMS,
        TextContent,
    )
    from mcp.shared.exceptions import MCPError

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - mcp/pydantic is an optional dependency
    _MCP_AVAILABLE = False

TASKS_IDENTIFIER = "io.modelcontextprotocol/tasks"

_POOL_STATE_TO_TASK = {
    "queued": "working",
    "working": "working",
    "completed": "completed",
    # A restarted process cannot safely replay without the original request
    # envelope. The worker pool exposes an interrupted terminal result, and
    # Tasks carries it as a completed response with isError=true.
    "interrupted": "completed",
    # Controller/tool failures are CallToolResult(isError=true), not JSON-RPC
    # failures; SEP-2663 keeps those tasks in the completed state.
    "failed": "completed",
    "cancelled": "cancelled",
}

_DEFAULT_TTL_MS = 30 * 60 * 1000
_DEFAULT_POLL_INTERVAL_MS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_declared_tasks(ctx: ServerRequestContext[Any, Any]) -> bool:
    """True when the client declared the tasks extension in its capabilities."""

    try:
        capabilities = ctx.session.client_capabilities
    except AttributeError:  # pragma: no cover - defensive against SDK drift
        return False
    declared = capabilities.extensions if capabilities is not None else None
    return bool(declared and TASKS_IDENTIFIER in declared)


def task_dict(pool: BoundedWorkerPool, caller_id: str, task_id: str) -> dict[str, Any]:
    """Render a pool snapshot as the modern (SEP-2663) flat Task wire shape.

    The wire shape is the flat ``Task`` from the ext-tasks schema: ``taskId``,
    ``status``, ``statusMessage``, ``createdAt``, ``lastUpdatedAt``, ``ttlMs``
    and optional ``pollIntervalMs``, with a terminal ``result``/``error``.
    """

    snapshot = pool.get(caller_id, task_id)
    _raise_if_unknown(snapshot, task_id)
    status = _POOL_STATE_TO_TASK.get(snapshot.get("status"), "working")
    created_at = snapshot.get("created_at") or _now_iso()
    updated_at = snapshot.get("updated_at") or created_at
    task: dict[str, Any] = {
        "taskId": task_id,
        "status": status,
        "createdAt": created_at,
        "lastUpdatedAt": updated_at,
        "ttlMs": _DEFAULT_TTL_MS,
        "pollIntervalMs": _DEFAULT_POLL_INTERVAL_MS,
    }
    if status == "completed":
        task["result"] = _call_tool_result(snapshot.get("result") or {})
    return task


def _raise_if_unknown(snapshot: Mapping[str, Any], task_id: str) -> None:
    error = snapshot.get("error")
    if isinstance(error, Mapping) and error.get("kind") == "unknown_job":
        raise MCPError(
            code=INVALID_PARAMS,
            message=f"unknown taskId: {task_id}",
        )


def _call_tool_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Convert controller-owned result data to the original CallToolResult shape."""

    value = dict(result)
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, sort_keys=True),
            }
        ],
        "isError": value.get("status") == "failed",
        "structuredContent": value,
    }


if _MCP_AVAILABLE:

    class UpdateTaskParams(BaseModel):
        """Params for ``tasks/update`` (SEP-2663): responses to input requests."""

        model_config = ConfigDict(populate_by_name=True, extra="ignore")

        task_id: str = Field(alias="taskId")
        input_responses: dict[str, Any] | None = Field(alias="inputResponses", default=None)

    class TasksExtension(Extension):
        """Server side of ``io.modelcontextprotocol/tasks`` over a worker pool."""

        identifier = TASKS_IDENTIFIER

        def __init__(
            self,
            pool: BoundedWorkerPool,
            *,
            caller_id: str,
            ttl_ms: int = _DEFAULT_TTL_MS,
            poll_interval_ms: int = _DEFAULT_POLL_INTERVAL_MS,
        ) -> None:
            self._pool = pool
            self._caller_id = caller_id
            self._ttl_ms = ttl_ms
            self._poll_interval_ms = poll_interval_ms

        def methods(self) -> list[MethodBinding]:
            return [
                MethodBinding("tasks/get", GetTaskRequestParams, self._handle_get),
                MethodBinding("tasks/cancel", CancelTaskRequestParams, self._handle_cancel),
                MethodBinding("tasks/update", UpdateTaskParams, self._handle_update),
            ]

        async def intercept_tool_call(
            self,
            params: CallToolRequestParams,
            ctx: ServerRequestContext[Any, Any],
            call_next,
        ) -> Any:
            """Short-circuit ``delegate_code`` into a task when the client opted in.

            A client that did not declare the extension falls through to the
            synchronous handler, so the server never returns a task handle to a
            client that cannot handle it.
            """

            if params.name != "delegate_code":
                return await call_next(ctx)
            if not _client_declared_tasks(ctx):
                return await call_next(ctx)
            try:
                from .service import DelegationRequest
                from .task import TaskEnvelope

                arguments = params.arguments or {}
                request = DelegationRequest(
                    request_id=arguments.get("request_id"),
                    workspace_ref=arguments.get("workspace_ref"),
                    model_profile=arguments.get("model_profile"),
                    task=TaskEnvelope.from_mapping(arguments.get("task") or {}),
                )
            except (TypeError, ValueError):
                # Invalid arguments must report the same synchronous policy failure
                # as the non-task path; fall through to the real handler.
                return await call_next(ctx)
            snapshot = self._pool.submit(self._caller_id, request)
            if snapshot.get("status") == "failed":
                error = snapshot.get("error") or {}
                return CallToolResult(
                    content=[TextContent(type="text", text=str(error))],
                    is_error=True,
                )
            return {
                "resultType": "task",
                "taskId": snapshot["job_id"],
                "status": _POOL_STATE_TO_TASK.get(snapshot.get("status"), "working"),
                "createdAt": snapshot.get("created_at") or _now_iso(),
                "lastUpdatedAt": snapshot.get("updated_at") or _now_iso(),
                "ttlMs": self._ttl_ms,
                "pollIntervalMs": self._poll_interval_ms,
            }

        async def _handle_get(
            self,
            ctx: ServerRequestContext[Any, Any],
            params: GetTaskRequestParams,
        ) -> dict[str, Any]:
            task = task_dict(self._pool, self._caller_id, params.task_id)
            task["resultType"] = "complete"
            return task

        async def _handle_cancel(
            self,
            ctx: ServerRequestContext[Any, Any],
            params: CancelTaskRequestParams,
        ) -> dict[str, Any]:
            snapshot = self._pool.cancel(self._caller_id, params.task_id)
            _raise_if_unknown(snapshot, params.task_id)
            return {"resultType": "complete"}

        async def _handle_update(
            self,
            ctx: ServerRequestContext[Any, Any],
            params: UpdateTaskParams,
        ) -> dict[str, Any]:
            snapshot = self._pool.get(self._caller_id, params.task_id)
            _raise_if_unknown(snapshot, params.task_id)
            # MVP has no `input_required` flow: delegate_code never surfaces
            # input requests, so tasks/update only acknowledges. Responses to
            # unknown or already-satisfied keys are ignored per the spec.
            return {"resultType": "complete"}
