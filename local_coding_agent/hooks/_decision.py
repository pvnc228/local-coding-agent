"""Hook decision model (R30)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HookDecision:
    """Consolidated outcome of hook execution at an interception point."""

    decision: str = "allow"  # "allow", "deny", "ask", "approve", "block"
    allowed: bool = True
    reason: str | None = None
    additional_context: list[str] = field(default_factory=list)
    feedback: list[str] = field(default_factory=list)
    system_messages: list[str] = field(default_factory=list)
    updated_input: dict[str, Any] | None = None
    stop: bool = False
    stop_reason: str | None = None
    raw_outputs: list[dict[str, Any]] = field(default_factory=list)

    def is_blocking(self) -> bool:
        """True if the hook explicitly denied or blocked the operation."""
        return not self.allowed or self.decision in ("deny", "block")
