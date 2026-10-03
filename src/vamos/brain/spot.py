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
        if not (can_wager and _may_raise(hand, hero)):
            return ["fold", "call"]
        # Short of a full raise, the only raise PokerNow offers is all in.
        full = hand.street_bets.get(hero, 0) + spot.behind >= hand.price + hand.min_raise
        return ["fold", "call", "raise", "allin"] if full else ["fold", "call", "allin"]
    return ["check"] + (["bet", "allin"] if can_wager else [])


def _bet_faced(hand: Hand, hero: str, labels: dict[str, str]) -> dict | None:
    """
    The last full bet or raise on this street by someone else, sized like Claude's own
    sizes: a bet against the pot before it, a raise on top of calling against the pot after
    the raiser's call. A short all-in after it (which does not reopen betting) is reported
    alongside, not in its place.
    """
    street = [a for a in hand.actions if a.street == hand.street]
    bb = hand.big_blind or 1
    price = 0
    committed: dict[str, int] = {}
    faced = None
    for i, action in enumerate(street):
        if action.to is None:
            continue
        aggressive = action.kind in ("bet", "raise") and action.to > price
        if aggressive and action.player != hero:
            if action.kind == "raise" and action.reopens is False and faced is not None:
                faced["then_short_all_in_to_bb"] = round(action.to / bb, 2)
            else:
                pot_before = hand.pot - sum(a.added for a in street[i:])
                after_call = pot_before + price - committed.get(action.player, 0)
                faced = {
                    "by": labels.get(action.player, "another player"),
                    "to_bb": round(action.to / bb, 2),
                    "pot_before_it_bb": round(pot_before / bb, 2),
                    "pct_of_pot": round(100 * (action.to - price) / after_call)
                    if after_call
                    else None,
                    "all_in": action.all_in,
                }
        committed[action.player] = max(committed.get(action.player, 0), action.to)
        price = max(price, action.to)
    return faced


def _acts_last(hand: Hand, hero: str) -> bool:
    """Whether Hero is in position: last to act among the players still in the hand."""
    folded = hand.folded()
    live = [pid for pid in seat_order(hand) if pid not in folded]
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
    # The stack-to-pot ratio from the start of this street, as the guide's bands mean it: a
    # bigger bet faced must not move Hero into the "committed" band by itself.
    this_street = [a for a in hand.actions if a.street == hand.street]
    pot_start = hand.pot - sum(a.added for a in this_street)
    start_stacks = [_behind(hand, pid) + hand.street_bets.get(pid, 0) for pid in spot.opponents]
    effective_start = min(spot.behind + street_bet, max(start_stacks, default=0))
    allowed = legal(hand, hero, spot)
    all_in_call = spot.to_call > 0 and spot.to_call >= spot.behind
    facts: dict = {
        "street": spot.street.name.lower(),
        "pot_bb": round(spot.pot / bb, 2),
        "to_call_bb": round(spot.to_call / bb, 2),
        "equity_needed_to_call_pct": card.get("need"),
        "effective_stack_bb": round(effective / bb, 2),
        "stack_to_pot_ratio_at_street_start": round(effective_start / pot_start, 2)
        if pot_start
        else None,
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
    if all_in_call and spot.pot + spot.to_call > spot.winnable:
        # Back to the bettor, or a side pot between others: either way not Hero's to win.
        facts["chips_hero_cannot_win_bb"] = round((spot.pot + spot.to_call - spot.winnable) / bb, 2)
    if "bet" in allowed:
        facts["min_bet_bb"] = 1.0
    if "raise" in allowed:
        facts["min_raise_to_bb"] = round((hand.price + max(hand.min_raise, bb)) / bb, 2)
    return facts
