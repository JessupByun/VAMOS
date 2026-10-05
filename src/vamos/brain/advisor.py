"""
Claude's advice, before or after the flop: at most one call per decision and preset, run in
the background.

Every call is stamped with the decision node it answers, and what it returns is shown only
while that node is current. When the hero acts or the hand moves on, the call is cancelled
(its stream is closed, so only tokens produced are billed) and its answer is dropped. A call
has a deadline from the table's clock, about five seconds before the hero's time runs out,
or 25 seconds after the turn began when the table has no clock. A call that misses it,
fails or answers nonsense leaves the facts on the card with the reason. A failure that may
pass on its own (Claude overloaded, a dropped connection, an unreadable answer) is tried
once more if enough time is left.

Every call that answers or fails is appended to data/advice.jsonl, under pseudonyms: the
hand as sent, Claude's raw answer and what the card showed, so real spots can be replayed
through a changed prompt (scripts/replay_advice.py; it cannot be rebuilt afterwards).
"""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from vamos.brain.answer import Answer, BadAnswer, Choice, Spot, settle
from vamos.brain.costs import Ledger, price
from vamos.brain.provider import CallFailed, Provider, Request
from vamos.engine.facts import format_bb, format_chips

log = logging.getLogger("vamos.brain")

DEFAULT_SECONDS = 25.0  # the deadline when the table has no clock
SPARE_SECONDS = 5.0  # left on the clock for the hero to act after the deadline
MIN_SECONDS = 3.0  # with less time than this, no call is started
RETRY_SECONDS = 6.0  # the least time left for a second try
RETRYABLE = ("Claude unavailable", "no connection to Claude", "unreadable answer")
MODEL_NAMES = {
    "claude-opus-5-5": "Claude Opus 5.5",
}


@dataclass(frozen=True)
class Job:
    node: str
    request: Request
    spot: Spot
    reveal: Callable[[str], str] = field(default=lambda text: text)
    kind: str = "advice"  # the cost ledger's label: advice, preflop or ask


class Call:
    def __init__(self, job: Job, deadline: float, started: float):
        self.job = job
        self.deadline = deadline
        self.started = started
        self.cancelled = threading.Event()
        self.state = "thinking"  # thinking, done, failed or cancelled
        self.answer: Answer | None = None
        self.reason: str | None = None
        self.raw: str | None = None  # Claude's answer as sent, under pseudonyms

    @property
    def model(self) -> str:
        return self.job.request.preset.model


def _label(call: "Call") -> str:
    """The model and its effort, e.g. "Claude Opus 5.5 · low"."""
    name = MODEL_NAMES.get(call.model, call.model)
    effort = call.job.request.preset.effort
    return f"{name} · {effort}" if effort else name


def describe(choice: Choice, spot: Spot) -> str:
    """The action as the card shows it, e.g. "BET 1.20 (6bb)"."""
    bb = spot.big_blind
    if choice.action in ("fold", "check"):
        return choice.action.upper()
    if choice.action == "call":
        amount = min(spot.to_call, spot.behind)
        return f"CALL {format_chips(amount, bb)} ({format_bb(amount, bb)})"
    verb = {"bet": "BET", "raise": "RAISE TO", "allin": "ALL IN"}[choice.action]
    return f"{verb} {format_chips(choice.to, bb)} ({format_bb(choice.to, bb)})"


def _answer_fields(call: "Call | None") -> dict[str, Any]:
    """What the card and the panel show of one call's answer; empty while it has none."""
    if call is None or call.answer is None:
        return dict.fromkeys(("action", "mix", "pick", "pickPct", "why", "plan")) | {
            "deviation": False
        }
    spot, answer = call.job.spot, call.answer
    return {
        "action": describe(answer.chosen, spot),
        "mix": [f"{_short(c, spot)} {round(100 * c.freq)}%" for c in answer.mix],
        "pick": answer.mix.index(answer.chosen),  # index into mix of the action drawn
        "pickPct": round(100 * answer.chosen.freq),
        # Drawn from a less frequent option than the top one.
        "deviation": answer.chosen.freq < answer.mix[0].freq - 1e-9,
        "why": answer.reason,
        "plan": answer.plan or None,
    }


def _short(choice: Choice, spot: Spot) -> str:
    if choice.action in ("bet", "raise"):
        return f"{choice.action} {format_bb(choice.to, spot.big_blind)}"
    return "all in" if choice.action == "allin" else choice.action


class Advisor:
    def __init__(
        self,
        provider: Provider,
        ledger: Ledger,
        on_update: Callable[[str], None],  # a game's advice changed
        on_cost: Callable[[], None] = lambda: None,  # a call was billed
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = datetime.now,
        advice_log: Path | None = None,
    ):
        self._provider = provider
        self._ledger = ledger
        self._advice_log = advice_log
        self._on_update = on_update
        self._on_cost = on_cost
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._ledger_lock = threading.Lock()
        self._calls: dict[str, Call] = {}  # per game, the latest call
        # Per game, the latest call that answered: an earlier answer for the same node stays
        # on the card while a deeper model is asked.
        self._answered: dict[str, Call] = {}
        # Per game, every call about the latest call's node in the order asked, so a deeper
        # answer is shown under the first one rather than in its place.
        self._node_calls: dict[str, list[Call]] = {}

    def asked(self, game_id: str, node: str, preset: str | None = None) -> bool:
        """Whether Claude was asked about `node` (with `preset`, if given)."""
        with self._lock:
            call = self._calls.get(game_id)
            if call is None or call.job.node != node:
                return False
            return preset is None or call.job.request.preset.name == preset

    def start(self, game_id: str, job: Job, deadline: float, refuse: str | None = None) -> bool:
        """
        Ask about `job` unless this node was already asked with this preset. A `refuse`
        reason records the call as failed without sending it.
        """
        with self._lock:
            current = self._calls.get(game_id)
            same_node = current is not None and current.job.node == job.node
            if same_node and current.job.request.preset == job.request.preset:
                return False
            if not same_node:
                self._answered.pop(game_id, None)
                self._node_calls.pop(game_id, None)
            if current is not None:
                self._cancel(current)
            call = Call(job, deadline, self._clock())
            self._calls[game_id] = call
            self._node_calls.setdefault(game_id, []).append(call)
            if refuse is None and deadline - self._clock() < MIN_SECONDS:
                refuse = "not enough time on the clock"
            if refuse is not None:
                call.state, call.reason = "failed", refuse
                return True
        threading.Thread(target=self._run, args=(game_id, call), daemon=True).start()
        threading.Thread(target=self._watch, args=(game_id, call), daemon=True).start()
        return True

    def set_deadline(self, game_id: str, node: str, deadline: float) -> None:
        """The table's clock was read after the call started."""
        with self._lock:
            call = self._calls.get(game_id)
            if call is not None and call.job.node == node:
                call.deadline = deadline

    def stop(self, game_id: str) -> None:
        """The decision passed: drop whatever is running or shown for this game."""
        with self._lock:
            call = self._calls.pop(game_id, None)
            self._answered.pop(game_id, None)
            self._node_calls.pop(game_id, None)
            if call is not None:
                self._cancel(call)

    def view(self, game_id: str, node: str) -> dict[str, Any] | None:
        """The card's advice for `node`, or None when Claude was not asked about it."""
        with self._lock:
            call = self._calls.get(game_id)
            if call is None or call.job.node != node:
                return None
            shown = self._answered.get(game_id)
            asked = _label(call)
            out: dict[str, Any] = {
                "source": "Claude",
                **_answer_fields(shown),  # the answer on the card: the latest one given
                "model": _label(shown) if shown else asked,  # e.g. "Claude Opus 5.5 · low"
                "asked": asked,  # the latest call's model and effort
                "preset": (shown or call).job.request.preset.name,
                "askedPreset": call.job.request.preset.name,
                "state": call.state,
                "reason": call.reason,
                # Every call about this node in the order asked, each with its own answer.
                "answers": [
                    {
                        "model": _label(c),
                        "preset": c.job.request.preset.name,
                        "state": c.state,
                        "reason": c.reason,
                        **_answer_fields(c),
                    }
                    for c in self._node_calls.get(game_id, [call])
                ],
            }
        return out

    def _cancel(self, call: Call) -> None:
        if call.state == "thinking":
            call.state = "cancelled"
        call.cancelled.set()

    def _watch(self, game_id: str, call: Call) -> None:
        while True:
            with self._lock:
                if call.state != "thinking":
                    return
                left = call.deadline - self._clock()
            if left <= 0:
                self._finish(game_id, call, reason="timed out")
                call.cancelled.set()
                return
            call.cancelled.wait(min(left, 0.25))

    def _again(self, call: Call, attempt: int, reason: str) -> bool:
        return (
            attempt == 1
            and not call.cancelled.is_set()
            and reason.startswith(RETRYABLE)
            and call.deadline - self._clock() >= RETRY_SECONDS
        )

    def _run(self, game_id: str, call: Call) -> None:
        for attempt in (1, 2):
            try:
                reply = self._provider.run(call.job.request, call.cancelled)
            except CallFailed as failure:
                self._record(call, failure.usage)
                if self._again(call, attempt, failure.reason):
                    continue
                self._finish(game_id, call, reason=failure.reason)
                return
            except Exception:
                log.exception("[VAMOS] Claude call crashed")
                self._finish(game_id, call, reason="internal error")
                return
            self._record(call, reply.usage)
            call.raw = reply.text
            try:
                # Each preset draws its own roll, so a deeper answer is a fresh draw from its
                # own mix rather than one tied to the first answer's.
                seed = f"{call.job.node}:{call.job.request.preset.name}"
                answer = settle(reply.text, call.job.spot, seed, call.job.reveal)
            except BadAnswer as error:
                log.warning("[VAMOS] unusable answer: %s", error)
                if self._again(call, attempt, "unreadable answer"):
                    continue
                self._finish(game_id, call, reason="unreadable answer")
                return
            except Exception:
                log.exception("[VAMOS] could not read Claude's answer")
                self._finish(game_id, call, reason="internal error")
                return
            self._finish(game_id, call, answer=answer)
            return

    def _finish(
        self, game_id: str, call: Call, answer: Answer | None = None, reason: str | None = None
    ) -> None:
        with self._lock:
            if call.state != "thinking":
                return  # timed out, cancelled or replaced meanwhile
            call.state = "done" if answer is not None else "failed"
            call.answer, call.reason = answer, reason
            if answer is not None:
                self._answered[game_id] = call
            current = self._calls.get(game_id) is call
        self._log_advice(game_id, call)
        if current:
            self._on_update(game_id)

    def _log_advice(self, game_id: str, call: Call) -> None:
        if self._advice_log is None:
            return
        request = call.job.request
        system = "\n".join(block["text"] for block in request.system)
        answer = call.answer
        entry = {
            "at": self._now().isoformat(timespec="seconds"),
            "game": game_id,
            "node": call.job.node,
            "kind": call.job.kind,
            "model": call.model,
            "state": call.state,
            "reason": call.reason,
            # From the call's start: how long it took, and how long it was allowed.
            "seconds": round(self._clock() - call.started, 2),
            "allowed": round(call.deadline - call.started, 1),
            # Which profiles the answer saw, without storing them again.
            "prefix": hashlib.sha256(system.encode()).hexdigest()[:16],
            "spot": request.messages[-1]["content"] if request.messages else None,
            "answer": call.raw,
            "mix": [[c.action, c.to, round(c.freq, 4)] for c in answer.mix] if answer else None,
            "chosen": [answer.chosen.action, answer.chosen.to] if answer else None,
        }
        try:
            with self._ledger_lock:
                self._advice_log.parent.mkdir(parents=True, exist_ok=True)
                with self._advice_log.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
        except OSError:
            log.exception("[VAMOS] could not log advice")

    def _record(self, call: Call, usage: dict[str, Any]) -> None:
        if not any(isinstance(v, int) and v for v in usage.values()):
            return
        try:
            with self._ledger_lock:
                self._ledger.record(price(call.model, usage, call.job.kind, self._now()))
        except Exception:
            log.exception("[VAMOS] could not record a call's cost")
            return
        self._on_cost()
