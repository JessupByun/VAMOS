import pytest

from vamos.engine.madehand import describe, improving_cards


@pytest.mark.parametrize(
    ("hero", "board", "expected"),
    [
        (("As", "Kd"), (), "AKo"),
        (("As", "Kd"), ("Ks", "7d", "2c"), "top pair, top kicker"),
        (("Qs", "Kd"), ("Ks", "7d", "2c"), "top pair, good kicker"),
        (("Kh", "4d"), ("Ks", "7d", "2c"), "top pair, weak kicker"),
        (("7h", "Ad"), ("Ks", "7d", "2c"), "second pair"),
        (("2h", "Ad"), ("Ks", "7d", "2c"), "bottom pair"),
        (("Ah", "Ac"), ("Ks", "7d", "2c"), "overpair"),
        (("8h", "8c"), ("Ks", "7d", "2c"), "pocket pair below the top card"),
        (("2h", "2s"), ("Ks", "7d", "3c"), "underpair"),
        (("7h", "7c"), ("Ks", "7d", "2c"), "set of 7s"),
        (("Kh", "7c"), ("Ks", "7d", "2c"), "two pair"),
        (("Kh", "3c"), ("Ks", "Kd", "2c"), "trips"),
        (("Ah", "Qc"), ("Ks", "8d", "2c"), "Ace high"),
        (("Ah", "Qc"), ("Ks", "Kd", "2c"), "Ace high, board paired"),
        (("6h", "6c"), ("6s", "Kd", "2c"), "set of 6s"),
    ],
)
def test_made_hands(hero, board, expected):
    assert describe(hero, board) == expected


@pytest.mark.parametrize(
    ("hero", "board", "expected"),
    [
        (("Ah", "Qh"), ("Kh", "7h", "2c"), "Ace high + flush draw"),
        (("9h", "8c"), ("7s", "6d", "2c"), "9 high + open-ended straight draw"),
        (("9h", "8c"), ("Js", "7d", "2c"), "9 high + gutshot"),
        (("Jh", "Th"), ("9h", "8h", "2c"), "Jack high + flush draw + open-ended straight draw"),
        (("Kh", "4h"), ("Ks", "7h", "2h"), "top pair, weak kicker + flush draw"),
        (("Ah", "2c"), ("3s", "4d", "Kc"), "Ace high + gutshot"),
    ],
)
def test_draws_are_listed_before_the_river(hero, board, expected):
    assert describe(hero, board) == expected


def test_no_draws_on_the_river():
    assert describe(("Ah", "Qh"), ("Kh", "7h", "2c", "9s", "3d")) == "Ace high"


def test_a_draw_made_only_by_the_board_is_not_the_heros():
    # The board alone holds four to a straight; a hole card does not change that.
    assert describe(("2h", "2c"), ("9s", "8d", "7c", "6h")) == "underpair"


@pytest.mark.parametrize(
    ("hero", "board", "expected"),
    [
        (("2c", "3d"), ("As", "Ks", "Qs", "Js", "Ts"), "straight flush on the board"),
        (("Ah", "Kh"), ("Qh", "Jh", "Th", "2c", "3d"), "straight flush"),
        (("9c", "9d"), ("9s", "9h", "Ks", "2c", "3d"), "quads"),
        (("Kc", "Kd"), ("Ks", "2h", "2s", "7c", "8d"), "full house"),
        (("Ac", "4d"), ("5s", "6h", "7s", "8c", "Kd"), "straight"),
    ],
)
def test_big_hands(hero, board, expected):
    assert describe(hero, board) == expected


def test_improving_cards_are_exact():
    # A gutshot: only a 3 makes the straight (a 7 does not).
    assert improving_cards(["2d", "6c"], ["4c", "5h", "Jc"]) == {
        "straight": ["3s", "3h", "3d", "3c"]
    }
    # Bottom pair with no straight or flush draw at all.
    assert improving_cards(["Td", "4d"], ["5h", "4s", "6s"]) == {}
    # A nut flush draw: the nine other hearts.
    hearts = improving_cards(["Ah", "Kh"], ["2h", "7h", "9c"])
    assert len(hearts["flush"]) == 9 and all(c[1] == "h" for c in hearts["flush"])
    # A set improves to a full house or better on any card pairing the board or making quads.
    assert len(improving_cards(["9s", "9d"], ["9c", "5h", "2d"])["full house or better"]) == 7
    # A 4 or a 9 puts a straight on the board for everyone, so it is no out for aces.
    assert improving_cards(["As", "Ad"], ["5c", "6d", "7h", "8s"]) == {}
    # On the river there is nothing to come.
    assert improving_cards(["As", "Kd"], ["Qs", "8h", "4c", "2d", "7s"]) == {}
