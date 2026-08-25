"""Mode router: deterministic heuristic classifier and hybrid mode routing.

Routes a user prompt to one of the operational modes: 'chat', 'build' or
'plan'. The fast path is a pure heuristic; the hybrid path optionally consults
an external router (an LLM) on a cadence, in an isolated context, and falls
back to the heuristic when the router is absent, uncertain or fails.
"""

import re
from enum import Enum
from typing import Callable


class ModeName(str, Enum):
    CHAT = "chat"
    BUILD = "build"
    PLAN = "plan"
    HYBRID = "hybrid"


MODES = ("chat", "build", "plan", "hybrid")  # tuple of valid mode strings
VALID_ROUTED = ("chat", "build", "plan")  # modes a classifier can return

# Small-talk / conversational greetings and phrases.
_GREETINGS = {
    "hi", "hello", "hey", "привет", "здравствуйте", "yo", "sup",
    "thanks", "спасибо", "ok", "help", "test",
}
_PHRASES = {
    "how are you", "what's up", "whats up", "wassup", "how are u",
}

# Planning-intent prefixes (explicit phrases) and the Russian verb form
# (substring match is fine for the verb; the noun is matched by token boundary).
_PLAN_PREFIXES = (
    "plan ", "plan:", "спланируй ", "спланируй:", "составь план ", "план для ",
)

# Informational / code-inquiry prefixes.
_INFO_PREFIXES = (
    "read ", "explain ", "what ", "how ", "tell me ", "show ",
    "go to ", "open ", "where is ",
    "опиши ", "прочитай ", "что делает ", "как ", "покажи ", "где ",
)

# Interrogative tokens marking an informational intent anywhere in the prompt
# ("can u tell me what main.py does?"), not only as a prefix.
_QUESTION_TOKENS = frozenset({
    "what", "how", "where", "why", "who", "which",
    "что", "где", "почему", "зачем", "какой", "какая",
})

# Imperative verbs marking an actionable build task even when the prompt also
# contains question wording ("fix how the retry loop counts turns").
_BUILD_VERB_PREFIXES = (
    "fix ", "add ", "write ", "create ", "implement ", "refactor ",
    "update ", "remove ", "delete ", "change ", "test ", "rename ",
    "optimize ", "clean ", "install ", "make ", "исправь ", "добавь ",
)

_TOKEN_STRIP = ".,!?;:'\"()«»"


def classify_fast(prompt: str, current_mode: str | None = None) -> str:
    # ponytail: current_mode kept for backward-compat; unused (mode continuity
    # not implemented). Ignored, not passed to callers.
    """Deterministic heuristic classifier. Never returns 'hybrid'."""
    if not prompt or not prompt.strip():
        return "chat"

    text = prompt.strip().lower()

    # Small-talk / conversational.
    if text in _GREETINGS or text in _PHRASES:
        return "chat"

    # Actionable imperative wins over question wording.
    if text.startswith(_BUILD_VERB_PREFIXES):
        return "build"

    tokens = [tok.strip(_TOKEN_STRIP) for tok in text.split()]
    is_question = text.endswith("?") or any(tok in _QUESTION_TOKENS for tok in tokens)

    # Informational / code inquiry takes precedence over planning intent so
    # that e.g. "explain the deployment plan" reads as informational.
    if text.startswith(_INFO_PREFIXES) or is_question:
        return "chat"

    # Planning intent. English "plan" matched at word boundaries (so
    # "planner"/"airplane" don't match); Russian noun "план" as a standalone
    # token; verb "планируй" as a substring.
    if (
        text.startswith(_PLAN_PREFIXES)
        or re.search(r"\bplan\b", text)
        or "планируй" in text
        or "план" in text.split()
    ):
        return "plan"

    return "build"


def classify_mode(
    prompt: str,
    *,
    current_mode: str | None = None,
    router=None,
    n_every: int = 3,
    counter: int = 0,
    recent_prompts: list[str] | None = None,
) -> str:
    # ponytail: current_mode threaded through for backward-compat; unused
    # (mode continuity not implemented).
    """Route a prompt, optionally consulting an external router on a cadence."""
    if router is None or not callable(router):
        return classify_fast(prompt, current_mode)

    n_every = max(1, n_every)
    counter = max(0, counter)

    if counter % n_every == 0:
        try:
            result = router(recent_prompts)
        except Exception:
            result = None
        if result in VALID_ROUTED:
            return result

    return classify_fast(prompt, current_mode)


DEFAULT_MODE_ROUTER_PROFILE = "qwen2.5-1.5b"

# Extremely short system prompt: small models comply best with one clear word.
_ROUTER_SYSTEM = (
    "Classify the user's intent. Reply with exactly one word: chat, build, or plan."
)


def build_mode_router(
    profile_name: str = DEFAULT_MODE_ROUTER_PROFILE, *, client=None
) -> Callable[[list[str] | None], str | None]:
    """Return a router callable for classify_mode's `router` parameter.

    The returned callable takes an optional list of recent user prompts (the
    isolated context) and returns one of 'chat'|'build'|'plan', or None if it
    cannot decide or anything fails. It consults a small local model in an
    ISOLATED context (a fresh single-turn completion with no workspace or
    task context, just the classification task). It never runs the Controller
    and never writes to disk.

    - If `client` is provided, use it directly (for tests/hermetic use).
    - Otherwise build one via build_client(get_profile(profile_name))
      imported lazily inside the factory so this module has no heavy
      dependencies at import time.
    - The completion uses a strict system prompt instructing the model to
      reply with exactly one word: chat, build, or plan.
    - Parse the reply: lowercase, take the first token that matches one of
      VALID_ROUTED; if none matches, return None.
    - Wrap everything in try/except; on ANY exception return None (never raise).
    """
    if client is None:
        # Lazily import the backend so importing mode_router stays dependency-light.
        from local_coding_agent.ollama_adapter import build_client
        from local_coding_agent.profiles import get_profile

        client = build_client(get_profile(profile_name))

    def router(recent_prompts: list[str] | None = None) -> str | None:
        try:
            messages: list[dict[str, object]] = [
                {"role": "system", "content": _ROUTER_SYSTEM}
            ]
            user_content = "Recent messages:\n" + "\n".join(
                f"- {p}" for p in (recent_prompts or [])
            )
            if not user_content.strip():
                user_content = "No recent context."
            messages.append({"role": "user", "content": user_content})

            reply = client.chat(messages)
            content = (reply or {}).get("message", {}).get("content") or ""
            for token in content.lower().split():
                tok = token.strip(".,!?;:")
                if tok in VALID_ROUTED:
                    return tok
            return None
        except Exception:
            return None

    return router
