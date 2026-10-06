"""
Claude's answer, before or after the flop: the fixed JSON shape it must return, and how
that becomes one legal action on the card.

The reason comes first in the schema, so the model states what decides the spot before
it picks the action; the plan for the next street follows. A bet or raise is sized by
to_bb, Hero's street total after it in big blinds, or when that is 0 by size_pct_pot: a
bet of that share of the pot, or a raise of that share of the pot after calling. Code, not
the model, makes the answer legal: a bet where only raising is possible counts as a raise
and the other way round, actions the spot does not allow are dropped (an all in where only
calling is allowed counts as a call), frequencies are renormalized, sizes are snapped
between the minimum and the stack (within 10% of the stack becomes all in), and one action
is chosen per decision: the most frequent one, unless it is under 60%, when Claude is close
to indifferent and the action is drawn from the mix.

Claude's frequencies are its confidence, not a solved mix: it gives 80/20 or 85/15 with a
reason that argues for the top option. Mixing only pays when the options are worth the same,
so drawing the 20% there gives away value, and against opponents who rarely study Hero's
frequencies the unpredictability buys little. Near 50/50 the options are close, so a draw
costs almost nothing and keeps Hero from always taking the same line.
"""

import json
import random
from dataclasses import dataclass

from vamos.money import Cents

ACTIONS = ("fold", "check", "call", "bet", "raise", "allin")

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "strategy": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "to_bb": {"type": "number"},
                    "size_pct_pot": {"type": "number"},
                    "freq": {"type": "number"},
                },
                "required": ["action", "to_bb", "size_pct_pot", "freq"],
                "additionalProperties": False,
            },
        },
        "plan": {"type": "string"},
    },
    "required": ["reason", "strategy", "plan"],
    "additionalProperties": False,
}

ALL_IN_WITHIN = 0.10  # a size within 10% of the stack is played as all in
DRAW_BELOW = 0.60  # a top option under this share counts as close: draw from the mix


def snap(cents: Cents, big_blind: Cents, up: bool = False) -> Cents:
    """
    A size on the quarter-big-blind grid (5 cents at 0.10/0.20), in whole cents: to the
    nearest quarter, half up, or the next one up when it must not go below `cents`.
    """
    if big_blind <= 0:
        return cents
    quarters = -(-cents * 4 // big_blind) if up else (cents * 8 + big_blind) // (2 * big_blind)
    return -(-quarters * big_blind // 4) if up else (quarters * big_blind * 2 + 4) // 8


@dataclass(frozen=True)
class Spot:
    """The betting state at the hero's decision, in cents."""

    pot: Cents  # everything in the middle, this street's bets included
    to_call: Cents
    street_bet: Cents  # what the hero has put in on this street
    price: Cents  # the street total to match
    min_raise: Cents  # the smallest raise increment allowed
    behind: Cents  # the hero's chips not yet in the pot
    big_blind: Cents
    # What the rules allow here (see spot.legal): no raise after a short all-in that did not
    # reopen betting, or when nobody else has chips left.
    legal: tuple[str, ...] = ACTIONS


@dataclass(frozen=True)
class Choice:
    action: str  # fold, check, call, bet, raise or allin
    to: Cents  # the hero's street total after the action (0 for fold and check)
    freq: float


@dataclass(frozen=True)
class Answer:
    mix: tuple[Choice, ...]  # most frequent first
    chosen: Choice
    reason: str  # the one fact that decides the spot
    plan: str  # the next street or the answer to a raise; may be empty


class BadAnswer(ValueError):
    """The response was not usable (not JSON, or nothing legal in it)."""


def _all_in(spot: Spot) -> Cents:
    return spot.street_bet + spot.behind


def _legal(spot: Spot, action: str, pct: float, to_bb: float = 0.0) -> Choice | None:
    if action == "allin" and "allin" not in spot.legal and "call" in spot.legal:
        action = "call"  # with raising closed, the most Hero can put in is a call
    # A bet and a raise are the same move: a raise once a bet stands on this street, even
    # with nothing owed (the big blind's option over limpers).
    if action == "raise" and spot.price == 0:
        action = "bet"
    elif action == "bet" and spot.price > 0:
        action = "raise"
    if action in ("bet", "raise") and action not in spot.legal and "allin" in spot.legal:
        action = "allin"  # short of a full raise, the only raise PokerNow offers is all in
    if action in ("call", "fold") and spot.to_call == 0 and "check" in spot.legal:
        action = "check"  # nothing owed: calling is checking, and folding gives up for free
    if action not in spot.legal:
        return None
    all_in = _all_in(spot)
    if action == "fold":
        return Choice("fold", 0, 0) if spot.to_call > 0 else None
    if action == "check":
        return Choice("check", 0, 0) if spot.to_call == 0 else None
    if action == "call":
        return Choice("call", min(spot.price, all_in), 0) if spot.to_call > 0 else None
    if action == "allin":
        return Choice("allin", all_in, 0) if spot.behind > 0 else None
    if spot.behind <= spot.to_call:
        return None  # nothing left to bet or raise with
    share = max(pct, 0) / 100
    if action == "bet":
        lowest = spot.big_blind
        to = round(share * spot.pot)
    else:
        lowest = spot.price + spot.min_raise
        to = spot.price + round(share * (spot.pot + spot.to_call))
    if to_bb > 0:
        to = min(round(to_bb * spot.big_blind), all_in)
    to = max(snap(to, spot.big_blind), snap(lowest, spot.big_blind, up=True))
    if to >= all_in * (1 - ALL_IN_WITHIN):
        return Choice("allin", all_in, 0)
    return Choice(action, to, 0)


def settle(text: str, spot: Spot, seed: str, reveal=lambda s: s) -> Answer:
    """Turn Claude's JSON into a legal mix and the one action the card shows."""
    try:
        data = json.loads(text)
        rows = data["strategy"]
        reason = str(data["reason"])
        plan = str(data.get("plan") or "")
    except (ValueError, KeyError, TypeError) as error:
        raise BadAnswer(f"unreadable answer: {error}") from error
    merged: dict[tuple[str, Cents], float] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            choice = _legal(
                spot,
                str(row["action"]),
                float(row["size_pct_pot"]),
                float(row.get("to_bb") or 0),
            )
            freq = max(float(row["freq"]), 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if choice is not None:
            key = (choice.action, choice.to)
            merged[key] = merged.get(key, 0.0) + freq
    total = sum(merged.values())
    if total <= 0:
        raise BadAnswer("no legal action in the answer")
    # Options under half a percent are noise ("fold 0%"): dropped before the draw.
    merged = {key: f for key, f in merged.items() if f / total >= 0.005}
    total = sum(merged.values())
    mix = tuple(
        sorted(
            (Choice(action, to, f / total) for (action, to), f in merged.items()),
            key=lambda c: -c.freq,
        )
    )
    chosen = mix[0]
    if chosen.freq < DRAW_BELOW:
        roll = random.Random(seed).random()
        chosen = mix[-1]
        for choice in mix:
            roll -= choice.freq
            if roll < 0:
                chosen = choice
                break
    return Answer(mix, chosen, reveal(reason), reveal(plan))
