"""External agent hook bridges and protocol adapters (R30).

Provides lifecycle hook interception points (on_pre_tool_call, on_post_tool_call,
on_turn_start, on_turn_end, on_session_finish) and wire-protocol adapters compatible
with Codex and Claude Code hook standards.
"""

from __future__ import annotations

from ._adapters import ClaudeCodeHookAdapter, CodexHookAdapter
from ._bridge import HookBridge
from ._decision import HookDecision

__all__ = [
    "ClaudeCodeHookAdapter",
    "CodexHookAdapter",
    "HookBridge",
    "HookDecision",
]
