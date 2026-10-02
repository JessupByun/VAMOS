"""
Claude's postflop answer: the fixed JSON shape it must return, and how that becomes one
legal action on the card.

The strategy comes first in the schema so it streams before the note. Claude's sizes are
a percentage of the pot: a bet of that share of the pot, or a raise of that share of the pot
after calling. Code, not the model, makes the answer legal: actions the spot does not allow
are dropped (an all in where only calling is allowed counts as a call), frequencies are
renormalized, sizes are snapped between the minimum and the stack (within 10% of the stack
becomes all in), and one action is sampled per decision.
"""

import json
import random
from dataclasses import dataclass

from vamos.money import Cents

ACTIONS = ("fold", "check", "call", "bet", "raise", "allin")

SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "size_pct_pot": {"type": "number"},
                    "freq": {"type": "number"},
                },
                "required": ["action", "size_pct_pot", "freq"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["strategy", "note"],
    "additionalProperties": False,
}

ALL_IN_WITHIN = 0.10  # a size within 10% of the stack is played as all in


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
    note: str


class BadAnswer(ValueError):
    """The response was not usable (not JSON, or nothing legal in it)."""


def _all_in(spot: Spot) -> Cents:
    return spot.street_bet + spot.behind


def _legal(spot: Spot, action: str, pct: float) -> Choice | None:
    if action == "allin" and "allin" not in spot.legal and "call" in spot.legal:
        action = "call"  # with raising closed, the most Hero can put in is a call
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
        if spot.price > spot.street_bet:
            return None
        lowest = spot.street_bet + spot.big_blind
        to = spot.street_bet + round(share * spot.pot)
    else:
        if spot.to_call == 0:
            return None
        lowest = spot.price + spot.min_raise
        to = spot.price + round(share * (spot.pot + spot.to_call))
    to = max(to, lowest)
    if to >= all_in * (1 - ALL_IN_WITHIN):
        return Choice("allin", all_in, 0)
    return Choice(action, to, 0)


def settle(text: str, spot: Spot, seed: str, reveal=lambda s: s) -> Answer:
    """Turn Claude's JSON into a legal mix and one sampled action."""
    try:
        data = json.loads(text)
        rows = data["strategy"]
        note = str(data["note"])
    except (ValueError, KeyError, TypeError) as error:
        raise BadAnswer(f"unreadable answer: {error}") from error
    merged: dict[tuple[str, Cents], float] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            choice = _legal(spot, str(row["action"]), float(row["size_pct_pot"]))
            freq = max(float(row["freq"]), 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if choice is not None:
            key = (choice.action, choice.to)
            merged[key] = merged.get(key, 0.0) + freq
    total = sum(merged.values())
    if total <= 0:
        raise BadAnswer("no legal action in the answer")
    mix = tuple(
        sorted(
            (Choice(action, to, f / total) for (action, to), f in merged.items()),
            key=lambda c: -c.freq,
        )
    )
    roll = random.Random(seed).random()
    chosen = mix[-1]
    for choice in mix:
        roll -= choice.freq
        if roll < 0:
            chosen = choice
            break
    return Answer(mix, chosen, reveal(note))
