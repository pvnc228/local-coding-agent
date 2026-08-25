"""Event-Sourced Session Engine.

Implements typed immutable session events, append-only monotonic event logging,
model-visible message derivation (Model-Visible ⟺ Logged invariant),
and session branching/forking.

Adapted from DeepSeek Harness @deepseek-ai/dsh-session and @deepseek-ai/dsh-session-persistence-jsonl.
"""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
import threading
from typing import Any, Mapping, Sequence, Union


def _utc_now_iso() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Typed Immutable Session Events
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionCreatedEvent:
    """Emitted when a new session is initialized or branched from a parent."""

    session_id: str
    seq: int
    timestamp: str
    parent_session_id: str | None = None
    fork_seq: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "session_created"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UserPromptEvent:
    """Emitted when a user submits a prompt or goal."""

    session_id: str
    seq: int
    timestamp: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "user_prompt"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelTurnEvent:
    """Emitted when the language model completes an assistant turn."""

    session_id: str
    seq: int
    timestamp: str
    content: str = ""
    model: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "model_turn"

    def __post_init__(self) -> None:
        if isinstance(self.tool_calls, list):
            object.__setattr__(self, "tool_calls", tuple(self.tool_calls))

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tool_calls"] = [dict(tc) for tc in self.tool_calls]
        return data


@dataclass(frozen=True)
class ToolCallEvent:
    """Emitted when a specific tool is invoked during a turn."""

    session_id: str
    seq: int
    timestamp: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "tool_call"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolResultEvent:
    """Emitted when a tool invocation completes and produces output or error."""

    session_id: str
    seq: int
    timestamp: str
    tool_call_id: str
    tool_name: str
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "tool_result"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PrescriptionEvent:
    """Emitted when the controller prescribes a deterministic correction to the model."""

    session_id: str
    seq: int
    timestamp: str
    kind: str
    instruction: str
    details: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "prescription"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionCompletedEvent:
    """Emitted when a session reaches terminal completion."""

    session_id: str
    seq: int
    timestamp: str
    status: str  # "success", "failed", "cancelled", "interrupted"
    summary: str = ""
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    event_type: str = "session_completed"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


SessionEvent = Union[
    SessionCreatedEvent,
    UserPromptEvent,
    ModelTurnEvent,
    ToolCallEvent,
    ToolResultEvent,
    PrescriptionEvent,
    SessionCompletedEvent,
]

_EVENT_TYPE_MAP: dict[str, type[SessionEvent]] = {
    "session_created": SessionCreatedEvent,
    "user_prompt": UserPromptEvent,
    "model_turn": ModelTurnEvent,
    "tool_call": ToolCallEvent,
    "tool_result": ToolResultEvent,
    "prescription": PrescriptionEvent,
    "session_completed": SessionCompletedEvent,
}


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    """Convert any SessionEvent to a JSON-serializable dictionary."""
    return event.as_dict()


def event_from_dict(data: Mapping[str, Any]) -> SessionEvent:
    """Reconstruct a typed SessionEvent from dictionary payload."""
    event_type = data.get("event_type")
    if not event_type or event_type not in _EVENT_TYPE_MAP:
        raise ValueError(f"Unknown or missing event_type in data: {event_type!r}")

    cls = _EVENT_TYPE_MAP[event_type]
    session_id = str(data["session_id"])
    seq = int(data["seq"])
    timestamp = str(data.get("timestamp") or _utc_now_iso())
    metadata = dict(data.get("metadata", {}))

    if cls is SessionCreatedEvent:
        return SessionCreatedEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            parent_session_id=data.get("parent_session_id"),
            fork_seq=data.get("fork_seq"),
            metadata=metadata,
        )
    elif cls is UserPromptEvent:
        return UserPromptEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            content=str(data.get("content", "")),
            metadata=metadata,
        )
    elif cls is ModelTurnEvent:
        raw_tc = data.get("tool_calls", ())
        tool_calls = tuple(dict(tc) for tc in raw_tc) if isinstance(raw_tc, (list, tuple)) else ()
        return ModelTurnEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            content=str(data.get("content", "")),
            model=str(data.get("model", "")),
            tool_calls=tool_calls,
            metadata=metadata,
        )
    elif cls is ToolCallEvent:
        return ToolCallEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            tool_call_id=str(data["tool_call_id"]),
            tool_name=str(data["tool_name"]),
            arguments=dict(data.get("arguments", {})),
            metadata=metadata,
        )
    elif cls is ToolResultEvent:
        return ToolResultEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            tool_call_id=str(data["tool_call_id"]),
            tool_name=str(data["tool_name"]),
            result=data.get("result"),
            error=data.get("error"),
            metadata=metadata,
        )
    elif cls is PrescriptionEvent:
        return PrescriptionEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            kind=str(data.get("kind", "")),
            instruction=str(data.get("instruction", "")),
            details=dict(data.get("details", {})),
            metadata=metadata,
        )
    elif cls is SessionCompletedEvent:
        res = data.get("result")
        return SessionCompletedEvent(
            session_id=session_id,
            seq=seq,
            timestamp=timestamp,
            status=str(data.get("status", "unknown")),
            summary=str(data.get("summary", "")),
            result=dict(res) if isinstance(res, Mapping) else None,
            metadata=metadata,
        )

    raise ValueError(f"Unsupported event type class for: {event_type}")


# -----------------------------------------------------------------------------
# Model-Visible ⟺ Logged Invariant: Message Derivation
# -----------------------------------------------------------------------------


def derive_messages(events: Sequence[SessionEvent]) -> list[dict[str, Any]]:
    """Derive standard LLM messages array strictly from recorded session events.

    Enforces the core architectural invariant: Model-Visible ⟺ Logged.
    Anything that reaches a model request must be reconstructable from the log.
    """
    messages: list[dict[str, Any]] = []

    for event in events:
        if isinstance(event, UserPromptEvent):
            messages.append({"role": "user", "content": event.content})

        elif isinstance(event, ModelTurnEvent):
            msg: dict[str, Any] = {"role": "assistant"}
            if event.content:
                msg["content"] = event.content
            else:
                msg["content"] = ""
            if event.tool_calls:
                msg["tool_calls"] = [dict(tc) for tc in event.tool_calls]
            messages.append(msg)

        elif isinstance(event, ToolResultEvent):
            if isinstance(event.result, str):
                content = event.result
            elif event.result is not None:
                content = json.dumps(event.result, ensure_ascii=False)
            else:
                content = ""

            if event.error:
                content = f"Error: {event.error}\n{content}".strip()

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.tool_call_id,
                    "name": event.tool_name,
                    "content": content,
                }
            )

        elif isinstance(event, PrescriptionEvent):
            # Prescriptions are pinpointed corrective feedback rendered as a model-visible user message
            messages.append(
                {
                    "role": "user",
                    "content": f"[PRESCRIPTION: {event.kind}] {event.instruction}",
                }
            )

    return messages


# -----------------------------------------------------------------------------
# Append-Only Session Log Container & Persistence
# -----------------------------------------------------------------------------


class SessionLog:
    """Thread-safe append-only session event log with JSONL persistence and monotonic sequencing."""

    def __init__(
        self,
        session_id: str,
        *,
        storage_dir: str | Path | None = None,
        log_path: str | Path | None = None,
    ) -> None:
        self.session_id = session_id
        self._lock = threading.RLock()
        self._events: list[SessionEvent] = []

        if log_path is not None:
            self._log_path: Path | None = Path(log_path)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
        elif storage_dir is not None:
            dir_path = Path(storage_dir)
            dir_path.mkdir(parents=True, exist_ok=True)
            self._log_path = dir_path / f"{session_id}.jsonl"
        else:
            self._log_path = None

    @property
    def log_path(self) -> Path | None:
        return self._log_path

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        with self._lock:
            return tuple(self._events)

    @property
    def last_event(self) -> SessionEvent | None:
        with self._lock:
            return self._events[-1] if self._events else None

    @property
    def parent_session_id(self) -> str | None:
        with self._lock:
            if self._events and isinstance(self._events[0], SessionCreatedEvent):
                return self._events[0].parent_session_id
            return None

    @property
    def fork_seq(self) -> int | None:
        with self._lock:
            if self._events and isinstance(self._events[0], SessionCreatedEvent):
                return self._events[0].fork_seq
            return None

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def __iter__(self):
        with self._lock:
            return iter(list(self._events))

    def __getitem__(self, index: int) -> SessionEvent:
        with self._lock:
            return self._events[index]

    def get_event(self, seq: int) -> SessionEvent | None:
        with self._lock:
            if 0 <= seq < len(self._events):
                return self._events[seq]
            return None

    def append(self, event: SessionEvent) -> SessionEvent:
        """Append an event to the log, enforcing monotonic sequencing."""
        with self._lock:
            if event.session_id != self.session_id:
                raise ValueError(
                    f"Event session_id mismatch: expected {self.session_id!r}, got {event.session_id!r}"
                )

            expected_seq = len(self._events)
            if event.seq != expected_seq:
                raise ValueError(
                    f"Non-monotonic event sequence: expected seq={expected_seq}, got seq={event.seq}"
                )

            self._events.append(event)

            if self._log_path is not None:
                self._persist_event(event)

            return event

    def _persist_event(self, event: SessionEvent) -> None:
        """Write single event line to JSONL file."""
        if self._log_path is None:
            return
        line = json.dumps(event_to_dict(event), ensure_ascii=False) + "\n"
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()

    # --- Convenience Recording Methods ---

    def record_created(
        self,
        parent_session_id: str | None = None,
        fork_seq: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionCreatedEvent:
        with self._lock:
            event = SessionCreatedEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                parent_session_id=parent_session_id,
                fork_seq=fork_seq,
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_user_prompt(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> UserPromptEvent:
        with self._lock:
            event = UserPromptEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                content=content,
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_model_turn(
        self,
        content: str = "",
        model: str = "",
        tool_calls: Sequence[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModelTurnEvent:
        with self._lock:
            event = ModelTurnEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                content=content,
                model=model,
                tool_calls=tuple(tool_calls or ()),
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_tool_call(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ToolCallEvent:
        with self._lock:
            event = ToolCallEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=dict(arguments),
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResultEvent:
        with self._lock:
            event = ToolResultEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=result,
                error=error,
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_prescription(
        self,
        kind: str,
        instruction: str,
        details: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PrescriptionEvent:
        with self._lock:
            event = PrescriptionEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                kind=kind,
                instruction=instruction,
                details=details or {},
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def record_completed(
        self,
        status: str,
        summary: str = "",
        result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionCompletedEvent:
        with self._lock:
            event = SessionCompletedEvent(
                session_id=self.session_id,
                seq=len(self._events),
                timestamp=_utc_now_iso(),
                status=status,
                summary=summary,
                result=result,
                metadata=metadata or {},
            )
            self.append(event)
            return event

    def to_jsonl(self) -> str:
        """Render all events as formatted JSONL string."""
        with self._lock:
            return "".join(
                json.dumps(event_to_dict(ev), ensure_ascii=False) + "\n"
                for ev in self._events
            )

    def save(self, path: str | Path | None = None) -> Path:
        """Write entire session log to file."""
        target = Path(path) if path is not None else self._log_path
        if target is None:
            raise ValueError("No path specified and no log_path configured for session log")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            content = self.to_jsonl()
            temp_file = target.with_suffix(".tmp")
            temp_file.write_text(content, encoding="utf-8")
            temp_file.replace(target)
            self._log_path = target
        return target

    @classmethod
    def load_from_jsonl(cls, path: str | Path) -> SessionLog:
        """Load and reconstruct a SessionLog from an existing JSONL file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Session log file not found: {file_path}")

        lines = file_path.read_text(encoding="utf-8").strip().splitlines()
        if not lines:
            raise ValueError(f"Session log file is empty: {file_path}")

        first_data = json.loads(lines[0])
        session_id = str(first_data["session_id"])
        log = cls(session_id=session_id, log_path=file_path)

        for line_num, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            event = event_from_dict(data)
            if event.session_id != session_id:
                raise ValueError(
                    f"Line {line_num} has unexpected session_id: expected {session_id}, got {event.session_id}"
                )
            if event.seq != len(log._events):
                raise ValueError(
                    f"Line {line_num} violates sequence monotonicity: expected {len(log._events)}, got {event.seq}"
                )
            log._events.append(event)

        return log


# -----------------------------------------------------------------------------
# Session Forking & Branching
# -----------------------------------------------------------------------------


def fork_session(
    original_session_id: str,
    step_index: int,
    new_session_id: str,
    storage_dir: str | Path | None = None,
    original_log: SessionLog | None = None,
) -> SessionLog:
    """Fork an existing session up to step_index into a new independent session.

    Reconstructs history up to step_index under new_session_id with a SessionCreatedEvent
    recording lineage (parent_session_id and fork_seq).
    """
    if step_index < 0:
        raise ValueError(f"step_index must be non-negative, got {step_index}")

    source_log = original_log
    if source_log is None and storage_dir is not None:
        src_path = Path(storage_dir) / f"{original_session_id}.jsonl"
        if src_path.exists():
            source_log = SessionLog.load_from_jsonl(src_path)

    if source_log is None:
        raise ValueError(
            f"Original session {original_session_id!r} could not be resolved from storage_dir or memory"
        )

    sliced_events = [ev for ev in source_log.events if ev.seq <= step_index]
    if not sliced_events:
        raise ValueError(f"No events found in original session up to step_index {step_index}")

    new_log = SessionLog(session_id=new_session_id, storage_dir=storage_dir)

    # 1. Record SessionCreatedEvent pointing to parent
    new_log.record_created(
        parent_session_id=original_session_id,
        fork_seq=step_index,
        metadata={"forked_at": _utc_now_iso(), "source_events_count": len(sliced_events)},
    )

    # 2. Replay all subsequent events up to step_index
    for ev in sliced_events:
        # Skip the original session_created event if it was at seq 0
        if isinstance(ev, SessionCreatedEvent) and ev.seq == 0:
            continue

        raw = ev.as_dict()
        raw["session_id"] = new_session_id
        raw["seq"] = len(new_log)
        reconstructed = event_from_dict(raw)
        new_log.append(reconstructed)

    if new_log.log_path is not None:
        new_log.save()

    return new_log
