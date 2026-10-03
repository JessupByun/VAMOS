import pytest

from vamos.engine.facts import (
    TableView,
    current_spot,
    equity_vs,
    facts,
    format_bb,
    format_chips,
)
from vamos.log.hands import build_hands

HAND = [
    '-- starting hand #7 (id: h7)  No Limit Texas Hold\'em (dealer: "Hero @ heroid0001") --',
    'Player stacks: #1 "Hero @ heroid0001" (10.00) | #3 "V @ villain001" (10.00)',
    "Your hand is A♠, K♦",
    '"Hero @ heroid0001" posts a small blind of 0.10',
    '"V @ villain001" posts a big blind of 0.20',
    '"Hero @ heroid0001" raises to 0.60',
    '"V @ villain001" calls 0.60',
    "Flop:  [K♠, 7♦, 2♣]",
    '"V @ villain001" bets 0.40',
]
# The table at the hero's turn after the villain's flop bet: 1.20 in the pot plus 0.40 bet.
VIEW = TableView("heroid0001", ("Kd", "As"), ("Ks", "7d", "2c"), 160)


def log_of(lines):
    return build_hands((100 + i, line) for i, line in enumerate(lines))


def test_facts_once_the_log_matches_the_table():
    spot = current_spot(log_of(HAND), VIEW)
    assert spot is not None
    result = facts(spot)
    assert result["position"] == "BTN"
    assert (result["pot"], result["toCall"], result["need"]) == ("8bb", "0.40 (2bb)", 20)
    assert result["madeHand"] == "top pair, top kicker"
    assert result["equityVs"] == "a random hand"
    assert 80 <= result["equity"] <= 97
    assert result["node"] == spot.node


@pytest.mark.parametrize(
    ("lines", "view"),
    [
        # The villain's bet has not reached the log yet.
        (HAND[:-1], VIEW),
        # The flop has not reached the log yet.
        (HAND[:-2], VIEW),
        # The table shows a new hand's cards; the log still holds the last hand.
        (HAND, TableView("heroid0001", ("2h", "3h"), ("Ks", "7d", "2c"), 160)),
        # The hand is over.
        (
            [*HAND[:-1], '"V @ villain001" folds', "-- ending hand #7 --"],
            TableView("heroid0001", ("As", "Kd"), ("Ks", "7d", "2c"), 120),
        ),
    ],
)
def test_no_facts_while_the_log_lags_behind_the_table(lines, view):
    assert current_spot(log_of(lines), view) is None


THREE = [
    '-- starting hand #8 (id: h8)  No Limit Texas Hold\'em (dealer: "Hero @ heroid0001") --',
    'Player stacks: #1 "Hero @ heroid0001" (10.00) | #2 "V @ villain001" (10.00)'
    ' | #3 "W @ villain002" (10.00)',
    "Your hand is A♠, K♦",
    '"V @ villain001" posts a small blind of 0.10',
    '"W @ villain002" posts a big blind of 0.20',
    '"Hero @ heroid0001" raises to 0.60',
    '"V @ villain001" calls 0.60',
    '"W @ villain002" calls 0.60',
    "Flop:  [K♠, 7♦, 2♣]",
    '"V @ villain001" checks',
]


def test_a_fold_missing_from_the_log_holds_the_facts_back():
    # A fold changes no number on the table, so only the log's turn order shows the lag.
    view = TableView("heroid0001", ("As", "Kd"), ("Ks", "7d", "2c"), 180)
    assert current_spot(log_of(THREE), view) is None
    spot = current_spot(log_of([*THREE, '"W @ villain002" folds']), view)
    assert facts(spot)["equityVs"] == "a random hand"


def test_no_price_when_checked_to():
    view = TableView("heroid0001", ("As", "Kd"), ("Ks", "7d", "2c"), 120)
    spot = current_spot(log_of([*HAND[:-1], '"V @ villain001" checks']), view)
    result = facts(spot)
    assert (result["toCall"], result["need"]) == (None, None)


def test_no_facts_for_an_empty_log_or_a_spectator():
    assert current_spot(log_of([]), VIEW) is None
    assert current_spot(log_of(HAND), TableView("someone999", (), (), 160)) is None


@pytest.mark.parametrize(
    ("cents", "big_blind", "expected"),
    [(40, 20, "0.40"), (2000, 2000, "20"), (123400, 2000, "1,234")],
)
def test_chips_are_shown_the_way_the_table_shows_them(cents, big_blind, expected):
    assert format_chips(cents, big_blind) == expected


@pytest.mark.parametrize(
    ("cents", "big_blind", "expected"), [(160, 20, "8bb"), (30, 20, "1.5bb"), (10, 20, "0.5bb")]
)
def test_big_blinds(cents, big_blind, expected):
    assert format_bb(cents, big_blind) == expected


def test_equity_label_counts_opponents():
    assert (equity_vs(1), equity_vs(2)) == ("a random hand", "2 random hands")


def test_the_price_of_calling_an_all_in_counts_only_what_hero_can_win():
    # The villain shoves 99bb into a hero with 48bb left; the 50bb the hero cannot match goes
    # back to the villain, so calling 48 wins 52 and needs 48%, not 48/150 = 32%.
    lines = [
        '-- starting hand #8 (id: h8)  No Limit Texas Hold\'em (dealer: "V @ villain001") --',
        'Player stacks: #1 "Hero @ heroid0001" (10.00) | #3 "V @ villain001" (25.00)',
        "Your hand is 2♦, 6♣",
        '"V @ villain001" posts a small blind of 0.10',
        '"Hero @ heroid0001" posts a big blind of 0.20',
        '"V @ villain001" calls 0.20',
        '"Hero @ heroid0001" checks',
        "Flop:  [J♣, 5♥, 4♣]",
        '"Hero @ heroid0001" bets 0.20',
        '"V @ villain001" raises to 19.80 and go all in',
    ]
    view = TableView("heroid0001", ("2d", "6c"), ("Jc", "5h", "4c"), 2040)
    spot = current_spot(log_of(lines), view)
    assert spot is not None
    assert (spot.to_call, spot.winnable) == (960, 2000)
    assert facts(spot)["need"] == 48
