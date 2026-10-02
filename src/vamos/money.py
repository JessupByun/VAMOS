"""
Chip amounts.

Amounts are parsed with Decimal and stored as integer cents, so 0.10/0.20 pots never
drift the way floats do. Conversion to big blinds happens only at the engine boundary.
"""

from decimal import Decimal, InvalidOperation

type Cents = int


def parse_chips(text: str) -> Cents | None:
    """
    Parse a PokerNow amount such as "1,250", "0.20" or "15" into cents.

    Returns None for anything that is not a plain amount, including the "All In" stack
    label and blanks. Callers decide what None means for them.
    """
    cleaned = text.replace(",", "").strip()
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    cents = value * 100
    if not value.is_finite() or value < 0 or cents != cents.to_integral_value():
        return None
    return int(cents)
