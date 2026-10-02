import pytest

from vamos.money import parse_chips


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("0.20", 20),
        ("0.1", 10),
        ("15", 1500),
        ("1,250", 125000),
        ("1,250.50", 125050),
        (" 3.05 ", 305),
        ("0", 0),
    ],
)
def test_parse_chips(text, cents):
    assert parse_chips(text) == cents


@pytest.mark.parametrize("text", ["", "   ", "All In", "N/A", "-5", "0.005", "NaN", "inf"])
def test_parse_chips_rejects_non_amounts(text):
    assert parse_chips(text) is None


def test_decimal_avoids_float_drift():
    # 0.1 + 0.2 != 0.3 in floats; in cents it is exact.
    assert parse_chips("0.10") + parse_chips("0.20") == parse_chips("0.30")
