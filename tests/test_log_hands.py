import random

from hypothesis import given, settings
from hypothesis import strategies as st

from vamos.log.hands import Street, build_hands

A = '"Alice @ aaaaaaaaaa"'
B = '"Bob @ bbbbbbbbbb"'
C = '"Cara @ cccccccccc"'


def rows(text: str, start: int = 100) -> list[tuple[int, str]]:
    return [(start + i, line) for i, line in enumerate(text.strip("\n").split("\n"))]


def one_hand(text: str):
    log = build_hands(rows(text))
    assert not log.unknown, log.unknown
    assert len(log.hands) == 1
    return log.hands[0]


HEADS_UP = f"""\
-- starting hand #2 (id: h2)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (1000) | #2 {B} (1000)
Your hand is 4♠, 10♠
{A} posts a small blind of 10
{B} posts a big blind of 20
{A} calls 20
{B} raises to 60
{A} calls 60
Flop:  [10♣, 6♣, 3♥]
{B} bets 40
{A} raises to 140
{B} folds
Uncalled bet of 100 returned to {A}
{A} collected 200 from pot
-- ending hand #2 --
{A} shows a 4♠.
"""


def test_amounts_are_street_totals_and_the_hand_balances():
    hand = one_hand(HEADS_UP)
    adds = [(a.player[0], a.kind, a.to, a.added) for a in hand.actions if a.added]
    assert adds == [
        ("a", "post_sb", 1000, 1000),
        ("b", "post_bb", 2000, 2000),
        ("a", "call", 2000, 1000),
        ("b", "raise", 6000, 4000),
        ("a", "call", 6000, 4000),
        ("b", "bet", 4000, 4000),
        ("a", "raise", 14000, 14000),
    ]
    assert hand.pot == 20000
    assert hand.balanced
    assert hand.hero_cards == ("4s", "Ts")
    assert hand.board == ["Tc", "6c", "3h"]
    assert hand.street == Street.FLOP


def test_rows_after_the_end_still_belong_to_the_hand():
    hand = one_hand(HEADS_UP)
    assert hand.ended
    assert hand.shows == {"aaaaaaaaaa": ("4s",)}


def test_missed_big_blind_is_live_and_absorbs_a_small_blind_while_missing_sb_is_dead():
    hand = one_hand(f"""\
-- starting hand #5 (id: h5)  No Limit Texas Hold'em (dealer: {C}) --
Player stacks: #1 {A} (500) | #2 {B} (500) | #3 {C} (500)
{A} posts a small blind of 2
{B} posts a big blind of 5
{A} posts a missed big blind of 5
{C} posts a missing small blind of 2
{C} calls 5
{A} checks
{B} checks
Flop:  [K♠, 2♦, 7♣]
{A} bets 10
{B} folds
{C} folds
Uncalled bet of 10 returned to {A}
{A} collected 17 from pot
-- ending hand #5 --
""")
    assert hand.put_in == {"aaaaaaaaaa": 1500, "bbbbbbbbbb": 500, "cccccccccc": 700}
    assert hand.balanced


def test_straddle_by_the_small_blind_replaces_it():
    hand = one_hand(f"""\
-- starting hand #9 (id: h9)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (500) | #2 {B} (500)
{A} posts a small blind of 2
{B} posts a big blind of 5
{A} posts a straddle of 10
{B} folds
Uncalled bet of 5 returned to {A}
{A} collected 10 from pot
-- ending hand #9 --
""")
    assert hand.put_in["aaaaaaaaaa"] == 1000
    assert hand.balanced


def test_an_incomplete_all_in_raise_does_not_reopen_betting():
    hand = one_hand(f"""\
-- starting hand #3 (id: h3)  No Limit Texas Hold'em (dealer: {C}) --
Player stacks: #1 {A} (1000) | #2 {B} (110) | #3 {C} (1000)
{A} posts a small blind of 10
{B} posts a big blind of 20
{C} raises to 60
{A} raises to 105
{B} raises to 110 and go all in
{C} calls 110
{A} calls 110
Flop:  [10♣, 6♣, 3♥]
""")
    raises = [(a.player[0], a.to, a.reopens) for a in hand.actions if a.kind == "raise"]
    assert raises == [("c", 6000, True), ("a", 10500, True), ("b", 11000, False)]
    assert not hand.ended


def test_run_it_twice_cards_do_not_advance_the_street():
    hand = one_hand(f"""\
-- starting hand #4 (id: h4)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (100) | #2 {B} (100)
{A} posts a small blind of 10
{B} posts a big blind of 20
{A} calls 20
{B} checks
Flop:  [K♠, A♥, J♥]
{B} bets 80 and go all in
{A} calls 80 and go all in
Remaining players decide whether to run it twice.
{A} chooses to  run it twice.
{B} chooses to  run it twice.
All players in hand choose to run it twice.
Turn: K♠, A♥, J♥ [2♣]
River: K♠, A♥, J♥, 2♣ [3♦]
Turn (second run): K♠, A♥, J♥ [6♣]
River (second run): K♠, A♥, J♥, 6♣ [K♣]
{A} collected 100 from pot with Pair, A's (combination: A♥, K♠, J♥, 3♦, 2♣)
{B} collected 100 from pot with Two Pair, K's & A's on the second run  (combination: J♥, K♠, K♣, A♥, A♣)
-- ending hand #4 --
""")
    assert hand.board == ["Ks", "Ah", "Jh", "2c", "3d"]
    assert hand.second_run == ["Ks", "Ah", "Jh", "6c", "Kc"]
    assert hand.run_it_twice
    assert hand.street == Street.RIVER
    assert hand.balanced


def test_non_nlh_hands_are_skipped_whole_but_table_rows_still_apply():
    log = build_hands(
        rows(f"""\
-- starting hand #7 (id: h7)  Pot Limit Omaha Hi (dealer: {A}) --
Player stacks: #1 {A} (1000) | #2 {B} (1000)
{A} posts a small blind of 10
The game's big blind was changed from 20 to 40.
-- ending hand #7 --
-- starting hand #8 (id: h8)  No Limit Texas Hold'em (dealer: {B}) --
Player stacks: #1 {A} (1000) | #2 {B} (1000)
""")
    )
    assert [h.number for h in log.hands] == [8]
    assert log.skipped_variants == {"Pot Limit Omaha Hi": 1}
    assert log.hands[0].big_blind == 4000


def test_a_new_start_leaves_the_previous_hand_incomplete():
    log = build_hands(
        rows(f"""\
-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (1000) | #2 {B} (1000)
{A} posts a small blind of 10
-- starting hand #2 (id: h2)  No Limit Texas Hold'em (dealer: {B}) --
""")
    )
    assert [(h.number, h.ended) for h in log.hands] == [(1, False), (2, False)]


@settings(max_examples=60)
@given(
    seed=st.integers(min_value=0, max_value=10_000), repeats=st.integers(min_value=0, max_value=5)
)
def test_order_duplicates_and_paging_never_change_the_hands(seed, repeats):
    base = rows(HEADS_UP)
    rng = random.Random(seed)
    noisy = base + [rng.choice(base) for _ in range(repeats * 3)]
    rng.shuffle(noisy)
    expected, got = build_hands(base).hands[0], build_hands(noisy).hands[0]
    assert (got.actions, got.board, got.put_in, got.collected, got.shows) == (
        expected.actions,
        expected.board,
        expected.put_in,
        expected.collected,
        expected.shows,
    )
