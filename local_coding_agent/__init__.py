"""Bounded controller components for delegating small coding tasks."""

__version__ = "1.0.0"


from .acp_server import (
    ACP_TOOLS,
    AcpCodec,
    AcpServer,
    AcpSession,
)
from .ast_compactor import skeletonize_file, skeletonize_python
from .atomizer import Decomposition, PreflightReport, TaskBudget, decompose, preflight

from .calibration import calibrate_for_model, calibrate_workers, model_vram_bytes
from .context_manager import (
    ContextAssembler,
    HarnessState,
    compact_tool_exchanges,
    purge_diff_residues,
)
from .controller import Controller
from .delegator import DelegatingAgent, DecompositionTemplate, is_decomposable_failure
from .doctor import (
    CheckResult,
    DoctorFixReport,
    DoctorReport,
    diagnose_environment,
    remediate_environment,
)
from .mcp_config import generate_mcp_config_dict, get_client_config_path, integrate_mcp_config
from .mcp_server import build_server
from .memory import LoadedModel, MemoryBudgetError, MemorySnapshot, ModelMemoryManager
from .mode_router import (
    DEFAULT_MODE_ROUTER_PROFILE,
    MODES,
    VALID_ROUTED,
    ModeName,
    build_mode_router,
    classify_fast,
    classify_mode,
)
from .monitor import MonitorServer
from .lsp import (
    FallbackLspEngine,
    LspClient,
    LspConnectionError,
    LspError,
    LspHoverResult,
    LspLocation,
    LspManager,
    LspPosition,
    LspRange,
    LspResponseError,
    LspSymbol,
    LspTimeoutError,
    MockLspServer,
)
from .observation_policy import (
    FsObservationError,
    FsObservationGate,
    is_observed,
    observe_file,
    reset_session,
    verify_edit_intent,
)
from .plan_mode import (
    AskUserQuestionTool,
    PlanArtifact,
    PlanModeController,
    PlanModeError,
    PlanModePolicyError,
    PlanModeState,
    QuestionAnswer,
    QuestionItem,
    TodoChecklist,
    TodoItem,
    TodoStatus,
)
from .ollama_adapter import (
    ModelProfile,
    OllamaClient,
    OllamaError,
    OpenAICompatibleClient,
    build_client,
)
from .ripgrep import RipgrepMatch, ripgrep_files, ripgrep_search
from .semantic_linter import (
    LinterDiagnostic,
    LinterReport,
    lint_patch_in_memory,
    lint_source_code,
)
from .service import DelegationRequest, DelegationService
from .smoke import run_smoke_test
from .speculative_racing import SpeculativeRacer
from .spill import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    SpillRef,
    SpillStore,
    maybe_spill,
    read_spill,
    save_text,
)
from .stats import DelegationStats, JsonlStatsSink, TimedDelegationStats
from .stdio import StdioDelegationAdapter
from .hooks import (
    ClaudeCodeHookAdapter,
    CodexHookAdapter,
    HookBridge,
    HookDecision,
)
from .subagent import (
    MailboxMessage,
    SubagentContext,
    SubagentCoordinator,
    SubagentReport,
)
from .task import TaskEnvelope
from .task_store import JsonFileTaskStore, TaskRecord, TaskStore
from .terminal import (
    TerminalError,
    TerminalManager,
    TerminalProcessExitedError,
    TerminalSession,
    TerminalSessionExistsError,
    TerminalSessionInfo,
    TerminalSessionNotFoundError,
    TerminalTimeoutError,
    execute_terminal_tool,
    get_terminal_tool_schemas,
    kill_process_tree,
)
from .validators import ValidationReport, validate_candidate
from .worker_pool import BoundedWorkerPool
from .session_events import (
    ModelTurnEvent,
    PrescriptionEvent,
    SessionCompletedEvent,
    SessionCreatedEvent,
    SessionEvent,
    SessionLog,
    ToolCallEvent,
    ToolResultEvent,
    UserPromptEvent,
    derive_messages,
    event_from_dict,
    event_to_dict,
    fork_session,
)
from .session_query import (
    SessionQueryEngine,
    get_session_trace,
    sanitize_fts5_query,
    search_events,
    search_sessions,
)

try:
    from .tasks import TASKS_IDENTIFIER, TasksExtension
except ImportError:  # pragma: no cover - mcp is an optional dependency
    TasksExtension = None  # type: ignore[assignment]
    TASKS_IDENTIFIER = "io.modelcontextprotocol/tasks"

__all__ = [
    "Controller",
    "DelegationRequest",
    "DelegationService",
    "DelegationStats",
    "JsonlStatsSink",
    "TimedDelegationStats",
    "BoundedWorkerPool",
    "StdioDelegationAdapter",
    "DelegatingAgent",
    "DecompositionTemplate",
    "is_decomposable_failure",
    "Decomposition",
    "PreflightReport",
    "TaskBudget",
    "decompose",
    "preflight",
    "calibrate_for_model",
    "calibrate_workers",
    "model_vram_bytes",
    "LoadedModel",
    "MemoryBudgetError",
    "MemorySnapshot",
    "ModelMemoryManager",
    "ModeName",
    "MODES",
    "VALID_ROUTED",
    "build_mode_router",
    "DEFAULT_MODE_ROUTER_PROFILE",
    "classify_fast",
    "classify_mode",
    "ModelProfile",
    "OllamaClient",
    "OllamaError",
    "OpenAICompatibleClient",
    "build_client",
    "build_server",
    "TaskEnvelope",
    "ValidationReport",
    "validate_candidate",
    "TasksExtension",
    "TASKS_IDENTIFIER",
    "diagnose_environment",
    "DoctorReport",
    "DoctorFixReport",
    "remediate_environment",
    "CheckResult",
    "generate_mcp_config_dict",
    "get_client_config_path",
    "integrate_mcp_config",
    "run_smoke_test",
    "MonitorServer",
    "TaskStore",
    "TaskRecord",
    "JsonFileTaskStore",
    "HarnessState",
    "ContextAssembler",
    "compact_tool_exchanges",
    "purge_diff_residues",
    "skeletonize_python",
    "skeletonize_file",
    "LinterDiagnostic",
    "LinterReport",
    "lint_source_code",
    "lint_patch_in_memory",
    "SpeculativeRacer",
    "FsObservationError",
    "FsObservationGate",
    "observe_file",
    "is_observed",
    "verify_edit_intent",
    "reset_session",
    "RipgrepMatch",
    "ripgrep_search",
    "ripgrep_files",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "SpillRef",
    "SpillStore",
    "save_text",
    "read_spill",
    "maybe_spill",
    "LspPosition",
    "LspRange",
    "LspLocation",
    "LspHoverResult",
    "LspSymbol",
    "LspClient",
    "MockLspServer",
    "FallbackLspEngine",
    "LspManager",
    "LspError",
    "LspTimeoutError",
    "LspConnectionError",
    "LspResponseError",
    "TerminalSession",
    "TerminalSessionInfo",
    "TerminalManager",
    "TerminalError",
    "TerminalSessionNotFoundError",
    "TerminalSessionExistsError",
    "TerminalProcessExitedError",
    "TerminalTimeoutError",
    "get_terminal_tool_schemas",
    "execute_terminal_tool",
    "kill_process_tree",
    "PlanModeState",
    "PlanArtifact",
    "PlanModeController",
    "PlanModeError",
    "PlanModePolicyError",
    "QuestionItem",
    "QuestionAnswer",
    "AskUserQuestionTool",
    "TodoStatus",
    "TodoItem",
    "TodoChecklist",
    "HookBridge",
    "HookDecision",
    "CodexHookAdapter",
    "ClaudeCodeHookAdapter",
    "SubagentCoordinator",
    "SubagentContext",
    "MailboxMessage",
    "SubagentReport",
    "AcpServer",
    "AcpSession",
    "AcpCodec",
    "ACP_TOOLS",
    "SessionCreatedEvent",
    "UserPromptEvent",
    "ModelTurnEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "PrescriptionEvent",
    "SessionCompletedEvent",
    "SessionEvent",
    "SessionLog",
    "derive_messages",
    "fork_session",
    "event_to_dict",
    "event_from_dict",
    "SessionQueryEngine",
    "search_sessions",
    "search_events",
    "get_session_trace",
    "sanitize_fts5_query",
]
