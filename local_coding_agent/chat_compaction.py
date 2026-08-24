"""Rolling context window: compaction of long chat histories for bounded models.

Local models run with a fixed ``num_ctx``; a long multi-turn session eventually
produces a prompt larger than that window and the request dies.  This module
implements the borrowed "rolling window" pattern:

- Evict WHOLE turns oldest-first.  A turn starts at a ``user`` message and
  includes everything up to (not including) the next ``user`` message.  The
  newest message is never dropped.
- Insert a single visible system notice describing the omission, so the model
  knows earlier context was truncated rather than never existed.
- Support a STICKY boundary: callers persist ``next_sticky_boundary`` per
  session and pass it back on the next call, so repeated compactions keep
  dropping from the same frontier instead of creeping forward turn-by-turn
  (which would shred llama-server's prefix cache).
- Leave headroom below the budget (see
  ``ROLLING_COMPACTION_HEADROOM_RATIO``) so the next turn does not immediately
  re-trigger compaction.

All functions are pure: no I/O and no global state is mutated.  Token counts
are honest character-based estimates (see :func:`estimate_tokens`), never
self-reported model numbers.
"""

import math
from typing import Any

ROLLING_COMPACTION_HEADROOM_RATIO = 0.9
"""Fraction of ``token_budget`` that :func:`fit_history` aims to fit under.

Compaction triggers when the history exceeds the full budget, but evicts only
until the compacted history fits ``int(token_budget * 0.9)``.  The reserved
10% headroom absorbs the next user/assistant exchange so compaction does not
re-trigger on every single turn.
"""

_CHARS_PER_TOKEN = 4
_UNBROKEN_ASCII_CHARS_PER_TOKEN = 2
_UNBROKEN_ASCII_RUN_MIN = 64
_MESSAGE_OVERHEAD_TOKENS = 4

_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF00, 0xFFEF),  # Fullwidth forms
)


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """Honest char-based token estimate for a single string.

    - Normal text (short ASCII runs, non-CJK characters): ~4 chars/token.
    - CJK ideographs/kana/hangul/fullwidth punctuation: ~1 token/char.
    - An unbroken ASCII run (no whitespace) of >= 64 chars — hashes, base64,
      minified blobs — is priced at ~2 chars/token.

    Runs are broken by whitespace, so ordinary prose with spaces stays at
    ~4 chars/token.  Returns the ceiling of the accumulated weight; empty
    strings cost 0.
    """
    if not text:
        return 0
    total_weight = 0.0
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isascii() and not ch.isspace():
            j = i
            while j < n and text[j].isascii() and not text[j].isspace():
                j += 1
            run_len = j - i
            if run_len >= _UNBROKEN_ASCII_RUN_MIN:
                total_weight += run_len / _UNBROKEN_ASCII_CHARS_PER_TOKEN
            else:
                total_weight += run_len / _CHARS_PER_TOKEN
            i = j
        elif _is_cjk(ch):
            total_weight += 1.0
            i += 1
        else:  # whitespace and other non-CJK characters
            total_weight += 1.0 / _CHARS_PER_TOKEN
            i += 1
    return math.ceil(total_weight)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate the prompt tokens for an OpenAI-style message list.

    Sum of :func:`estimate_tokens` over each message's content plus a small
    per-message overhead constant (~4 tokens) covering role framing.  Non-str
    content is coerced with ``str(content)`` for estimation only; callers must
    coerce identically at send time or treat this as a lower bound.
    """
    return sum(
        estimate_tokens(str(message.get("content", ""))) + _MESSAGE_OVERHEAD_TOKENS
        for message in messages
    )


def _compaction_notice(dropped_count: int) -> str:
    return (
        f"[context compacted: {dropped_count} earlier message(s) omitted "
        f"to fit the model context window]"
    )


def _message_cost(message: dict[str, Any]) -> int:
    return estimate_tokens(str(message.get("content", ""))) + _MESSAGE_OVERHEAD_TOKENS


def _plan_eviction(
    messages: list[dict[str, Any]], sticky_boundary: int
) -> tuple[set[int], list[tuple[int, int]]]:
    """Plan this run's eviction.

    Returns ``(dead_indices, units)``:

    - ``dead_indices``: messages at ``[1, frontier)`` already compacted away by
      earlier runs (sticky replay of the full transcript).  They are omitted
      from the output for free and are NOT counted in ``dropped_count``.
    - ``units``: half-open index ranges evictable THIS run, oldest first.  A
      unit is a whole turn (starts at a ``user`` message, runs to the next
      ``user`` message) with ``start >= max(1, sticky_boundary)``; a turn
      containing the last message contributes only its prefix
      ``[start, last_index)`` so the final message is never touched.  Index 0
      is always protected.

    If nothing is droppable at/after the sticky boundary, the boundary is
    abandoned: ``dead_indices`` is emptied and units are rebuilt from index 1
    (the protected region becomes ordinary evictable content).
    """
    last_idx = len(messages) - 1
    unit_floor = max(1, sticky_boundary)
    frontier = min(unit_floor, max(last_idx, 1))
    first_user = next(
        (i for i, m in enumerate(messages) if m.get("role") == "user"), None
    )

    def collect(floor: int) -> list[tuple[int, int]]:
        if first_user is None:
            return []
        starts = [first_user] + [
            i
            for i in range(first_user + 1, len(messages))
            if messages[i].get("role") == "user"
        ]
        units: list[tuple[int, int]] = []
        for pos, start in enumerate(starts):
            end = starts[pos + 1] if pos + 1 < len(starts) else len(messages)
            if end <= last_idx:
                if start >= floor:
                    units.append((start, end))
            else:  # turn contains the last message: only its prefix is evictable
                p_start = max(start, floor)
                if p_start < last_idx:
                    units.append((p_start, last_idx))
        units.sort()
        return units

    units = collect(unit_floor)
    if units:
        return set(range(1, frontier)), units
    return set(), collect(1)


def fit_history(
    messages: list[dict[str, Any]],
    token_budget: int,
    sticky_boundary: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compact a chat history to fit ``token_budget`` estimated tokens.

    Semantics:
    - ``token_budget <= 0`` raises ``ValueError``.  If even system+last exceed
      the budget, still returns the best-effort result (system?, notice, last)
      with an honest (possibly over-budget) ``estimated_tokens``; never raises
      beyond the budget-validation error.
    - Never drops ``messages[0]`` when its role is ``"system"`` (index 0 is
      protected unconditionally) and never drops the last message.
    - If the whole history already fits the budget, returns ``(copy, info)``
      unchanged with ``compacted=False``.
    - Otherwise drops whole turns (a turn starts at a ``user`` message and runs
      to the next ``user`` message) oldest-first, beginning after index 0 and,
      when ``sticky_boundary > 0``, only from indices ``>= sticky_boundary``;
      if nothing droppable exists at/after the sticky boundary, falls back to
      dropping from index 1.  Eviction stops once the projected result fits
      ``int(token_budget * ROLLING_COMPACTION_HEADROOM_RATIO)``; if everything
      evictable was dropped and it still does not fit, the best-effort result
      is returned anyway.
    - Sticky replay: messages at indices ``[1, sticky_frontier)`` of the input
      were already compacted away by earlier runs (the caller replays the full
      transcript together with the persisted boundary).  They are omitted from
      the output for free — not counted in ``dropped_count`` and not mentioned
      by this run's notice.  The frontier never covers the last message.
    - When messages were dropped, exactly one system-role notice is inserted
      right after the surviving system message (or at index 0 if there is none).

    Non-str content is coerced with ``str(content)`` for estimation; original
    message objects are preserved untouched (the returned list is a shallow
    copy plus, if compacting, one new notice dict).

    ``info`` keys:
    - ``dropped_count``: number of messages evicted THIS run (whole turns /
      final-turn prefixes), excluding free omissions below the sticky frontier.
    - ``next_sticky_boundary``: defined EXACTLY as
      ``sticky_boundary + dropped_count``.  It is an index into the ORIGINAL
      message list marking where future compactions should start dropping:
      every index below it has been omitted or evicted by some run so far.
      Persist it per session (e.g. alongside the stored transcript) and pass
      it back as ``sticky_boundary`` on the next call; without it the eviction
      frontier creeps forward each turn and destroys llama-server's prefix
      cache.
    - ``estimated_tokens``: honest estimate of the returned list (notice
      included), recomputed on the final result.
    - ``compacted``: True iff any message was dropped or omitted.
    """
    if token_budget <= 0:
        raise ValueError("token_budget must be a positive integer")
    costs = [_message_cost(m) for m in messages]
    total = sum(costs)
    if total <= token_budget:
        return list(messages), {
            "dropped_count": 0,
            "next_sticky_boundary": sticky_boundary,
            "estimated_tokens": total,
            "compacted": False,
        }

    dead, units = _plan_eviction(messages, sticky_boundary)
    live_total = total - sum(costs[i] for i in dead)
    target = int(token_budget * ROLLING_COMPACTION_HEADROOM_RATIO)
    dropped_indices: set[int] = set()
    dropped_tokens = 0
    dropped_count = 0
    for start, end in units:
        dropped_indices.update(range(start, end))
        dropped_tokens += sum(costs[start:end])
        dropped_count += end - start
        notice_cost = (
            estimate_tokens(_compaction_notice(dropped_count))
            + _MESSAGE_OVERHEAD_TOKENS
        )
        if live_total - dropped_tokens + notice_cost <= target:
            break

    if not dead and not dropped_indices:
        # Over budget but nothing is evictable (e.g. [system, last] alone).
        return list(messages), {
            "dropped_count": 0,
            "next_sticky_boundary": sticky_boundary,
            "estimated_tokens": total,
            "compacted": False,
        }

    skip = dead | dropped_indices
    result = [m for i, m in enumerate(messages) if i not in skip]
    notice_msg = {"role": "system", "content": _compaction_notice(dropped_count)}
    insert_at = 1 if messages[0].get("role") == "system" else 0
    result.insert(insert_at, notice_msg)
    return result, {
        "dropped_count": dropped_count,
        "next_sticky_boundary": sticky_boundary + dropped_count,
        "estimated_tokens": estimate_messages_tokens(result),
        "compacted": True,
    }
