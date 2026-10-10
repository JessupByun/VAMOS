import json
import logging
import queue
import threading
import time
from datetime import datetime

import pytest

from vamos.brain.advisor import Advisor, Job, with_dollars
from vamos.brain.answer import Spot
from vamos.brain.costs import Ledger
from vamos.brain.provider import PRESETS, STOPPED, CallFailed, FakeProvider, Reply, Request

SPOT = Spot(pot=180, to_call=0, street_bet=0, price=0, min_raise=20, behind=940, big_blind=20)
USAGE = {"input_tokens": 100, "output_tokens": 300, "cache_read_input_tokens": 9000}


def answer(*rows, note="Player A calls too much"):
    strategy = [{"action": a, "size_pct_pot": size, "freq": f} for a, size, f in rows]
    return Reply(
        json.dumps({"reason": note, "strategy": strategy, "plan": "Bet again on blanks."}),
        "end_turn",
        USAGE,
    )


BET = answer(("bet", 50, 1.0))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def rig(tmp_path):
    class Rig:
        def __init__(self, script, provider=None):
            self.updates: queue.Queue[str] = queue.Queue()
            self.clock = Clock()
            self.provider = provider or FakeProvider(script)
            self.ledger = Ledger(tmp_path / "costs.jsonl")
            self.advice_log = tmp_path / "advice.jsonl"
            self.advisor = Advisor(
                self.provider,
                self.ledger,
                self.updates.put,
                clock=self.clock,
                now=lambda: datetime(2026, 10, 2, 13),
                advice_log=self.advice_log,
            )

        def job(self, node="h1:1:8", preset="standard", message=None):
            messages = [{"role": "user", "content": message}] if message else []
            request = Request(PRESETS[preset], [{"type": "text", "text": "prefix"}], messages)
            return Job(node, request, SPOT, lambda s: s.replace("Player A", "alex"))

        def updated(self):
            return self.updates.get(timeout=3)

        def sent(self, count):
            """Waits until Claude was sent `count` requests: each call runs on its own thread."""
            until = time.monotonic() + 3
            while len(self.provider.requests) < count:
                assert time.monotonic() < until
                time.sleep(0.005)

        def logged(self):
            self.advisor.close()  # every call has ended and been logged
            return [json.loads(line) for line in self.advice_log.read_text().splitlines()]

    return Rig


def test_an_answer_reaches_the_card_with_real_names(rig):
    r = rig([BET])
    assert r.advisor.start("g", r.job(), r.clock.t + 20)
    assert r.updated() == "g"
    view = r.advisor.view("g", "h1:1:8")
    assert view["source"] == "Claude" and view["state"] == "done"
    assert view["action"] == "BET $0.90 (4.5bb)"
    assert (view["mix"], view["pick"], view["pickPct"]) == (["bet $0.90 (4.5bb) 100%"], 0, 100)
    assert (view["why"], view["plan"]) == ("alex calls too much", "Bet again on blanks.")
    assert view["model"] == "Claude Opus 5.5 · low"
    assert r.ledger.totals(datetime(2026, 10, 2, 14))["session"] > 0
    assert r.advisor.view("g", "h1:1:9") is None  # never shown for another node


def test_a_mixed_answer_shows_its_mix(rig):
    r = rig([answer(("bet", 33, 0.6), ("check", 0, 0.4))])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert r.advisor.view("g", "h1:1:8")["mix"] == ["bet $0.60 (3bb) 60%", "check 40%"]


def test_one_call_per_node_and_preset(rig):
    r = rig([BET])
    assert r.advisor.start("g", r.job(), r.clock.t + 20)
    assert not r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    assert len(r.provider.requests) == 1


def test_a_missed_deadline_says_why(rig):
    gate = threading.Event()
    r = rig([(gate, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.clock.t += 11
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["state"], view["reason"]) == (None, "failed", "timed out")
    gate.set()  # the late answer is dropped
    with pytest.raises(queue.Empty):
        r.updates.get(timeout=0.5)
    assert r.advisor.view("g", "h1:1:8")["reason"] == "timed out"


def test_too_little_time_skips_the_call(rig):
    r = rig([])
    r.advisor.start("g", r.job(), r.clock.t + 2)
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["reason"]) == (None, "not enough time on the clock")
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
    assert r.advisor.view("g", "h1:1:8")["action"] == "BET $0.90 (4.5bb)"
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


def test_a_failure_before_claudes_first_event_costs_nothing(rig):
    # Turned away (rate limited, overloaded, no credits): Claude did no work, so no estimate.
    r = rig([CallFailed("rate limited")])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    r.logged()
    assert not r.ledger.path.exists()


def test_asking_opus_keeps_the_first_answer_up_meanwhile(rig):
    gate = threading.Event()
    r = rig([BET, (gate, answer(("check", 0, 1.0), note="Player A traps"))])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["model"], view["asked"], view["state"]) == (
        "BET $0.90 (4.5bb)",
        "Claude Opus 5.5 · low",
        "Claude Opus 5.5 · medium",
        "thinking",
    )
    gate.set()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["model"], view["why"]) == (
        "CHECK",
        "Claude Opus 5.5 · medium",
        "alex traps",
    )
    # The panel keeps both: the first answer, then the deeper one under it.
    assert [(a["preset"], a["model"], a["action"]) for a in view["answers"]] == [
        ("standard", "Claude Opus 5.5 · low", "BET $0.90 (4.5bb)"),
        ("deep", "Claude Opus 5.5 · medium", "CHECK"),
    ]
    assert view["answers"][1]["why"] == "alex traps"


def test_asking_deeper_while_the_first_answer_thinks_lets_both_finish(rig):
    first = threading.Event()
    r = rig({"standard": (first, BET), "deep": answer(("check", 0, 1.0), note="Player A traps")})
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.updated()  # the deeper answer came first
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["state"]) for a in view["answers"]] == [
        ("standard", "thinking"),
        ("deep", "done"),
    ]
    assert (view["action"], view["model"]) == ("CHECK", "Claude Opus 5.5 · medium")
    first.set()
    assert r.updated() == "g"  # the first answer still reaches the panel
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["action"]) for a in view["answers"]] == [
        ("standard", "BET $0.90 (4.5bb)"),
        ("deep", "CHECK"),
    ]
    # The card keeps the deeper answer: the one asked last.
    assert (view["action"], view["model"], view["state"]) == (
        "CHECK",
        "Claude Opus 5.5 · medium",
        "done",
    )
    # Both are billed and logged.
    entries = sorted(r.logged(), key=lambda e: e["preset"])
    assert [(e["preset"], e["state"]) for e in entries] == [("deep", "done"), ("standard", "done")]
    assert len(r.ledger.path.read_text().splitlines()) == 2


def held_back(r, count=1):
    """Claude was sent `count` requests and no more, a moment after asking."""
    r.sent(count)
    time.sleep(0.2)
    return len(r.provider.requests) == count


def test_ask_deeper_before_the_first_reply_began_waits_for_it(rig):
    # Anthropic's prompt cache can be read only once a request's reply has begun: sent before
    # that, the deeper call would write the profiles to the cache a second time (2026-10-07,
    # two $0.42 calls on one decision).
    replying, first = threading.Event(), threading.Event()
    deeper = answer(("check", 0, 1.0), note="Player A traps")
    r = rig({"standard": (replying, first, BET), "deep": deeper})
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.sent(1)
    assert r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    assert held_back(r)
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["state"]) for a in view["answers"]] == [
        ("standard", "thinking"),
        ("deep", "thinking"),
    ]
    replying.set()  # Claude's first event for the first call: the deeper one goes now
    r.sent(2)
    r.updated()
    first.set()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["state"], a["action"]) for a in view["answers"]] == [
        ("standard", "done", "BET $0.90 (4.5bb)"),
        ("deep", "done", "CHECK"),
    ]
    assert view["action"] == "CHECK"


def test_ask_deeper_after_the_first_reply_began_goes_at_once(rig):
    first = threading.Event()
    r = rig({"standard": (first, BET), "deep": answer(("check", 0, 1.0))})
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.sent(1)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.updated()  # the deeper answer, while the first call still thinks
    assert len(r.provider.requests) == 2
    first.set()


def test_ask_deeper_goes_once_the_first_call_fails_before_its_first_event(rig):
    never, failing = threading.Event(), threading.Event()
    r = rig({"standard": (never, failing, CallFailed("rate limited")), "deep": BET})
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    assert held_back(r)
    failing.set()
    r.updated()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["state"], a["reason"]) for a in view["answers"]] == [
        ("standard", "failed", "rate limited"),
        ("deep", "done", None),
    ]


def test_ask_deeper_goes_once_the_first_call_times_out_before_its_first_event(rig):
    never, gate = threading.Event(), threading.Event()
    r = rig({"standard": (never, gate, BET), "deep": answer(("check", 0, 1.0))})
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    assert held_back(r)
    r.clock.t += 11  # the first call's deadline passes; the deeper one has 9 seconds left
    r.updated()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["preset"], a["state"], a["reason"]) for a in view["answers"]] == [
        ("standard", "failed", "timed out"),
        ("deep", "done", None),
    ]
    assert len(r.provider.requests) == 2


def test_a_deeper_call_whose_wait_used_up_the_clock_is_never_sent(rig):
    # Both calls on a decision share its deadline, as the server sets them.
    never, gate = threading.Event(), threading.Event()
    r = rig({"standard": (never, gate, BET), "deep": BET})
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 10)
    assert held_back(r)
    r.clock.t += 11  # the deadline passes before Claude's first event
    r.updated()
    deadline = time.monotonic() + 2
    while (
        len([a for a in r.advisor.view("g", "h1:1:8")["answers"] if a["state"] != "thinking"]) < 2
    ):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    time.sleep(0.1)  # a request sent late would show up by now
    assert len(r.provider.requests) == 1
    states = {
        a["preset"]: (a["state"], a["reason"]) for a in r.advisor.view("g", "h1:1:8")["answers"]
    }
    assert states["deep"][0] == "failed"
    assert states["deep"][1] in ("timed out", "not enough time on the clock")
    gate.set()


def test_a_first_reply_starting_just_before_the_deadline_leaves_the_deeper_call_unsent(rig):
    first, gate = threading.Event(), threading.Event()
    r = rig({"standard": (first, gate, BET), "deep": BET})
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 10)
    assert held_back(r)
    r.clock.t += 9  # one second left: too little for an answer
    first.set()
    r.updated()
    assert {
        a["preset"]: (a["state"], a["reason"]) for a in r.advisor.view("g", "h1:1:8")["answers"]
    }["deep"] == ("failed", "not enough time on the clock")
    assert len(r.provider.requests) == 1
    gate.set()


def test_a_deeper_call_still_held_back_when_the_decision_passes_is_never_sent(rig, caplog):
    caplog.set_level(logging.INFO, logger="vamos.brain")
    never, gate = threading.Event(), threading.Event()
    r = rig({"standard": (never, gate, BET), "deep": BET})
    r.advisor.start("g", r.job(message="Hand so far"), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep", message="Hand so far"), r.clock.t + 20)
    assert held_back(r)
    r.advisor.stop("g")  # the hero acted before Claude's first event
    entries = sorted(r.logged(), key=lambda e: e["preset"])
    assert [(e["preset"], e["state"], e["attempts"]) for e in entries] == [
        ("deep", "cancelled", 0),
        ("standard", "cancelled", 1),
    ]
    assert entries[0]["spot"] is None  # never sent, so never replayed
    assert len(r.provider.requests) == 1
    # Only the request that was sent is in the cost ledger.
    [row] = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert (row["kind"], row["usage"]) == ("advice", "unknown")
    [deep] = [line for line in claude_lines(caplog) if "preset=deep" in line]
    assert deep.endswith("outcome=cancelled seconds=0.00 attempts=0")


def test_the_first_answer_shows_on_the_card_while_the_deeper_one_thinks(rig):
    first, deeper = threading.Event(), threading.Event()
    r = rig({"standard": (first, BET), "deep": (deeper, answer(("check", 0, 1.0)))})
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    first.set()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert (view["action"], view["model"], view["asked"], view["state"]) == (
        "BET $0.90 (4.5bb)",
        "Claude Opus 5.5 · low",
        "Claude Opus 5.5 · medium",
        "thinking",
    )


def test_a_preset_is_asked_once_per_decision_even_after_a_deeper_ask(rig):
    r = rig([BET, BET])
    assert r.advisor.start("g", r.job(), r.clock.t + 20)
    assert r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    assert not r.advisor.start("g", r.job(), r.clock.t + 20)
    assert r.advisor.asked("g", "h1:1:8", "standard") and r.advisor.asked("g", "h1:1:8", "deep")
    r.updated()
    r.updated()
    assert len(r.provider.requests) == 2


def test_the_decision_passing_stops_every_call_on_it(rig):
    first, deeper = threading.Event(), threading.Event()
    r = rig([(first, BET), (deeper, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.advisor.stop("g")
    assert [e["state"] for e in r.logged()] == ["cancelled", "cancelled"]


def test_a_new_decision_stops_every_call_on_the_last_one(rig):
    first, deeper = threading.Event(), threading.Event()
    r = rig([(first, BET), (deeper, BET), answer(("check", 0, 1.0))])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.sent(1)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.sent(2)
    r.advisor.start("g", r.job("h1:2:10"), r.clock.t + 20)
    r.updated()
    by_node = sorted((e["node"], e["preset"], e["state"]) for e in r.logged())
    assert by_node == [
        ("h1:1:8", "deep", "cancelled"),
        ("h1:1:8", "standard", "cancelled"),
        ("h1:2:10", "standard", "done"),
    ]


def test_a_decision_that_needs_no_call_stops_the_calls_on_an_earlier_one(rig):
    r = rig([(threading.Event(), BET)])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.leave("g", "h1:1:8")  # still the same decision: the call goes on
    assert r.advisor.view("g", "h1:1:8")["state"] == "thinking"
    r.advisor.leave("g", "h2:1:3")  # the next hand's preflop spot, answered by a rule
    assert [e["state"] for e in r.logged()] == ["cancelled"]
    assert r.advisor.view("g", "h1:1:8") is None


def test_a_clock_read_moves_the_deadline_of_every_call_on_the_decision(rig):
    first, deeper = threading.Event(), threading.Event()
    r = rig([(first, BET), (deeper, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.advisor.set_deadline("g", "h1:1:8", r.clock.t + 5)
    r.clock.t += 6
    r.updated()
    r.updated()
    view = r.advisor.view("g", "h1:1:8")
    assert [(a["state"], a["reason"]) for a in view["answers"]] == [("failed", "timed out")] * 2
    first.set()
    deeper.set()


def test_a_key_or_credit_failure_is_reported_until_a_call_gets_through(tmp_path):
    seen, updates = [], queue.Queue()
    script = [CallFailed("out of API credits"), CallFailed("rate limited"), BET]
    advisor = Advisor(
        FakeProvider(script),
        Ledger(tmp_path / "costs.jsonl"),
        updates.put,
        on_account=seen.append,
        clock=Clock(),
    )
    for node in ("h1:1:8", "h1:1:9", "h1:1:10"):
        request = Request(PRESETS["standard"], [], [])
        advisor.start("g", Job(node, request, SPOT), 2000.0)
        updates.get(timeout=3)
    # A rate limit says nothing about the key; an answer shows it works again.
    assert seen == ["out of API credits", None]


def test_a_new_decision_starts_a_fresh_list_of_answers(rig):
    r = rig([BET, BET])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    r.advisor.start("g", r.job(node="h1:2:10"), r.clock.t + 20)
    r.updated()
    assert [a["state"] for a in r.advisor.view("g", "h1:2:10")["answers"]] == ["done"]


def test_a_refused_call_is_never_sent(rig):
    r = rig([])
    r.advisor.start("g", r.job(), r.clock.t + 20, refuse="blocked: a real name was left")
    assert r.advisor.view("g", "h1:1:8")["reason"] == "blocked: a real name was left"
    assert r.advisor.asked("g", "h1:1:8") and r.advisor.asked("g", "h1:1:8", "standard")
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
    assert (first["model"], first["preset"]) == ("claude-opus-5-5", "standard")
    assert "Player A calls too much" in first["answer"]  # as Claude wrote it
    assert (second["state"], second["reason"], second["answer"]) == ("failed", "rate limited", None)


def claude_lines(caplog):
    return [m for m in caplog.messages if m.startswith("[VAMOS] Claude call")]


def test_every_call_gets_one_line_in_the_host_log_with_its_tokens(rig, caplog):
    caplog.set_level(logging.INFO, logger="vamos.brain")
    r = rig([BET])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    [entry] = r.logged()
    assert (entry["state"], entry["attempts"], entry["dollars"]) == ("done", 1, 0.0082)
    assert entry["usage"] == {
        "input_tokens": 100,
        "output_tokens": 300,
        "cache_read_input_tokens": 9000,
        "cache_creation_input_tokens": 0,
    }
    assert claude_lines(caplog) == [
        "[VAMOS] Claude call game=g node=h1:1:8 kind=advice preset=standard outcome=done "
        "seconds=0.00 attempts=1 input=100 cache_read=9000 cache_write=0 output=300 "
        "dollars=0.0082"
    ]
    # The ledger row names the prefix it read, so a stopped call on it can be estimated.
    [row] = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert (row["prefix"], row["usage"]) == (entry["prefix"], "reported")


def test_a_call_cancelled_before_claudes_first_event_is_still_logged(rig, caplog):
    caplog.set_level(logging.INFO, logger="vamos.brain")
    gate = threading.Event()
    r = rig([(threading.Event(), gate, BET)])
    r.advisor.start("g", r.job(message="Hand so far: Player A checks"), r.clock.t + 20)
    r.clock.t += 1.5
    r.advisor.stop("g")  # the hero acted
    [entry] = r.logged()
    assert (entry["node"], entry["state"], entry["seconds"], entry["attempts"]) == (
        "h1:1:8",
        "cancelled",
        1.5,
        1,
    )
    assert entry["spot"] == "Hand so far: Player A checks"  # sent, so kept for replays
    # Stopped before Claude's first event: Claude never says what it used.
    assert (entry["usage"], entry["dollars"]) == (None, None)
    # With no earlier call on these profiles, nothing to estimate it from: counted at $0.
    [row] = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert (row["usage"], row["dollars"], row["prefix"]) == ("unknown", 0, entry["prefix"])
    assert claude_lines(caplog) == [
        "[VAMOS] Claude call game=g node=h1:1:8 kind=advice preset=standard outcome=cancelled "
        "seconds=1.50 attempts=1 tokens=unknown estimate=none"
    ]


def test_a_call_stopped_before_claudes_first_event_is_estimated_from_the_last_like_it(rig, caplog):
    caplog.set_level(logging.INFO, logger="vamos.brain")
    gate = threading.Event()
    billed = []
    r = rig([BET, (threading.Event(), gate, BET)])
    r.advisor._on_cost = lambda: billed.append(True)
    r.advisor.start("g", r.job("h1:1:8"), r.clock.t + 20)
    r.updated()
    r.advisor.start("g", r.job("h1:2:9"), r.clock.t + 20)
    r.sent(2)
    r.advisor.stop("g")  # the hero acted while Claude had not begun to answer
    first, stopped = r.logged()
    assert stopped["prefix"] == first["prefix"]
    rows = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert [(row["usage"], row["prefix"]) for row in rows] == [
        ("reported", first["prefix"]),
        ("estimated", first["prefix"]),
    ]
    # The first call's input and cache read, without its output: 100 x $4 + 9k x $0.20.
    assert (rows[1]["input_tokens"], rows[1]["cache_read_tokens"], rows[1]["output_tokens"]) == (
        100,
        9000,
        0,
    )
    assert rows[1]["dollars"] == 0.0022
    assert billed == [True, True]  # the side panel hears of both
    assert claude_lines(caplog)[1].endswith("attempts=1 tokens=unknown estimate=0.0022")
    totals = r.ledger.totals(datetime(2026, 10, 2, 13, 30))
    assert totals["estimated"]["session"] == {"dollars": 0.0022, "calls": 1}


class StoppedAfterFirstEvent:
    """Claude's first event (and with it the input's usage) arrived, then the decision passed."""

    def __init__(self):
        self.requests = []

    def run(self, request, cancelled, replying):
        self.requests.append(request)
        replying.set()
        cancelled.wait(3)
        raise CallFailed(STOPPED, {"input_tokens": 700, "output_tokens": 1})


def test_a_call_cancelled_after_claudes_first_event_is_billed_and_logged(rig):
    r = rig([], provider=StoppedAfterFirstEvent())
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.advisor.start("g", r.job(preset="deep"), r.clock.t + 20)
    r.sent(2)
    r.advisor.stop("g")  # the hero acted while both were thinking
    entries = sorted(r.logged(), key=lambda e: e["preset"])  # each logs as its thread ends
    assert [(e["preset"], e["state"], e["dollars"]) for e in entries] == [
        ("deep", "cancelled", 0.00282),
        ("standard", "cancelled", 0.00282),
    ]
    assert entries[1]["usage"]["input_tokens"] == 700
    rows = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert [(row["kind"], row["input_tokens"]) for row in rows] == [
        ("advice", 700),
        ("advice", 700),
    ]


def test_refused_calls_are_logged_without_the_hand(rig, caplog):
    caplog.set_level(logging.INFO, logger="vamos.brain")
    r = rig([])
    hand = "Hand so far: alex checks"  # a name the check caught
    r.advisor.start("g", r.job(message=hand), r.clock.t + 20, refuse="blocked: a real name")
    r.advisor.start("g", r.job("h1:2:9", message="Hand so far"), r.clock.t + 2)
    first, second = r.logged()
    assert (first["state"], first["reason"], first["attempts"], first["seconds"]) == (
        "refused",
        "blocked: a real name",
        0,
        0,
    )
    # Never sent, so never kept: a replay must not send a prompt the name check blocked.
    assert first["spot"] is None and second["spot"] is None
    assert (second["state"], second["reason"]) == ("refused", "not enough time on the clock")
    # The card still reads it as a failure with its reason.
    assert r.advisor.view("g", "h1:2:9")["state"] == "failed"
    assert claude_lines(caplog) == [
        "[VAMOS] Claude call game=g node=h1:1:8 kind=advice preset=standard outcome=refused "
        'reason="blocked: a real name" seconds=0.00 attempts=0',
        "[VAMOS] Claude call game=g node=h1:2:9 kind=advice preset=standard outcome=refused "
        'reason="not enough time on the clock" seconds=0.00 attempts=0',
    ]
    assert r.provider.requests == []


def test_a_timed_out_call_is_logged_once(rig):
    gate = threading.Event()
    r = rig([(threading.Event(), gate, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 10)
    r.clock.t += 11
    r.updated()
    [entry] = r.logged()
    assert (entry["state"], entry["reason"], entry["seconds"]) == ("failed", "timed out", 11)
    # Stopped at the deadline before Claude's first event: counted like any stopped call.
    [row] = [json.loads(line) for line in r.ledger.path.read_text().splitlines()]
    assert row["usage"] == "unknown"


def test_a_retried_call_is_one_entry_with_both_attempts_tokens(rig):
    partial = {"input_tokens": 100, "output_tokens": 0, "cache_read_input_tokens": 9000}
    r = rig([CallFailed("Claude unavailable (529)", partial), BET])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    r.updated()
    [entry] = r.logged()
    assert (entry["state"], entry["attempts"]) == ("done", 2)
    assert (entry["usage"]["input_tokens"], entry["usage"]["cache_read_input_tokens"]) == (
        200,
        18000,
    )
    assert len(r.ledger.path.read_text().splitlines()) == 2  # each request is billed


def test_a_call_still_running_when_the_backend_closes_is_stopped_and_logged(rig):
    gate = threading.Event()
    r = rig([(gate, BET)])
    r.advisor.start("g", r.job(), r.clock.t + 20)
    [entry] = r.logged()
    assert entry["state"] == "cancelled"


def test_sizes_in_claudes_text_also_show_in_dollars():
    assert with_dollars("a 1bb bet into 28.5bb, then jam 3 bb", 20) == (
        "a $0.20 (1bb) bet into $5.70 (28.5bb), then jam $0.60 (3bb)"
    )
    # Already shown as "(3bb)", a range, a position and a 3-bet stay as written.
    kept = "call $0.60 (3bb), open 2-3bb, the BB 3-bets"
    assert with_dollars(kept, 20) == kept
    assert with_dollars("a 0.25bb chip", 20) == "a $0.05 (0.25bb) chip"
