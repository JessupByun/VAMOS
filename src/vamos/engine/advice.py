"""
Preflop advice that needs no Claude call: heads-up at 10bb or less the push/fold chart
(Nash for the game where the small blind may only jam or fold, matching HoldemResources'
published solution; deeper, the full game also limps and min-raises), otherwise
the rules in preflop_rules (trash folds, the strongest hands raise). None means nothing
here covers the spot, and Claude decides; postflop Claude always decides. The home-built
full-depth tables were retired on 2026-10-05 (docs/preflop.md).
"""

from vamos.brain.answer import snap
from vamos.brain.spot import legal
from vamos.engine import preflop_rules
from vamos.engine.facts import format_money
from vamos.engine.state import Decision
from vamos.log.hands import Hand, Street
from vamos.preflop import lookup, tables
from vamos.preflop.lookup import Table

# At or below this effective stack the push/fold chart applies: HoldemResources recommends
# pure push/fold only up to about 10bb.
PUSH_FOLD_MAX = 10.0
SOURCE = "Chart"


def preflop_advice(
    hand: Hand,
    spot: Decision,
    hero: str,
    table_sets: dict[tuple[str, int], list[Table]] | None = None,
) -> dict | None:
    """The card's advice block, or None after the flop or where Claude decides."""
    if spot.street != Street.PREFLOP or spot.effective_bb is None or not spot.big_blind:
        return None
    # The chart's game has plain blinds and something to call: a heads-up small blind who
    # also posted a missed big blind can check, which the chart cannot say.
    plain = not any(a.kind in preflop_rules.ODD_POSTS for a in hand.actions)
    if (
        spot.players_dealt == 2
        and spot.effective_bb <= PUSH_FOLD_MAX
        and spot.to_call > 0
        and plain
    ):
        chart = _push_fold(hand, spot, hero, table_sets)
        if chart is not None:
            return chart
    return preflop_rules.rule(hand, spot, hero, legal(hand, hero, spot))


def _push_fold(
    hand: Hand,
    spot: Decision,
    hero: str,
    table_sets: dict[tuple[str, int], list[Table]] | None,
) -> dict | None:
    """Heads-up jam or fold, and calling a jam, from the push/fold chart; None off its tree."""
    moves = lookup.live_moves(hand)
    chosen = (table_sets or tables.load_all()).get(("pushfold", 2))
    # Shallower than every table (a blind posted all in for less), the chart's game is not
    # the one being played.
    if moves is None or not chosen or spot.effective_bb < chosen[0].stack:
        return None
    position = "SB" if spot.players_dealt == 2 and spot.position == "BTN" else spot.position
    advice = lookup.advise(
        chosen, moves, position, spot.hero_cards, spot.effective_bb, seed=spot.node
    )
    if advice is None:
        return None
    all_in = hand.street_bets.get(hero, 0) + spot.behind
    scale = (hand.price / spot.big_blind) / advice.faced if hand.price else 1.0

    bb = spot.big_blind

    def raise_to(action: str) -> int:
        if action == "jam":
            return all_in
        return min(snap(round(lookup.size_of(action, 0) * scale * bb), bb), all_in)

    def say(action: str) -> str:
        if action in ("fold", "check"):
            return action.upper()
        if action == "call":
            return f"CALL {format_money(spot.to_call, bb)}"
        to = raise_to(action)
        verb = "ALL IN" if to >= all_in else "RAISE TO"
        return f"{verb} {format_money(to, bb)}"

    def short(action: str) -> str:
        if action in ("fold", "check", "call"):
            return action
        to = raise_to(action)
        return "all in" if to >= all_in else f"raise {format_money(to, bb)}"

    # Options that read the same on the card (two raises that both reach the stack) are one
    # option; the drawn one stays even if tiny, so the card always shows what was drawn.
    merged: dict[str, float] = {}
    for action, freq in advice.mix:
        if freq >= 0.005 or action == advice.action:
            merged[short(action)] = merged.get(short(action), 0.0) + freq
    shown = sorted(merged.items(), key=lambda item: -item[1])
    labels = [label for label, _ in shown]
    pick = labels.index(short(advice.action))
    return {
        "source": SOURCE,
        "action": say(advice.action),
        "mix": [f"{label} {round(100 * f)}%" for label, f in shown],
        "pick": pick,
        "pickPct": round(100 * shown[pick][1]),
        # Drawn from a less frequent option than the top one: a deliberate deviation.
        "deviation": shown[pick][1] < shown[0][1] - 1e-9,
    }
