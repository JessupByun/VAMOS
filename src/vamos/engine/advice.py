"""
Preflop advice from the baseline tables at the hero's decision. Postflop advice comes from
Claude (P3); until then the card shows facts only after the flop.
"""

from vamos.engine.facts import format_bb, format_chips
from vamos.engine.state import Decision
from vamos.log.hands import Hand, Street
from vamos.preflop import lookup, tables
from vamos.preflop.lookup import Table

PUSH_FOLD_MAX = 15.0  # at or below this effective stack, the push/fold tables apply
MIX_SHOWN_BELOW = 0.9  # the mix line is hidden when one action has this share or more
SOURCE = "INSTANT · preflop baseline"


def preflop_advice(
    hand: Hand,
    spot: Decision,
    hero: str,
    table_sets: dict[tuple[str, int], list[Table]] | None = None,
) -> dict | None:
    """The card's advice block, or None after the flop or where no table covers the spot."""
    if spot.street != Street.PREFLOP or spot.effective_bb is None or not spot.big_blind:
        return None
    moves = lookup.live_moves(hand)
    menu = "pushfold" if spot.effective_bb <= PUSH_FOLD_MAX else "standard"
    chosen = (table_sets or tables.load_all()).get((menu, spot.players_dealt))
    if moves is None or not chosen:
        return {"source": SOURCE, "action": None, "mix": None}
    position = "SB" if spot.players_dealt == 2 and spot.position == "BTN" else spot.position
    advice = lookup.advise(
        chosen, moves, position, spot.hero_cards, spot.effective_bb, seed=spot.node
    )
    if advice is None:
        return {"source": SOURCE, "action": None, "mix": None}
    all_in = hand.street_bets.get(hero, 0) + spot.behind
    scale = (hand.price / spot.big_blind) / advice.faced if hand.price else 1.0

    bb = spot.big_blind

    def raise_to(action: str) -> int:
        to = all_in if action == "jam" else round(lookup.size_of(action, 0) * scale * bb)
        return min(to, all_in)

    def say(action: str) -> str:
        if action in ("fold", "check"):
            return action.upper()
        if action == "call":
            return f"CALL {format_chips(spot.to_call, bb)} ({format_bb(spot.to_call, bb)})"
        to = raise_to(action)
        verb = "ALL IN" if to >= all_in else "RAISE TO"
        return f"{verb} {format_chips(to, bb)} ({format_bb(to, bb)})"

    def short(action: str) -> str:
        if action in ("fold", "check", "call"):
            return action
        to = raise_to(action)
        return "all in" if to >= all_in else f"raise {format_bb(to, bb)}"

    mix = None
    if advice.mix[0][1] < MIX_SHOWN_BELOW:
        mix = [f"{short(a)} {round(100 * f)}%" for a, f in advice.mix if f >= 0.005]
    return {"source": SOURCE, "action": say(advice.action), "mix": mix}
