"""
A short description of the hero's hand on the board, e.g. "top pair, weak kicker + flush
draw". Draws are only listed before the river and only when a hole card is part of them.
"""

from collections import Counter
from collections.abc import Sequence

import eval7

from vamos.cards import RANKS, hand_class

_NAMES = {"T": "10", "J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}


def _name(rank: str) -> str:
    return _NAMES.get(rank, rank)


def _plural(rank: str) -> str:
    return f"{_name(rank)}s" if rank != "6" else "6s"


def _value(rank: str) -> int:
    return RANKS.index(rank)


def _straights(ranks: set[str]) -> set[int]:
    """Top ranks (as values; the wheel tops at 3) of every straight the ranks make."""
    values = {_value(r) for r in ranks}
    if 12 in values:
        values.add(-1)  # the ace also plays low
    return {top for top in range(3, 13) if all(top - i in values for i in range(5))}


def _evaluate(cards: Sequence[str]) -> int:
    return eval7.evaluate([eval7.Card(c) for c in cards])


def _pair(hero: Sequence[str], board: Sequence[str]) -> str:
    board_ranks = sorted({c[0] for c in board}, key=_value, reverse=True)
    h1, h2 = hero[0][0], hero[1][0]
    if h1 == h2:
        if _value(h1) > _value(board_ranks[0]):
            return "overpair"
        if _value(h1) < _value(board_ranks[-1]):
            return "underpair"
        return "pocket pair below the top card"
    paired = [r for r in (h1, h2) if r in board_ranks]
    if not paired:
        return f"{_name(max(h1, h2, key=_value))} high, board paired"
    rank = paired[0]
    kicker = h2 if rank == h1 else h1
    position = board_ranks.index(rank)
    if position == 0:
        unused = [r for r in reversed(RANKS) if r not in board_ranks]
        if kicker == unused[0]:
            quality = "top kicker"
        elif _value(kicker) >= _value("J"):
            quality = "good kicker"
        else:
            quality = "weak kicker"
        return f"top pair, {quality}"
    return "second pair" if position == 1 else "bottom pair"


def _made(hero: Sequence[str], board: Sequence[str]) -> str:
    category = eval7.handtype(_evaluate([*hero, *board]))
    plays_board = len(board) == 5 and _evaluate(board) == _evaluate([*hero, *board])
    if plays_board and category != "High Card":
        return f"{category.lower()} on the board"
    if category == "Pair":
        return _pair(hero, board)
    if category == "Trips":
        h1, h2 = hero[0][0], hero[1][0]
        if h1 == h2:
            return f"set of {_plural(h1)}"
        counts = Counter(c[0] for c in board)
        return "trips" if counts.get(h1, 0) == 2 or counts.get(h2, 0) == 2 else "trips on the board"
    if category == "High Card":
        return f"{_name(max(hero[0][0], hero[1][0], key=_value))} high"
    return {"Two Pair": "two pair", "Quads": "quads", "Full House": "full house"}.get(
        category, category.lower()
    )


def _draws(hero: Sequence[str], board: Sequence[str], made: str) -> list[str]:
    draws = []
    cards = [*hero, *board]
    if "flush" not in made:
        suits = Counter(c[1] for c in cards)
        if any(n == 4 and suit in (hero[0][1], hero[1][1]) for suit, n in suits.items()):
            draws.append("flush draw")
    if "straight" not in made and _straights({c[0] for c in cards}) == _straights(
        {c[0] for c in board}
    ):
        outs = 0
        for rank in RANKS:
            with_hole = _straights({c[0] for c in cards} | {rank})
            board_only = _straights({c[0] for c in board} | {rank})
            if with_hole - board_only:
                outs += 1
        if outs >= 2:
            draws.append("open-ended straight draw")
        elif outs == 1:
            draws.append("gutshot")
    return draws


def describe(hero: Sequence[str], board: Sequence[str]) -> str:
    if not board:
        return hand_class(hero[0], hero[1])
    made = _made(hero, board)
    draws = _draws(hero, board, made) if len(board) < 5 else []
    return " + ".join([made, *draws])


_KINDS = (
    "High Card",
    "Pair",
    "Two Pair",
    "Trips",
    "Straight",
    "Flush",
    "Full House",
    "Quads",
    "Straight Flush",
)
_LABELS = {"Straight": "straight", "Flush": "flush"}


def improving_cards(hero: Sequence[str], board: Sequence[str]) -> dict[str, list[str]]:
    """
    Before the river: every unseen card that gives Hero a straight, a flush, or a full house
    or better (grouped as "full house or better") that Hero does not already have, unless the
    board alone would then show the same hand. Claude is given these exact lists so it never
    counts outs, or invents draws, by itself.
    """
    if len(board) not in (3, 4):
        return {}
    dead = {*hero, *board}
    before = _KINDS.index(eval7.handtype(_evaluate([*hero, *board])))
    out: dict[str, list[str]] = {}
    for rank in reversed(RANKS):
        for suit in "shdc":
            card = rank + suit
            if card in dead:
                continue
            value = _evaluate([*hero, *board, card])
            kind = eval7.handtype(value)
            if _KINDS.index(kind) <= before or _KINDS.index(kind) < _KINDS.index("Straight"):
                continue
            new_board = [*board, card]
            if len(new_board) == 5 and _evaluate(new_board) == value:
                continue  # the board plays: everyone has it
            out.setdefault(_LABELS.get(kind, "full house or better"), []).append(card)
    return out
