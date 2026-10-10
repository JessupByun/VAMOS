"""
Chip amounts.

Amounts are parsed with Decimal and stored as integer cents, so 0.10/0.20 pots never
drift the way floats do. Conversion to big blinds happens only at the engine boundary.
"""

import re
from decimal import Decimal

type Cents = int

# Digits with at most one decimal point: no exponents ("1e3"), signs, underscores or
# non-ASCII digits, which Decimal would otherwise accept.
_AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?")


def parse_chips(text: str) -> Cents | None:
    """
    Parse a PokerNow amount such as "1,250", "0.20" or "15" into cents.

    Returns None for anything that is not a plain amount, including the "All In" stack
    label and blanks. Callers decide what None means for them.
    """
    cleaned = text.replace(",", "").strip()
    if not _AMOUNT.fullmatch(cleaned):
        return None
    cents = Decimal(cleaned) * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)
