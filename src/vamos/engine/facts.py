"""
The facts on the action card at the hero's turn: position, pot, price, made hand.

The log arrives every couple of seconds while the table updates instantly, so facts are
only produced once the log has caught up: by the log it must be the hero's turn, and the
current hand must show the same hero cards, board and pot as the table. A fold changes
none of those numbers, which is why the log's own turn order is checked too. Until then the
card says it is syncing rather than showing numbers from an earlier moment.
"""

from dataclasses import dataclass

from vamos.engine.madehand import describe
from vamos.engine.state import Decision, decision, to_act
from vamos.log.hands import HandLog
from vamos.money import Cents


@dataclass(frozen=True)
class TableView:
    """What the table shows at the hero's turn."""

    hero_id: str
    hero_cards: tuple[str, ...]
    board: tuple[str, ...]
    pot: Cents  # everything in the middle, this street's bets included


def current_spot(log: HandLog, view: TableView) -> Decision | None:
    """The hero's decision once the log agrees with the table; None while it lags behind."""
    if not log.hands:
        return None
    hand = log.hands[-1]
    hero = log.current_id(view.hero_id)
    if to_act(hand) != hero:
        return None
    spot = decision(hand, hero)
    if spot is None or not spot.hero_cards:
        return None
    if set(spot.hero_cards) != set(view.hero_cards) or spot.board != view.board:
        return None
    return spot if spot.pot == view.pot else None


def format_chips(cents: Cents, big_blind: Cents) -> str:
    """An amount the way the table shows it: with cents when the blinds or the amount use them."""
    if big_blind % 100 or cents % 100:
        return f"{cents / 100:.2f}"
    return f"{cents // 100:,}"


def format_bb(cents: Cents, big_blind: Cents) -> str:
    return f"{cents / big_blind:.2f}".rstrip("0").rstrip(".") + "bb"


def format_money(cents: Cents, big_blind: Cents) -> str:
    """An amount in dollars and big blinds, e.g. "$0.60 (3bb)": PokerNow chips are dollars here."""
    return f"${format_chips(cents, big_blind)} ({format_bb(cents, big_blind)})"


def facts(spot: Decision) -> dict:
    bb = spot.big_blind
    to_call = None
    need = None
    if spot.to_call and bb:
        to_call = format_money(spot.to_call, bb)
        need = round(100 * spot.to_call / spot.winnable)
    return {
        "node": spot.node,
        "position": spot.position,
        "pot": format_money(spot.pot, bb) if bb else None,
        "toCall": to_call,
        "need": need,
        "madeHand": describe(spot.hero_cards, spot.board),
    }
