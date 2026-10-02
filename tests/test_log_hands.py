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


def raises(hand):
    return [(a.player[0], a.to, a.reopens) for a in hand.actions if a.kind == "raise"]


def test_a_postflop_bet_sets_the_minimum_raise():
    hand = one_hand(f"""\
-- starting hand #6 (id: h6)  No Limit Texas Hold'em (dealer: {C}) --
Player stacks: #1 {A} (1000) | #2 {B} (365) | #3 {C} (1000)
{A} posts a small blind of 10
{B} posts a big blind of 20
{C} calls 20
{A} calls 20
{B} checks
Flop:  [10♣, 6♣, 3♥]
{A} bets 300
{B} raises to 345 and go all in
{C} raises to 645
""")
    # +45 over a 300 bet is not a full raise; C's +300 over 345 is.
    assert raises(hand) == [("b", 34500, False), ("c", 64500, True)]


def test_a_short_all_in_big_blind_does_not_lower_the_price():
    hand = one_hand(f"""\
The game's big blind was changed from 20 to 10.
-- starting hand #7 (id: h7)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (1000) | #2 {B} (1) | #3 {C} (15)
{A} posts a small blind of 5
{B} posts a big blind of 1 and go all in
{C} raises to 15 and go all in
""")
    # The price is the nominal 10, so C's raise is only +5: incomplete.
    assert raises(hand) == [("c", 1500, False)]


def test_the_nominal_big_blind_is_never_learned_from_a_short_all_in_post():
    log = build_hands(
        rows(f"""\
-- starting hand #86 (id: h86)  No Limit Texas Hold'em (dealer: {C}) --
Player stacks: #1 {A} (1000) | #2 {B} (1) | #3 {C} (1000)
{A} posts a small blind of 5
{B} posts a big blind of 1 and go all in
{C} calls 10
-- starting hand #87 (id: h87)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (1000) | #3 {C} (1000)
{C} posts a small blind of 5
{A} posts a big blind of 10
{C} raises to 15 and go all in
""")
    )
    assert [h.big_blind for h in log.hands] == [None, 1000]
    assert raises(log.hands[1]) == [("c", 1500, False)]


def test_an_id_change_mid_hand_keeps_one_player_and_balances():
    henry_old, henry_new = '"Henry @ wgnNFHC2ZI"', '"Henry @ FQN9hzhzP_"'
    log = build_hands(
        rows(f"""\
-- starting hand #3 (id: h3)  No Limit Texas Hold'em (dealer: {A}) --
Player stacks: #1 {A} (1000) | #2 {henry_old} (1000)
{A} posts a small blind of 10
{henry_old} posts a big blind of 20
{A} raises to 60
The player {henry_new} changed the ID from wgnNFHC2ZI to FQN9hzhzP_ because authenticated login.
{henry_new} calls 60
Flop:  [10♣, 6♣, 3♥]
{henry_new} checks
{A} bets 60
{henry_new} folds
Uncalled bet of 60 returned to {A}
{A} collected 120 from pot
-- ending hand #3 --
""")
    )
    hand = log.hands[0]
    assert hand.balanced
    assert hand.put_in == {"aaaaaaaaaa": 12000, "FQN9hzhzP_": 6000}
    assert [s.player.id for s in hand.seats] == ["aaaaaaaaaa", "FQN9hzhzP_"]
    assert log.aliases == {"wgnNFHC2ZI": "FQN9hzhzP_"}


NEXT_HAND_WITHOUT_START = f"""\
Player stacks: #1 {A} (1100) | #2 {B} (900)
{A} posts a small blind of 10
{B} posts a big blind of 20
{A} raises to 60
"""


def test_rows_after_an_ended_hand_are_not_merged_into_it():
    log = build_hands(rows(HEADS_UP + NEXT_HAND_WITHOUT_START))
    [hand] = log.hands
    assert hand.ended and hand.balanced
    assert hand.board == ["Tc", "6c", "3h"]
    assert len(hand.actions) == 8


def test_a_gap_leaves_the_open_hand_alone_until_the_next_start():
    unfinished = "\n".join(HEADS_UP.splitlines()[:9])
    after_gap = NEXT_HAND_WITHOUT_START + (
        f"-- starting hand #9 (id: h9)  No Limit Texas Hold'em (dealer: {B}) --\n"
        f"Player stacks: #1 {A} (1000) | #2 {B} (1000)\n"
    )
    early = rows(unfinished, start=100)
    late = rows(after_gap, start=500)
    log = build_hands(early + late, gaps=[500])
    assert [h.number for h in log.hands] == [2, 9]
    first = log.hands[0]
    assert first.board == ["Tc", "6c", "3h"] and not first.ended
    assert all(a.order < 500 for a in first.actions)
