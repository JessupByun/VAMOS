"""
The hero's postflop decision in the terms Claude is asked about and its answer is checked
against: the betting state, the actions the rules allow, and the facts sent with the hand.
"""

from vamos.brain.answer import Spot
from vamos.engine.madehand import improving_cards
from vamos.engine.state import VOLUNTARY, Decision, seat_order
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


def _bet_faced(hand: Hand, hero: str, labels: dict[str, str]) -> dict | None:
    """The last bet or raise on this street by someone else, sized against the pot it went into."""
    street = [a for a in hand.actions if a.street == hand.street]
    bb = hand.big_blind or 1
    price = 0
    faced = None
    for i, action in enumerate(street):
        if action.to is None:
            continue
        if action.kind in ("bet", "raise") and action.to > price and action.player != hero:
            pot_before = hand.pot - sum(a.added for a in street[i:])
            faced = {
                "by": labels.get(action.player, "another player"),
                "to_bb": round(action.to / bb, 2),
                "pot_before_it_bb": round(pot_before / bb, 2),
                "pct_of_pot": round(100 * (action.to - price) / pot_before) if pot_before else None,
                "all_in": action.all_in,
            }
        price = max(price, action.to)
    return faced


def _acts_last(hand: Hand, hero: str) -> bool:
    """Whether Hero acts last on this street among the players who can still bet."""
    folded = hand.folded()
    live = [pid for pid in seat_order(hand) if pid not in folded and _behind(hand, pid) > 0]
    return bool(live) and live[-1] == hero


def claude_facts(hand: Hand, hero: str, spot: Decision, card: dict, labels: dict[str, str]) -> dict:
    """The engine's facts for Claude, from the same numbers as the card (`card`)."""
    bb = spot.big_blind or 1
    street_bet = hand.street_bets.get(hero, 0)
    # How much more Hero could put in that someone could still match.
    reachable = max(
        (_behind(hand, pid) + hand.street_bets.get(pid, 0) - street_bet for pid in spot.opponents),
        default=0,
    )
    effective = min(spot.behind, reachable)
    allowed = legal(hand, hero, spot)
    all_in_call = spot.to_call > 0 and spot.to_call >= spot.behind
    facts: dict = {
        "street": spot.street.name.lower(),
        "pot_bb": round(spot.pot / bb, 2),
        "to_call_bb": round(spot.to_call / bb, 2),
        "equity_needed_to_call_pct": card.get("need"),
        "effective_stack_bb": round(effective / bb, 2),
        "stack_to_pot_ratio": None
        if all_in_call or not spot.pot
        else round(effective / spot.pot, 2),
        "hero_acts_last": _acts_last(hand, hero),
        "hero_hand": card.get("madeHand"),
        # Exact, so Claude never counts outs itself; empty means no such draw.
        "next_card_improves_hero_to": {
            made: {"count": len(cards), "cards": cards}
            for made, cards in improving_cards(spot.hero_cards, spot.board).items()
        },
        "opponents_in_hand": len(spot.opponents),
    }
    faced = _bet_faced(hand, hero, labels) if spot.to_call > 0 else None
    if faced:
        facts["bet_faced"] = faced
    if all_in_call:
        facts["call_puts_hero_all_in"] = True
    returned = spot.pot + spot.to_call - spot.winnable
    if returned > 0:
        facts["returned_to_bettor_if_called_bb"] = round(returned / bb, 2)
    if "bet" in allowed:
        facts["min_bet_bb"] = 1.0
    if "raise" in allowed:
        facts["min_raise_to_bb"] = round((hand.price + max(hand.min_raise, bb)) / bb, 2)
    return facts
