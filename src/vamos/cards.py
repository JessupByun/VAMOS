"""
Cards and the 169 starting-hand classes.

A card is a two-character string, rank then suit: "As", "Td", "2c".
PokerNow writes cards as "A♠" or "10♥" in its log; `parse_log_card` converts them.
"""

RANKS = "23456789TJQKA"
SUITS = "shdc"

_SUIT_SYMBOLS = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}


def parse_log_card(text: str) -> str:
    """Convert a PokerNow log card such as "A♠" or "10♥" to "As" / "Th"."""
    text = text.strip()
    rank, suit = text[:-1], _SUIT_SYMBOLS.get(text[-1:])
    if rank == "10":
        rank = "T"
    if suit is None or len(rank) != 1 or rank not in RANKS:
        raise ValueError(f"not a PokerNow card: {text!r}")
    return rank + suit


def hand_class(card1: str, card2: str) -> str:
    """The canonical class of two hole cards: "AA", "AKs" or "AKo", order-invariant."""
    r1, r2 = card1[0], card2[0]
    if r1 == r2:
        return r1 + r2
    high, low = (r1, r2) if RANKS.index(r1) > RANKS.index(r2) else (r2, r1)
    return high + low + ("s" if card1[1] == card2[1] else "o")


def _all_hand_classes() -> tuple[str, ...]:
    ordered = RANKS[::-1]
    classes = []
    for i, high in enumerate(ordered):
        for low in ordered[i:]:
            if high == low:
                classes.append(high + low)
            else:
                classes.extend((high + low + "s", high + low + "o"))
    return tuple(classes)


ALL_HAND_CLASSES = _all_hand_classes()
