"""ACP session model."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class AcpSession:
    """An active Agent Client Protocol session."""

    session_id: str
    workspace_path: Path
    profile: str
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cancel_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active_prompt: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
