"""Unit tests for Speculative Multi-Drafting & Model Racing Engine (R19)."""

import time
from threading import Event
import pytest
from local_coding_agent.speculative_racing import SpeculativeRacer


def test_speculative_racing_winner_cancels_loser():
    def fast_winner(cancel_ev: Event) -> dict:
        time.sleep(0.05)
        return {"status": "accepted", "summary": "Fast winner patch", "patch": "+winner"}

    def slow_loser(cancel_ev: Event) -> dict:
        for _ in range(20):
            if cancel_ev.is_set():
                return {"status": "failed", "error": "cancelled"}
            time.sleep(0.05)
        return {"status": "accepted", "summary": "Slow loser"}

    racer = SpeculativeRacer()
    winner = racer.run([fast_winner, slow_loser])
    assert winner["status"] == "accepted"
    assert winner["summary"] == "Fast winner patch"


def test_speculative_racing_all_rejected():
    def rejected_1(cancel_ev: Event) -> dict:
        return {"status": "rejected", "summary": "Rejected 1"}

    def rejected_2(cancel_ev: Event) -> dict:
        return {"status": "rejected", "summary": "Rejected 2"}

    racer = SpeculativeRacer()
    res = racer.run([rejected_1, rejected_2])
    assert res["status"] == "rejected"


def test_speculative_racing_global_cancellation():
    global_cancel = Event()
    global_cancel.set()

    def runner_1(cancel_ev: Event) -> dict:
        if cancel_ev.is_set():
            return {"status": "failed", "error": "cancelled"}
        return {"status": "accepted"}

    racer = SpeculativeRacer()
    res = racer.run([runner_1], cancel_event=global_cancel)
    assert res.get("status") in ("failed", "cancelled")
