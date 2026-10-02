"""
The hero's postflop decision in the terms Claude is asked about and its answer is checked
against: the betting state, the actions the rules allow, and the facts sent with the hand.
"""

from vamos.brain.answer import Spot
from vamos.engine.state import VOLUNTARY, Decision
from vamos.log.hands import Hand


def _behind(hand: Hand, player: str) -> int:
    stacks = {s.player.id: s.stack for s in hand.seats}
    return stacks[player] - hand.put_in.get(player, 0)


def betting(hand: Hand, hero: str, spot: Decision) -> Spot:
    return Spot(
        pot=spot.pot,
        to_call=spot.to_call,
        street_bet=hand.street_bets.get(hero, 0),
        price=hand.price,
        min_raise=max(hand.min_raise, spot.big_blind or 0),
        behind=spot.behind,
        big_blind=spot.big_blind or 0,
        legal=tuple(legal(hand, hero, spot)),
    )


def _may_raise(hand: Hand, hero: str) -> bool:
    """False once the hero has acted on this street and only a short all-in came since."""
    acted = False
    for action in hand.actions:
        if action.street != hand.street:
            continue
        if action.player == hero and action.kind in VOLUNTARY:
            acted = True
        elif action.kind == "bet" or (action.kind == "raise" and action.reopens):
            acted = False
    return not acted


def legal(hand: Hand, hero: str, spot: Decision) -> list[str]:
    # Betting more only makes sense while someone else can still put chips in.
    others = any(_behind(hand, pid) > 0 for pid in spot.opponents)
    can_wager = others and spot.behind > spot.to_call
    if spot.to_call > 0:
        raising = can_wager and _may_raise(hand, hero)
        return ["fold", "call"] + (["raise", "allin"] if raising else [])
    return ["check"] + (["bet", "allin"] if can_wager else [])


def claude_facts(hand: Hand, spot: Decision, card: dict) -> dict:
    """The engine's facts for Claude, from the same numbers as the card (`card`)."""
    bb = spot.big_blind or 1
    deepest = max((_behind(hand, pid) for pid in spot.opponents), default=0)
    effective = min(spot.behind, deepest)
    return {
        "street": spot.street.name.lower(),
        "pot_bb": round(spot.pot / bb, 2),
        "to_call_bb": round(spot.to_call / bb, 2),
        "equity_needed_to_call_pct": card.get("need"),
        "effective_stack_bb": round(effective / bb, 2),
        "stack_to_pot_ratio": round(effective / spot.pot, 2) if spot.pot else None,
        "hero_hand": card.get("madeHand"),
        "hero_equity_vs_random_hands_pct": card.get("equity"),
        "opponents_in_hand": len(spot.opponents),
    }
