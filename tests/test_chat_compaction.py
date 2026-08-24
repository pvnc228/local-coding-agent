"""Tests for local_coding_agent.chat_compaction (rolling context window)."""

import math

import pytest

from local_coding_agent.chat_compaction import (
    ROLLING_COMPACTION_HEADROOM_RATIO,
    estimate_messages_tokens,
    estimate_tokens,
    fit_history,
)


def _turn(user_content, assistant_content):
    return [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": assistant_content},
    ]


def _session(turn_count, user_marker="x", assistant_marker="y"):
    messages = [{"role": "system", "content": "You are terse."}]
    for _ in range(turn_count):
        messages.extend(_turn(user_marker * 1000, assistant_marker * 1000))
    return messages


def test_headroom_ratio_constant():
    assert ROLLING_COMPACTION_HEADROOM_RATIO == 0.9


def test_estimate_tokens_ascii_prose():
    prose = "The quick brown fox jumps over the lazy dog near the riverbank today."
    assert estimate_tokens(prose) == math.ceil(len(prose) / 4)
    assert estimate_tokens("ab") == 1


def test_estimate_tokens_cjk():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("テスト") == 3


def test_estimate_tokens_unbroken_ascii_run():
    assert estimate_tokens("a" * 200) == 100
    assert estimate_tokens("a" * 64) == 32
    # Just under the threshold: back to normal pricing.
    assert estimate_tokens("a" * 63) == math.ceil(63 / 4)
    # Whitespace breaks runs: spaced-out text keeps ~4 chars/token pricing.
    spaced = " ".join(["word"] * 40)
    assert estimate_tokens(spaced) == math.ceil(len(spaced) / 4)


def test_estimate_messages_tokens_overhead():
    assert estimate_messages_tokens([]) == 0
    assert estimate_messages_tokens([{"role": "user", "content": ""}]) == 4
    assert estimate_messages_tokens([{"role": "user", "content": 12345}]) == (
        estimate_tokens("12345") + 4
    )


def test_fit_history_rejects_nonpositive_budget():
    with pytest.raises(ValueError):
        fit_history([{"role": "user", "content": "hi"}], 0)
    with pytest.raises(ValueError):
        fit_history([{"role": "user", "content": "hi"}], -5)


def test_fit_history_fits_returns_unchanged_copy():
    messages = _session(2)
    result, info = fit_history(messages, 100000, sticky_boundary=3)
    assert set(info) == {
        "dropped_count",
        "next_sticky_boundary",
        "estimated_tokens",
        "compacted",
    }
    assert result == messages
    assert result is not messages
    assert info["compacted"] is False
    assert info["dropped_count"] == 0
    assert info["next_sticky_boundary"] == 3
    assert info["estimated_tokens"] == estimate_messages_tokens(messages)


def test_fit_history_multi_turn_eviction():
    # system (8 tok) + 10 turns x 2 msgs (504 tok each) = 10088 tok.
    messages = _session(10)
    budget = 2500
    result, info = fit_history(messages, budget)

    assert info["compacted"] is True
    assert info["dropped_count"] == 16
    assert info["next_sticky_boundary"] == 16
    assert result[0] is messages[0]
    assert result[0]["role"] == "system"
    # Notice inserted right after the surviving system message.
    assert result[1]["role"] == "system"
    assert result[1]["content"] == (
        "[context compacted: 16 earlier message(s) omitted "
        "to fit the model context window]"
    )
    # Oldest turns dropped; newest two whole turns survive untouched.
    assert result[2:] == messages[17:]
    assert result[-1] is messages[-1]
    assert len(result) == 6
    # Headroom respected.
    assert estimate_messages_tokens(result) <= int(
        budget * ROLLING_COMPACTION_HEADROOM_RATIO
    )
    assert info["estimated_tokens"] == estimate_messages_tokens(result)


def test_sticky_boundary_does_not_creep():
    messages = _session(10)
    _, first_info = fit_history(messages, 2500)
    sticky = first_info["next_sticky_boundary"]
    assert (first_info["dropped_count"], sticky) == (16, 16)

    # Session grows by one new turn.
    grown = messages + _turn("z" * 1000, "w" * 1000)

    # With the persisted boundary: the already-compacted region [1, 16) is
    # omitted for free and only newly-droppable turns are evicted (4 messages:
    # the two surviving middle turns); growth alone drives the eviction.
    result, second_info = fit_history(grown, 2500, sticky_boundary=sticky)
    assert second_info["dropped_count"] == 4
    assert second_info["next_sticky_boundary"] == sticky + 4 == 20
    assert result[0] is grown[0]
    assert result[-1] is grown[-1]
    assert len(result) == 5
    assert all(
        kept is not grown[old]
        for kept in result
        for old in range(1, sticky)
    )
    assert estimate_messages_tokens(result) <= int(
        2500 * ROLLING_COMPACTION_HEADROOM_RATIO
    )

    # Control: without the sticky boundary the frontier creeps back to the
    # start and re-drops already-compacted history (18 vs 5 messages).
    _, creep_info = fit_history(grown, 2500)
    assert creep_info["dropped_count"] == 18
    assert creep_info["dropped_count"] != second_info["dropped_count"]

    # Sticky fallback: when nothing droppable exists at/after the boundary,
    # compaction falls back to dropping from index 1 rather than failing.
    result_fb, fb_info = fit_history(grown, 2500, sticky_boundary=len(grown))
    assert fb_info["dropped_count"] == 18
    assert result_fb[0] is grown[0]
    assert result_fb[-1] is grown[-1]


def test_tiny_budget_returns_system_notice_last():
    messages = [
        {"role": "system", "content": "S" * 40},
        {"role": "user", "content": "U" * 100},
        {"role": "assistant", "content": "A" * 100},
        {"role": "user", "content": "Q" * 100},
    ]
    result, info = fit_history(messages, 30)
    assert info["compacted"] is True
    assert info["dropped_count"] == 2
    assert [m["role"] for m in result] == ["system", "system", "user"]
    assert result[0] is messages[0]
    assert result[1]["content"] == (
        "[context compacted: 2 earlier message(s) omitted "
        "to fit the model context window]"
    )
    assert result[2] is messages[-1]
    # Honest estimate even though it exceeds the tiny budget.
    assert info["estimated_tokens"] == estimate_messages_tokens(result)


def test_no_system_notice_at_index_zero():
    messages = [
        {"role": "user", "content": "a" * 1000},
        {"role": "assistant", "content": "b" * 1000},
        {"role": "user", "content": "c" * 1000},
        {"role": "assistant", "content": "d" * 1000},
    ]
    result, info = fit_history(messages, 1750)
    assert info["compacted"] is True
    assert info["dropped_count"] == 1
    assert result[0]["role"] == "system"
    assert result[0]["content"] == (
        "[context compacted: 1 earlier message(s) omitted "
        "to fit the model context window]"
    )
    assert result[1] is messages[0]
    assert result[2] is messages[1]
    assert result[3] is messages[3]
    assert estimate_messages_tokens(result) <= int(1750 * 0.9)


def test_non_string_content_preserved_verbatim():
    content_variants = [42, None, ["a", "b"], {"k": 1}]
    messages = [
        {"role": "user", "content": variant} for variant in content_variants
    ]
    result, info = fit_history(messages, 10000)
    assert info["compacted"] is False
    for original, kept in zip(messages, result):
        assert kept is original
        assert kept["content"] is original["content"]


def test_empty_and_single_message_edges():
    result, info = fit_history([], 50)
    assert result == []
    assert info == {
        "dropped_count": 0,
        "next_sticky_boundary": 0,
        "estimated_tokens": 0,
        "compacted": False,
    }

    solo = [{"role": "user", "content": "x" * 1000}]
    result, info = fit_history(solo, 10)
    assert result == solo
    assert result[0] is solo[0]
    assert info["compacted"] is False
    assert info["estimated_tokens"] == 504  # honest, over budget, no raise
