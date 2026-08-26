"""MCP server exposing proposal-only ``delegate_code`` via the official SDK.

Built on ``mcp==2.0.0``, which speaks the 2026-07-28 stateless protocol
(per-request ``_meta``, ``server/discover``, ``resultType``) and auto-falls
back to the legacy ``initialize`` handshake for older clients through
``serve_dual_era_loop``. Policy, validation, idempotency and result ownership
stay in :class:`DelegationService`; this server is only a wire adapter.

With ``enable_tasks``, the server also mounts the ``io.modelcontextprotocol/tasks``
extension over a bounded worker pool (async lifecycle) and an ``apply_proposal``
tool whose confirmation is a Multi Round-Trip Request elicitation.

Single-tenant: the stdio server serves one direct client process, so every
request shares the fixed caller id ``"mcp-stdio"`` (idempotency + task pool
namespace). Multi-tenant caller scoping belongs to a remote HTTP gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Annotated, Any

from .service import DelegationService
from .worker_pool import ExecutionOverload, SharedExecutionGate

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - pydantic comes with mcp
    BaseModel = None  # type: ignore[assignment]

_CALLER_ID = "mcp-stdio"
_SERVER_NAME = "local-coding-agent"
_SERVER_VERSION = "0.8.2"




class _AsyncExecutionGate:
    """Async adapter over the shared thread-safe runtime gate."""

    def __init__(self, runtime_gate: SharedExecutionGate) -> None:
        self._runtime_gate = runtime_gate

    async def run(self, function, *args):
        return await asyncio.to_thread(self._runtime_gate.run, function, *args)

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.types import CallToolResult, TextContent

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency is optional
    MCPServer = None  # type: ignore[assignment]
    CallToolResult = None  # type: ignore[assignment]
    TextContent = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False

try:
    from mcp.server.elicitation import (
        AcceptedElicitation,
        CancelledElicitation,
        DeclinedElicitation,
        ElicitationResult,
    )
    from mcp.server.mcpserver import Context, Elicit, Resolve
except ImportError:  # pragma: no cover - mcp is an optional dependency
    AcceptedElicitation = None  # type: ignore[assignment]
    CancelledElicitation = None  # type: ignore[assignment]
    DeclinedElicitation = None  # type: ignore[assignment]
    ElicitationResult = None  # type: ignore[assignment]
    Context = None  # type: ignore[assignment]
    Elicit = None  # type: ignore[assignment]
    Resolve = None  # type: ignore[assignment]


def _require_mcp() -> None:
    if not _MCP_AVAILABLE:
        raise ImportError(
            "the MCP server requires the 'mcp' package; install it with `pip install mcp==2.0.0`"
        )


def _delegate_request(request_id: Any, workspace_ref: Any, model_profile: Any, task: Any):
    from .service import DelegationRequest
    from .task import TaskEnvelope

    return DelegationRequest(
        request_id=request_id,
        workspace_ref=workspace_ref,
        model_profile=model_profile,
        task=TaskEnvelope.from_mapping(task),
    )


# Module-level apply confirmation model and resolver so the official SDK can
# evaluate the ``apply_proposal`` tool's annotations via ``inspect.signature``.
# Defined only when pydantic/mcp are present; they are referenced solely at
# tool-registration time, after ``_require_mcp`` has already succeeded.
if BaseModel is not None:
    class ApplyConfirmation(BaseModel):
        confirm: bool
        proposal_id: str
        workspace_ref: str
        proposal_digest: str


    def _proposal_digest(preview: dict[str, Any]) -> str:
        canonical = {
            "request_id": preview.get("request_id"),
            "workspace_ref": preview.get("workspace_ref"),
            "status": preview.get("status"),
            "summary": preview.get("summary", ""),
            "files": preview.get("files", []),
            "patch": preview.get("patch", ""),
        }
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


    def _require_apply_preview(preview: Any, request_id: str, workspace_ref: str) -> dict[str, Any]:
        if not isinstance(preview, dict):
            raise ValueError("proposal preview is unavailable")
        if preview.get("status") != "accepted":
            raise ValueError("proposal preview is not an accepted proposal")
        if preview.get("request_id") != request_id or preview.get("workspace_ref") != workspace_ref:
            raise ValueError("proposal preview identity does not match the apply request")
        if not isinstance(preview.get("files"), list):
            raise ValueError("proposal preview files are unavailable")
        if not isinstance(preview.get("patch"), str) or not preview["patch"].strip():
            raise ValueError("proposal preview does not contain a patch")
        return preview

    def _resolve_apply(ctx: Context) -> Elicit[ApplyConfirmation]:
        arguments: dict[str, Any] = {}
        input_params = getattr(ctx, "_input_params", None)
        raw_arguments = getattr(input_params, "arguments", None)
        if isinstance(raw_arguments, dict):
            arguments = raw_arguments
        if not arguments:
            request_context = getattr(ctx, "request_context", None)
            request = getattr(request_context, "request", None)
            raw_arguments = getattr(request, "arguments", None)
            if isinstance(raw_arguments, dict):
                arguments = raw_arguments

        request_id = arguments.get("request_id", "unknown")
        workspace_ref = arguments.get("workspace_ref", "unknown")
        service = getattr(ctx.mcp_server, "_delegation_service", None)
        if service is None:
            service = getattr(ctx.mcp_server, "_codex_service", None)
        preview = _require_apply_preview(
            service.proposal_preview(_CALLER_ID, workspace_ref, request_id), request_id, workspace_ref
        )
        files = ", ".join(str(path) for path in preview.get("files", [])) or "(не указаны)"
        patch = preview["patch"]
        message = (
            "Подтвердите применение именно этого предложения к рабочей области.\n"
            f"proposal_id: {preview['request_id']}\n"
            f"workspace: {preview['workspace_ref']}\n"
            f"proposal_digest: {_proposal_digest(preview)}\n"
            f"status: {preview['status']}\n"
            f"summary: {preview.get('summary', '')}\n"
            f"files: {files}\n"
            f"diff:\n{patch}"
        )
        return Elicit(message, ApplyConfirmation)


def build_server(
    service: DelegationService,
    *,
    enable_tasks: bool = False,
    max_workers: int = 1,
    max_queue: int = 16,
    task_store: Any | None = None,
):
    """Build an official-SDK MCP server exposing proposal-only ``delegate_code``.

    Args:
        service: The transport-neutral delegation service.
        enable_tasks: When True, mount the Tasks extension (async lifecycle) and
            ``apply_proposal``. When False (default), keep the pure synchronous
            proposal-only path.
        max_workers: Active sync/task worker slots.
        max_queue: Admission queue bound for sync/task work.
        task_store: Optional durable TaskStore for task state persistence across restarts.
    """

    _require_mcp()
    runtime_gate = SharedExecutionGate(max_workers, max_queue)
    execution_gate = _AsyncExecutionGate(runtime_gate)

    extensions: list[Any] = []
    if enable_tasks:
        from .tasks import TasksExtension
        from .worker_pool import BoundedWorkerPool

        pool = BoundedWorkerPool(
            service,
            max_workers=max_workers,
            max_queue=max_queue,
            execution_gate=runtime_gate,
            task_store=task_store,
        )
        extensions.append(TasksExtension(pool, caller_id=_CALLER_ID))


    server = MCPServer(
        name=_SERVER_NAME,
        version=_SERVER_VERSION,
        instructions=(
            "Delegates one atomic, proposal-only coding task to a local Ollama "
            "model. Returns a controller-owned result (status, patch, checks, "
            "risks, validation, audit); never applies changes to the workspace."
        ),
        extensions=extensions or None,
    )
    setattr(server, "_delegation_service", service)
    setattr(server, "_codex_service", service)

    @server.resource(
        "model://profile",
        name="Model Profiles & Capabilities",
        description="Catalog of model profiles, context limits, and verified intelligence ladder tiers",
        mime_type="application/json",
    )
    async def get_model_profiles() -> str:
        from pathlib import Path
        from .profiles import list_profiles, get_profile
        items = []
        for name in list_profiles():
            p = get_profile(name)
            items.append({
                "name": p.name,
                "model": p.model,
                "provider": getattr(p, "provider", "ollama"),
                "num_ctx": p.num_ctx,
                "num_predict": p.num_predict,
                "max_context_length": p.max_context_length,
                "think": p.think,
            })
        latest_bench: dict[str, Any] | None = None
        bench_path = Path(".local-run/benchmarks/latest.json")
        if bench_path.is_file():
            try:
                latest_bench = json.loads(bench_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return _json({
            "profiles": items,
            "default_profile": "qwen3-8b-q6k",
            "latest_benchmark": latest_bench,
        })


    @server.tool(
        name="delegate_code",
        description=(
            "Delegate one atomic, proposal-only coding task to a local Ollama "
            "model. The result is controller-owned and never applies changes."
        ),
    )
    async def delegate_code(
        request_id: str,
        workspace_ref: str,
        model_profile: str,
        task: dict[str, Any],
    ) -> CallToolResult:
        request = _delegate_request(request_id, workspace_ref, model_profile, task)
        try:
            result = await execution_gate.run(service.delegate, _CALLER_ID, request)
        except ExecutionOverload:
            result = {
                "status": "failed",
                "error": {"kind": "queue_overload", "message": "bounded execution queue is full"},
                "applied": False,
            }
        text = _json(result)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=result,
            is_error=result.get("status") == "failed",
        )

    if enable_tasks:
        _register_apply_proposal(server, service, execution_gate)

    return server


def _register_apply_proposal(server, service: DelegationService, execution_gate: _AsyncExecutionGate) -> None:
    @server.tool(
        name="apply_proposal",
        description=(
            "Apply a previously accepted proposal to its workspace after explicit "
            "confirmation. Revalidates the patch, applies it, runs allowlisted "
            "checks and rolls back on failure."
        ),
    )
    async def apply_proposal(
        request_id: str,
        workspace_ref: str,
        confirmation: Annotated[ElicitationResult[ApplyConfirmation], Resolve(_resolve_apply)],
    ) -> CallToolResult:
        if isinstance(confirmation, DeclinedElicitation):
            result = {"status": "rejected", "error": {"kind": "apply_declined", "message": "apply was declined"}}
        elif isinstance(confirmation, CancelledElicitation):
            result = {"status": "rejected", "error": {"kind": "apply_cancelled", "message": "apply was cancelled"}}
        elif isinstance(confirmation, AcceptedElicitation) and confirmation.data.confirm is True:
            try:
                preview = _require_apply_preview(
                    service.proposal_preview(_CALLER_ID, workspace_ref, request_id), request_id, workspace_ref
                )
                expected_digest = _proposal_digest(preview)
            except (AttributeError, TypeError, ValueError) as error:
                result = {
                    "status": "failed",
                    "error": {"kind": "apply_confirmation_mismatch", "message": str(error)},
                    "applied": False,
                }
            else:
                if (
                    confirmation.data.proposal_id != request_id
                    or confirmation.data.workspace_ref != workspace_ref
                    or confirmation.data.proposal_digest != expected_digest
                ):
                    result = {
                        "status": "failed",
                        "error": {
                            "kind": "apply_confirmation_mismatch",
                            "message": "confirmation does not match the current proposal preview",
                        },
                        "applied": False,
                    }
                else:
                    try:
                        result = await execution_gate.run(service.apply, _CALLER_ID, workspace_ref, request_id)
                    except ExecutionOverload:
                        result = {
                            "status": "failed",
                            "error": {"kind": "queue_overload", "message": "bounded execution queue is full"},
                            "applied": False,
                        }
        else:
            result = {"status": "rejected", "error": {"kind": "apply_declined", "message": "apply was not confirmed"}}
        text = _json(result)
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=result,
            is_error=result.get("status") == "failed",
        )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the proposal-only MCP stdio server")
    parser.add_argument("--workspace-ref", default="workspace")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--enable-tasks", action="store_true", help="Mount the Tasks extension and apply_proposal")
    args = parser.parse_args(argv)
    service = DelegationService({args.workspace_ref: args.workspace})
    build_server(service, enable_tasks=args.enable_tasks).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
