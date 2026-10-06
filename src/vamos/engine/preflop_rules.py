"""
Preflop rules for the spots every solver agrees on: trash folds, and the strongest hands
raise. They answer about a third of preflop decisions (about one in ten heads-up, where
trash never folds by rule); Claude decides the rest.

TRASH is frozen from VAMOS's own 3-handed solves at 35, 50, 75 and 100bb (retired
2026-10-05): the hands that fold at least 99% both opening on the button and in the big
blind facing a 2.5bb button open, at every one of those depths. Those solves' flop model was
tuned on a GTO Wizard article's numbers, so the boundary is not from an allowed source; each
hand is backed separately (docs/preflop.md: Sklansky-Malmuth group 9, our equity tables);
54o, the one hand outside group 9, was taken out on 2026-10-05.
This game has no rake, and without rake the big blind defends wider against small raises.
So trash folds only at a price at
least as steep as the spot the list comes from, and never heads-up or blind against blind,
where real ranges are much wider. Facing any all-in it goes to Claude, whatever the price:
the call is decided by raw equity against the shover's range, and a short or loose shover
can make most trash a call; Claude has the shover's profile. A free check is always taken.
Facing a 3-bet, a straddle or dead money, trash still folds at a normal price.

The raise sets: the open set (Sklansky-Malmuth groups 1-2 plus AQo) first in or over
limpers, the 3-bet set (at least 63% equity against a top-15% opening range) facing
exactly one raise by someone else. Facing a single all-in
only aces and kings call or re-raise by rule; whether to call one with queens or ace-king
depends on the shover. Facing a 3-bet or more (a re-raise of the hero included), or with a
straddle or dead money, Claude decides.
"""

from vamos.brain.answer import snap
from vamos.cards import hand_class
from vamos.engine.facts import format_money
from vamos.engine.state import Decision, positions, seat_order
from vamos.log.hands import Hand, Street


def _hands(text: str) -> frozenset[str]:
    return frozenset(text.split())


TRASH = _hands(
    "K4o K3o K2o Q7o Q6o Q5o Q4o Q3o Q2o J7o J6o J5o J4o J3o J2o T7o T6o T5o T4o T3o T2o "
    "96o 95o 94o 93o 92o 86o 85o 84o 83s 83o 82s 82o 75o 74o 73o 72s 72o 64o 63o 62o 53o "
    "52o 43o 42o 32o"
)
OPEN_ALWAYS = _hands("AA KK QQ JJ TT AKs AQs AJs KQs AKo AQo")
THREE_BET_ALWAYS = _hands("AA KK QQ AKs AKo")
CALL_ALL_IN = _hands("AA KK")

# A raise that would put this share of the effective stack in goes all in instead.
ALL_IN_SHARE = 0.4
# Trash folds only when calling needs at least this share of the pot: the big blind's price
# against the 2.5bb button open the list comes from (1.5 to win 5.5). At a cheaper price (a
# min-raise, callers in between, several limpers) the call can be right without rake.
TRASH_FOLD_PRICE = 0.27
# Posts that change the betting from plain blinds: the raise rules leave these to Claude.
ODD_POSTS = {"post_straddle", "post_missed_bb", "post_missing_sb", "post_ante"}
SOURCE = "Rule"


def _blind_battle(hand: Hand, hero: str) -> bool:
    """Folded to the blinds without a chip in: a heads-up spot inside a bigger game."""
    if hand.dead_small_blind:
        return False  # no small blind in the hand, so no blind battle either
    labels = positions(hand)
    if labels.get(hero) not in ("SB", "BB"):
        return False
    folded = hand.folded()
    # An opener who folded to a squeeze does not make it one: the blinds face a 3-bet.
    entered = {
        a.player
        for a in hand.actions
        if a.street == Street.PREFLOP and a.kind in ("call", "bet", "raise")
    }
    return all(
        pid in folded and pid not in entered
        for pid, label in labels.items()
        if label not in ("SB", "BB")
    )


def _all_in_faced(hand: Hand, hero: str) -> bool:
    """The bet to match was made all in (others may still act behind)."""
    return any(
        a.street == Street.PREFLOP
        and a.kind in ("bet", "raise")
        and a.all_in
        and a.to == hand.price
        and a.player != hero
        for a in hand.actions
    )


def _reach(hand: Hand, hero: str, spot: Decision) -> int:
    """The hero's street total that someone still in can match; more would come back."""
    stacks = {s.player.id: s.stack for s in hand.seats}
    all_in = hand.street_bets.get(hero, 0) + spot.behind
    deepest = max(
        (
            stacks[pid] - hand.put_in.get(pid, 0) + hand.street_bets.get(pid, 0)
            for pid in spot.opponents
        ),
        default=all_in,
    )
    return min(all_in, deepest)


def _raise_to(hand: Hand, hero: str, spot: Decision) -> int:
    """A standard raise: open 3bb (2.5bb heads-up); isolate limpers at 4bb plus 1bb per
    limper (5bb over one: these pools call larger raises too), heads-up 4bb over the small
    blind's limp; 3-bet 3x in position or 4x out of position, plus one raise per caller."""
    bb = spot.big_blind or 0
    preflop = [a for a in hand.actions if a.street == Street.PREFLOP and a.player != hero]
    raises = [a for a in preflop if a.kind == "raise"]
    if not raises:
        limpers = len({a.player for a in preflop if a.kind == "call"})
        if limpers == 0:
            return 5 * bb // 2 if spot.players_dealt == 2 else 3 * bb
        return 4 * bb if spot.players_dealt == 2 else 4 * bb + limpers * bb
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
        price = spot.to_call / (spot.pot + spot.to_call)
        if (
            spot.players_dealt >= 3
            and price >= TRASH_FOLD_PRICE
            and not _blind_battle(hand, hero)
            and not _all_in_faced(hand, hero)
        ):
            return _advice("FOLD", "fold")
        return None
    preflop = [a for a in hand.actions if a.street == Street.PREFLOP]
    if any(a.kind in ODD_POSTS for a in preflop):
        return None
    # Every raise counts, the hero's too: a re-raise of the hero is a 3-bet or more, Claude's.
    raises = [a for a in preflop if a.kind == "raise"]
    if not raises:
        strong = OPEN_ALWAYS
    elif len(raises) == 1 and raises[0].player != hero:
        strong = CALL_ALL_IN if _all_in_faced(hand, hero) else THREE_BET_ALWAYS
    else:
        strong = frozenset()
    if cls not in strong:
        return None
    all_in = hand.street_bets.get(hero, 0) + spot.behind
    shove = _advice(f"ALL IN {format_money(all_in, bb)}", "all in")
    if "raise" not in legal and "bet" not in legal:
        # Short of a full raise PokerNow offers only all in; with the raiser all in and
        # nobody left to raise, calling is the play.
        if "allin" in legal:
            return shove
        if "call" in legal:
            return _advice(f"CALL {format_money(spot.to_call, bb)}", "call")
        return None
    # Never below the smallest raise PokerNow accepts. A raise of much of what the others can
    # still match (the hero may be far deeper than all of them) is all in.
    to = max(snap(_raise_to(hand, hero, spot), bb), snap(hand.price + hand.min_raise, bb, up=True))
    if to >= _reach(hand, hero, spot) * ALL_IN_SHARE:
        return shove
    return _advice(f"RAISE TO {format_money(to, bb)}", f"raise {format_money(to, bb)}")
