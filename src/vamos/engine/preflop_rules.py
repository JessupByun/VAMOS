"""
Preflop rules for the spots every solver agrees on, so that most preflop decisions need no
Claude call: trash folds, and the strongest hands raise. Everything else goes to Claude.

TRASH is frozen from VAMOS's own 3-handed solves at 35, 50, 75 and 100bb (retired
2026-10-05): the hands that fold at least 99% both opening on the button and in the big
blind facing a 2.5bb button open, at every one of those depths. Those solves played the
button and the big blind looser than published 6-max solver charts (docs/preflop.md), so
a hand even they fold is folded by real solvers too, apart from near break-even defends at
the bottom of a big blind's range without rake. The rule is not used heads-up or blind
against blind, where real ranges are much wider; a free check is always taken.

The raise sets are hands every solver plays for a raise at any stack and seat: the open
set first in or over limpers, the 3-bet set facing exactly one raise. Facing a 3-bet or
more, Claude decides.
"""

from vamos.brain.answer import snap
from vamos.cards import hand_class
from vamos.engine.facts import format_bb, format_chips
from vamos.engine.state import Decision, positions, seat_order
from vamos.log.hands import Hand, Street


def _hands(text: str) -> frozenset[str]:
    return frozenset(text.split())


TRASH = _hands(
    "K4o K3o K2o Q7o Q6o Q5o Q4o Q3o Q2o J7o J6o J5o J4o J3o J2o T7o T6o T5o T4o T3o T2o "
    "96o 95o 94o 93o 92o 86o 85o 84o 83s 83o 82s 82o 75o 74o 73o 72s 72o 64o 63o 62o 54o "
    "53o 52o 43o 42o 32o"
)
OPEN_ALWAYS = _hands("AA KK QQ JJ TT AKs AQs AJs KQs AKo AQo")
THREE_BET_ALWAYS = _hands("AA KK QQ AKs AKo")

# A raise that would put this share of the hero's stack in goes all in instead.
ALL_IN_SHARE = 0.4
SOURCE = "Rule"


def _blind_battle(hand: Hand, hero: str) -> bool:
    """Everyone but the two blinds has folded: a heads-up spot inside a bigger game."""
    labels = positions(hand)
    folded = hand.folded()
    if labels.get(hero) not in ("SB", "BB"):
        return False
    return all(pid in folded for pid, label in labels.items() if label not in ("SB", "BB"))


def _raise_to(hand: Hand, hero: str, spot: Decision) -> int:
    """A standard raise: open 3bb (2.5bb heads-up), isolate limpers at 4bb plus 1bb per extra
    limper, 3-bet 3x in position or 4x out of position, plus one raise per caller."""
    bb = spot.big_blind or 0
    preflop = [a for a in hand.actions if a.street == Street.PREFLOP and a.player != hero]
    raises = [a for a in preflop if a.kind == "raise"]
    if not raises:
        limpers = len({a.player for a in preflop if a.kind == "call"})
        if limpers == 0:
            return 5 * bb // 2 if spot.players_dealt == 2 else 3 * bb
        return 4 * bb + (limpers - 1) * bb
    raiser = raises[0]
    callers = len({a.player for a in preflop if a.kind == "call" and a.order > raiser.order})
    order = seat_order(hand)
    in_position = order.index(hero) > order.index(raiser.player)
    return hand.price * (3 if in_position else 4) + callers * hand.price


def _advice(action: str, short: str) -> dict:
    return {
        "source": SOURCE,
        "action": action,
        "mix": [f"{short} 100%"],
        "pick": 0,
        "pickPct": 100,
        "deviation": False,
    }


def rule(hand: Hand, spot: Decision, hero: str, legal: list[str]) -> dict | None:
    """The card's advice when a rule covers the spot, else None (Claude decides)."""
    if spot.street != Street.PREFLOP or len(spot.hero_cards) != 2 or not spot.big_blind:
        return None
    cls = hand_class(*spot.hero_cards)
    bb = spot.big_blind
    if cls in TRASH:
        if spot.to_call == 0:
            return _advice("CHECK", "check")
        if spot.players_dealt >= 3 and not _blind_battle(hand, hero):
            return _advice("FOLD", "fold")
        return None
    raises = sum(
        1
        for a in hand.actions
        if a.street == Street.PREFLOP and a.kind == "raise" and a.player != hero
    )
    strong = OPEN_ALWAYS if raises == 0 else THREE_BET_ALWAYS if raises == 1 else frozenset()
    if cls not in strong:
        return None
    all_in = hand.street_bets.get(hero, 0) + spot.behind
    shove = _advice(f"ALL IN {format_chips(all_in, bb)} ({format_bb(all_in, bb)})", "all in")
    if "raise" not in legal and "bet" not in legal:
        # Short of a full raise PokerNow offers only all in; with the raiser all in and
        # nobody left to raise, calling is the play.
        if "allin" in legal:
            return shove
        if "call" in legal:
            return _advice(
                f"CALL {format_chips(spot.to_call, bb)} ({format_bb(spot.to_call, bb)})", "call"
            )
        return None
    to = snap(_raise_to(hand, hero, spot), bb)
    if to >= all_in * ALL_IN_SHARE:
        return shove
    return _advice(
        f"RAISE TO {format_chips(to, bb)} ({format_bb(to, bb)})", f"raise {format_bb(to, bb)}"
    )
