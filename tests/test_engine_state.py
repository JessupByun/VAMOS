import pytest

from vamos.engine.state import _middle_labels, decision, positions
from vamos.log.hands import Street, build_hands

P = {k: f'"{k} @ {k * 10}"' for k in "ABCDEFGHI"}


def hand(text: str):
    rows = [(100 + i, line) for i, line in enumerate(text.strip("\n").split("\n"))]
    log = build_hands(rows)
    assert not log.unknown, log.unknown
    return log.hands[-1]


def roster(*names: str, start_seat: int = 1) -> str:
    return " | ".join(f"#{start_seat + i} {P[n]} (1000)" for i, n in enumerate(names))


@pytest.mark.parametrize(
    ("count", "labels"),
    [
        (0, []),
        (1, ["CO"]),
        (2, ["HJ", "CO"]),
        (3, ["UTG", "HJ", "CO"]),
        (4, ["UTG", "LJ", "HJ", "CO"]),
        (6, ["UTG", "UTG+1", "UTG+2", "LJ", "HJ", "CO"]),
    ],
)
def test_middle_position_names(count, labels):
    assert _middle_labels(count) == labels


def test_four_handed_positions_follow_seats_from_the_button():
    h = hand(f"""\
-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {P["C"]}) --
Player stacks: {roster("A", "B", "C", "D")}
{P["D"]} posts a small blind of 10
{P["A"]} posts a big blind of 20
""")
    assert positions(h) == {
        "DDDDDDDDDD": "SB",
        "AAAAAAAAAA": "BB",
        "BBBBBBBBBB": "CO",
        "CCCCCCCCCC": "BTN",
    }


def test_heads_up_dealer_is_the_button():
    h = hand(f"""\
-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (1000) | #7 {P["B"]} (1000)
{P["A"]} posts a small blind of 10
{P["B"]} posts a big blind of 20
""")
    assert positions(h) == {"AAAAAAAAAA": "BTN", "BBBBBBBBBB": "BB"}


def test_dead_button_anchors_on_the_big_blind():
    h = hand(f"""\
-- starting hand #26 (id: h26)  No Limit Texas Hold'em (dead button) --
Player stacks: {roster("A", "B", "C")}
{P["B"]} posts a small blind of 10
{P["C"]} posts a big blind of 20
""")
    assert positions(h) == {"BBBBBBBBBB": "SB", "CCCCCCCCCC": "BB", "AAAAAAAAAA": "BTN"}


def test_decision_reports_price_pot_and_effective_stack():
    h = hand(f"""\
-- starting hand #3 (id: h3)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (500) | #2 {P["B"]} (1000) | #3 {P["C"]} (800)
Your hand is A♠, K♦
{P["B"]} posts a small blind of 10
{P["C"]} posts a big blind of 20
{P["A"]} raises to 60
{P["B"]} folds
{P["C"]} calls 60
Flop:  [K♠, 7♦, 2♣]
{P["C"]} bets 80
""")
    d = decision(h, "AAAAAAAAAA")
    assert d is not None
    assert (d.street, d.position, d.players_dealt) == (Street.FLOP, "BTN", 3)
    assert d.opponents == ("CCCCCCCCCC",)
    assert (d.pot, d.to_call, d.behind) == (21000, 8000, 44000)
    assert d.effective_bb == pytest.approx(25.0)
    assert (d.board, d.hero_cards) == (("Ks", "7d", "2c"), ("As", "Kd"))


def test_the_price_is_capped_by_the_hero_stack():
    h = hand(f"""\
-- starting hand #4 (id: h4)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (50) | #2 {P["B"]} (1000)
{P["A"]} posts a small blind of 10
{P["B"]} posts a big blind of 20
{P["A"]} calls 20
{P["B"]} raises to 400
""")
    assert decision(h, "AAAAAAAAAA").to_call == 3000


def test_no_decision_for_a_folded_or_absent_hero():
    h = hand(f"""\
-- starting hand #5 (id: h5)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (1000) | #2 {P["B"]} (1000)
{P["A"]} posts a small blind of 10
{P["B"]} posts a big blind of 20
{P["A"]} folds
""")
    assert decision(h, "AAAAAAAAAA") is None
    assert decision(h, "ZZZZZZZZZZ") is None


def test_the_node_changes_with_every_action():
    text = f"""\
-- starting hand #6 (id: h6)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (1000) | #2 {P["B"]} (1000)
{P["A"]} posts a small blind of 10
{P["B"]} posts a big blind of 20
"""
    before = decision(hand(text), "AAAAAAAAAA").node
    after = decision(hand(text + f"{P['A']} calls 20\n{P['B']} checks\n"), "AAAAAAAAAA").node
    assert before != after
