import json
import queue
import threading
from datetime import datetime

import pytest

from vamos.brain.advisor import Advisor, Job
from vamos.brain.answer import Spot
from vamos.brain.costs import Ledger
from vamos.brain.provider import PRESETS, CallFailed, FakeProvider, Reply, Request

SPOT = Spot(pot=180, to_call=0, street_bet=0, price=0, min_raise=20, behind=940, big_blind=20)
USAGE = {"input_tokens": 100, "output_tokens": 300, "cache_read_input_tokens": 9000}


def answer(*rows, note="Player A calls too much"):
    strategy = [{"action": a, "size_pct_pot": size, "freq": f} for a, size, f in rows]
    return Reply(json.dumps({"strategy": strategy, "note": note}), "end_turn", USAGE)


BET = answer(("bet", 50, 1.0))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path):
    class Rig:
        def __init__(self, script):
            self.updates: queue.Queue[str] = queue.Queue()
            self.clock = Clock()
            self.provider = FakeProvider(script)
            self.ledger = Ledger(tmp_path / "costs.jsonl", datetime(2026, 10, 2, 12))
            self.advice_log = tmp_path / "advice.jsonl"
            self.advisor = Advisor(
                self.provider,
                self.ledger,
                self.updates.put,
                clock=self.clock,
                now=lambda: datetime(2026, 10, 2, 13),
                advice_log=self.advice_log,
            )

        def job(self, node="h1:1:8", preset="balanced"):
            request = Request(PRESETS[preset], [{"type": "text", "text": "prefix"}], [])
            return Job(node, request, SPOT, lambda s: s.replace("Player A", "alex"))

        def updated(self):
            return self.updates.get(timeout=3)

    return Rig


def test_an_answer_reaches_the_card_with_real_names(rig):
    r = rig([BET])
    assert r.advisor.start("g", r.job(), r.clock.t + 20)
    assert r.updated() == "g"
    view = r.advisor.view("g", "h1:1:8")
    assert view["source"] == "Claude" and view["state"] == "done"
    assert view["action"] == "BET 0.90 (4.5bb)"
    assert view["mix"] is None
    assert view["note"] == "alex calls too much"
    assert view["model"] == "Claude Sonnet 5.5"
    assert r.ledger.totals(datetime(2026, 10, 2, 14))["session"] > 0
    assert r.advisor.view("g", "h1:1:9") is None  # never shown for another node


def test_a_mixed_answer_shows_its_mix(rig):
    r = rig([answer(("bet", 33, 0.6), ("check", 0, 0.4))])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert r.advisor.view("g", "h1:1:8")["mix"] == ["bet 3bb 60%", "check 40%"]


def test_one_call_per_node_and_preset(rig):
    r = rig([BET])
    assert r.advisor.start("g", r.job(), r.clock.t + 20)
    assert not r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert len(r.provider.requests) == 1


def test_a_missed_deadline_falls_back_to_the_math(rig):
    gate = threading.Event()
    r = rig([(gate, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.clock.t += 11
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert (view["source"], view["state"], view["reason"]) == ("Math", "failed", "timed out")
    gate.set()  # the late answer is dropped
    with pytest.raises(queue.Empty):
        r.updates.get(timeout=0.5)
    assert r.advisor.view("g", "h1:1:8")["reason"] == "timed out"


def test_too_little_time_skips_the_call(rig):
    r = rig([])
    r.advisor.start("g", r.job(), r.clock.t + 2)
    view = r.advisor.view("g", "h1:1:8")
    assert (view["source"], view["reason"]) == ("Math", "not enough time on the clock")
    assert r.provider.requests == []


def test_the_decision_passing_cancels_the_call(rig):
    gate = threading.Event()
    r = rig([(gate, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.stop("g")
    assert r.advisor.view("g", "h1:1:8") is None
    with pytest.raises(queue.Empty):
        r.updates.get(timeout=0.5)


def test_a_new_node_replaces_the_old_call(rig):
    gate = threading.Event()
    r = rig([(gate, BET), answer(("check", 0, 1.0))])
    r.advisor.start("g", r.job("h1:1:8"), r.clock.t + 20)
    r.advisor.start("g", r.job("h1:2:10"), r.clock.t + 20)
    r.updated()
    assert r.advisor.view("g", "h1:2:10")["action"] == "CHECK"
    assert r.advisor.view("g", "h1:1:8") is None


def test_a_passing_failure_is_tried_again_while_there_is_time(rig):
    r = rig([CallFailed("Claude unavailable (529)"), BET])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert r.advisor.view("g", "h1:1:8")["action"] == "BET 0.90 (4.5bb)"
    assert len(r.provider.requests) == 2


def test_rate_limits_and_unreadable_answers_near_the_deadline_are_not_retried(rig):
    r = rig([CallFailed("rate limited")])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert r.advisor.view("g", "h1:1:8")["reason"] == "rate limited"

    r = rig([Reply("not json", "end_turn", USAGE)])
    r.advisor.start("g", r.job(), r.clock.t + 5)
    r.updated()
    assert r.advisor.view("g", "h1:1:8")["reason"] == "unreadable answer"
    assert len(r.provider.requests) == 1


def test_asking_opus_keeps_the_first_answer_up_meanwhile(rig):
    gate = threading.Event()
    r = rig([BET, (gate, answer(("check", 0, 1.0), note="Player A traps"))])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["model"], view["asked"], view["state"]) == (
        "BET 0.90 (4.5bb)",
        "Claude Sonnet 5.5",
        "Claude Opus 5.5",
        "thinking",
    )
    gate.set()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["model"], view["note"]) == ("CHECK", "Claude Opus 5.5", "alex traps")


def test_a_refused_call_is_never_sent(rig):
    r = rig([])
    r.advisor.start("g", r.job(), r.clock.t + 20, refuse="blocked: a real name was left")
    assert r.advisor.view("g", "h1:1:8")["reason"] == "blocked: a real name was left"
    assert r.advisor.asked("g", "h1:1:8") and r.advisor.asked("g", "h1:1:8", "balanced")
    assert not r.advisor.asked("g", "h1:1:8", "deep")
    assert r.provider.requests == []


def test_answers_and_failures_are_logged_under_pseudonyms(rig):
    r = rig([BET, CallFailed("rate limited")])
    r.advisor.start("g", r.job("h1:1:8"), r.clock.t + 20)
    r.updated()
    r.advisor.start("g", r.job("h1:2:9"), r.clock.t + 20)
    r.updated()
    first, second = (json.loads(line) for line in r.advice_log.read_text().splitlines())
    assert (first["node"], first["state"], first["chosen"]) == ("h1:1:8", "done", ["bet", 90])
    assert (first["seconds"], first["allowed"]) == (0, 20)  # the test clock stands still
    assert first["model"] == "claude-sonnet-5-5"
    assert "Player A calls too much" in first["answer"]  # as Claude wrote it
    assert (second["state"], second["reason"], second["answer"]) == ("failed", "rate limited", None)
