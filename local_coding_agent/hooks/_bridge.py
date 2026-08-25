"""Lifecycle hook bridge: registry and dispatcher (R30)."""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

from ._decision import HookDecision


@dataclass
class _HookRegistration:
    hook_id: str
    point: str
    handler: Callable[..., Any]
    matcher: str | None
    priority: int = 0


class HookBridge:
    """Central registry and dispatcher for lifecycle hooks."""

    POINT_PRE_TOOL_CALL = "on_pre_tool_call"
    POINT_POST_TOOL_CALL = "on_post_tool_call"
    POINT_TURN_START = "on_turn_start"
    POINT_TURN_END = "on_turn_end"
    POINT_SESSION_FINISH = "on_session_finish"

    _POINT_ALIASES = {
        "PreToolUse": POINT_PRE_TOOL_CALL,
        "pre_tool_call": POINT_PRE_TOOL_CALL,
        "on_pre_tool_call": POINT_PRE_TOOL_CALL,
        "PostToolUse": POINT_POST_TOOL_CALL,
        "post_tool_call": POINT_POST_TOOL_CALL,
        "on_post_tool_call": POINT_POST_TOOL_CALL,
        "UserPromptSubmit": POINT_TURN_START,
        "turn_start": POINT_TURN_START,
        "on_turn_start": POINT_TURN_START,
        "TurnEnd": POINT_TURN_END,
        "turn_end": POINT_TURN_END,
        "on_turn_end": POINT_TURN_END,
        "SessionStart": "on_session_start",
        "session_start": "on_session_start",
        "on_session_start": "on_session_start",
        "Stop": POINT_SESSION_FINISH,
        "session_finish": POINT_SESSION_FINISH,
        "on_session_finish": POINT_SESSION_FINISH,
        "SubagentStart": "on_subagent_start",
        "SubagentStop": "on_subagent_stop",
    }

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hooks: dict[str, list[_HookRegistration]] = {}

    def _normalize_point(self, point: str) -> str:
        return self._POINT_ALIASES.get(point, point)

    def register(
        self,
        point: str,
        handler: Callable[..., Any],
        *,
        matcher: str | None = None,
        priority: int = 0,
    ) -> str:
        """Register a hook handler for a lifecycle point."""
        norm_point = self._normalize_point(point)
        hook_id = f"hook-{uuid.uuid4().hex[:8]}"
        reg = _HookRegistration(
            hook_id=hook_id,
            point=norm_point,
            handler=handler,
            matcher=matcher,
            priority=priority,
        )
        with self._lock:
            if norm_point not in self._hooks:
                self._hooks[norm_point] = []
            self._hooks[norm_point].append(reg)
            self._hooks[norm_point].sort(key=lambda h: h.priority, reverse=True)
        return hook_id

    def unregister(self, hook_id: str) -> bool:
        """Unregister a hook handler by its ID."""
        with self._lock:
            for point, reg_list in self._hooks.items():
                for idx, reg in enumerate(reg_list):
                    if reg.hook_id == hook_id:
                        reg_list.pop(idx)
                        return True
        return False

    def on_pre_tool_call(self, matcher: str | None = None, priority: int = 0) -> Callable:
        """Decorator for registering pre-tool-call hooks."""
        def decorator(fn: Callable) -> Callable:
            self.register(self.POINT_PRE_TOOL_CALL, fn, matcher=matcher, priority=priority)
            return fn
        return decorator

    def on_post_tool_call(self, matcher: str | None = None, priority: int = 0) -> Callable:
        """Decorator for registering post-tool-call hooks."""
        def decorator(fn: Callable) -> Callable:
            self.register(self.POINT_POST_TOOL_CALL, fn, matcher=matcher, priority=priority)
            return fn
        return decorator

    def on_turn_start(self, matcher: str | None = None, priority: int = 0) -> Callable:
        """Decorator for registering turn-start hooks."""
        def decorator(fn: Callable) -> Callable:
            self.register(self.POINT_TURN_START, fn, matcher=matcher, priority=priority)
            return fn
        return decorator

    def on_turn_end(self, matcher: str | None = None, priority: int = 0) -> Callable:
        """Decorator for registering turn-end hooks."""
        def decorator(fn: Callable) -> Callable:
            self.register(self.POINT_TURN_END, fn, matcher=matcher, priority=priority)
            return fn
        return decorator

    def on_session_finish(self, priority: int = 0) -> Callable:
        """Decorator for registering session-finish hooks."""
        def decorator(fn: Callable) -> Callable:
            self.register(self.POINT_SESSION_FINISH, fn, priority=priority)
            return fn
        return decorator

    def _matches(self, pattern: str | None, query: str) -> bool:
        if pattern is None or pattern == "" or pattern == "*":
            return True
        try:
            return bool(re.search(pattern, query))
        except re.error:
            return pattern == query

    def trigger(
        self,
        point: str,
        match_target: str = "",
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> HookDecision:
        """Execute all matching hooks for a given point and combine decisions."""
        norm_point = self._normalize_point(point)
        with self._lock:
            candidates = list(self._hooks.get(norm_point, []))

        decision = HookDecision()
        merged_payload = dict(payload or {})
        merged_payload.update(kwargs)

        for reg in candidates:
            if not self._matches(reg.matcher, match_target):
                continue

            try:
                import inspect
                sig = inspect.signature(reg.handler)
                param_count = len([
                    p for p in sig.parameters.values()
                    if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                ])
                var_pos = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
                if var_pos or param_count >= 2:
                    out = reg.handler(match_target, merged_payload)
                elif param_count == 1:
                    out = reg.handler(merged_payload)
                else:
                    out = reg.handler()
            except Exception as exc:
                out = {"decision": "error", "reason": f"hook handler error: {exc}"}

            self._fold_outcome(decision, out)
            if decision.is_blocking():
                # Short-circuit on blocking decision
                break

        return decision

    def trigger_pre_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> HookDecision:
        """Convenience method for on_pre_tool_call point."""
        payload = {
            "tool_name": tool_name,
            "tool_input": arguments,
            "context": context or {},
        }
        return self.trigger(self.POINT_PRE_TOOL_CALL, match_target=tool_name, payload=payload)

    def trigger_post_tool_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> HookDecision:
        """Convenience method for on_post_tool_call point."""
        payload = {
            "tool_name": tool_name,
            "tool_input": arguments,
            "tool_output": result,
            "context": context or {},
        }
        return self.trigger(self.POINT_POST_TOOL_CALL, match_target=tool_name, payload=payload)

    def trigger_turn_start(
        self,
        turn: int,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> HookDecision:
        """Convenience method for on_turn_start point."""
        payload = {
            "turn": turn,
            "turn_id": str(turn),
            "prompt": prompt,
            "context": context or {},
        }
        return self.trigger(self.POINT_TURN_START, match_target="", payload=payload)

    def trigger_turn_end(
        self,
        turn: int,
        response: Any,
        context: dict[str, Any] | None = None,
    ) -> HookDecision:
        """Convenience method for on_turn_end point."""
        payload = {
            "turn": turn,
            "turn_id": str(turn),
            "response": response,
            "context": context or {},
        }
        return self.trigger(self.POINT_TURN_END, match_target="", payload=payload)

    def trigger_session_finish(
        self,
        session_id: str,
        summary: str = "",
        status: str = "completed",
        context: dict[str, Any] | None = None,
    ) -> HookDecision:
        """Convenience method for on_session_finish point."""
        payload = {
            "session_id": session_id,
            "summary": summary,
            "status": status,
            "context": context or {},
        }
        return self.trigger(self.POINT_SESSION_FINISH, match_target="", payload=payload)

    def _fold_outcome(self, decision: HookDecision, outcome: Any) -> None:
        """Fold an individual hook outcome into the aggregate HookDecision."""
        if outcome is None:
            return

        if isinstance(outcome, HookDecision):
            raw = asdict(outcome) if hasattr(outcome, "__dataclass_fields__") else {}
            if outcome.is_blocking():
                decision.decision = outcome.decision
                decision.allowed = False
                decision.reason = outcome.reason or decision.reason
            if outcome.additional_context:
                decision.additional_context.extend(outcome.additional_context)
            if outcome.feedback:
                decision.feedback.extend(outcome.feedback)
            if outcome.system_messages:
                decision.system_messages.extend(outcome.system_messages)
            if outcome.updated_input:
                decision.updated_input = outcome.updated_input
            if outcome.stop:
                decision.stop = True
                decision.stop_reason = outcome.stop_reason or decision.stop_reason
            decision.raw_outputs.append(raw)
            return

        if isinstance(outcome, dict):
            decision.raw_outputs.append(outcome)
            # Permission / blocking decision check
            dec = outcome.get("decision")
            perm_dec = (
                outcome.get("hookSpecificOutput", {}).get("permissionDecision")
                if isinstance(outcome.get("hookSpecificOutput"), dict)
                else None
            )
            selected_dec = perm_dec or dec

            if selected_dec in ("deny", "block", "error"):
                decision.decision = selected_dec
                decision.allowed = False
                reason = outcome.get("reason") or (
                    outcome.get("hookSpecificOutput", {}).get("reason")
                    if isinstance(outcome.get("hookSpecificOutput"), dict)
                    else None
                )
                if reason:
                    decision.reason = str(reason)
            elif selected_dec in ("allow", "approve", "ask"):
                if not decision.is_blocking():
                    decision.decision = selected_dec

            # Additional context
            ctx = outcome.get("additionalContext") or outcome.get("additional_context")
            if not ctx and isinstance(outcome.get("hookSpecificOutput"), dict):
                ctx = outcome.get("hookSpecificOutput", {}).get("additionalContext")
            if isinstance(ctx, str) and ctx.strip():
                decision.additional_context.append(ctx.strip())
            elif isinstance(ctx, list):
                decision.additional_context.extend([str(c) for c in ctx if c])

            # Feedback
            fb = outcome.get("feedback")
            if not fb and isinstance(outcome.get("hookSpecificOutput"), dict):
                fb = outcome.get("hookSpecificOutput", {}).get("feedback")
            if isinstance(fb, str) and fb.strip():
                decision.feedback.append(fb.strip())
            elif isinstance(fb, list):
                decision.feedback.extend([str(f) for f in fb if f])

            # System message
            sys_msg = outcome.get("systemMessage") or outcome.get("system_message")
            if not sys_msg and isinstance(outcome.get("hookSpecificOutput"), dict):
                sys_msg = outcome.get("hookSpecificOutput", {}).get("systemMessage")
            if isinstance(sys_msg, str) and sys_msg.strip():
                decision.system_messages.append(sys_msg.strip())

            # Updated input
            updated = outcome.get("updatedInput") or outcome.get("updated_input")
            if not updated and isinstance(outcome.get("hookSpecificOutput"), dict):
                updated = outcome.get("hookSpecificOutput", {}).get("updatedInput")
            if isinstance(updated, dict):
                decision.updated_input = updated

            # Stop
            cont = outcome.get("continue")
            if cont is False:
                decision.stop = True
                decision.stop_reason = outcome.get("stopReason") or outcome.get("stop_reason")
