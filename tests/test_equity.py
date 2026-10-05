from itertools import combinations

import eval7
import pytest

from vamos.engine.equity import DECK, any_two, equity

C = {c: eval7.Card(c) for c in DECK}


def brute_heads_up(hero, board, rng):
    """Reference: every weighted combo against every runout, in plain Python."""
    known = set(hero) | set(board)
    total = won = 0.0
    for (a, b), weight in rng.items():
        if a in known or b in known:
            continue
        rest = [c for c in DECK if c not in known and c not in (a, b)]
        runouts = list(combinations(rest, 5 - len(board)))
        score = 0.0
        for runout in runouts:
            cards = [C[c] for c in (*board, *runout)]
            mine = eval7.evaluate([C[hero[0]], C[hero[1]], *cards])
            theirs = eval7.evaluate([C[a], C[b], *cards])
            score += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
        won += weight * score / len(runouts)
        total += weight
    return won / total


def test_aces_against_kings_preflop():
    # Exact for As Ad against Kh Kc, enumerated over all 1,712,304 boards: 81.2555%.
    result = equity(("As", "Ad"), (), [{("Kh", "Kc"): 1.0}])
    assert result.value == pytest.approx(0.812555, abs=4 * result.stderr)
    assert result.method == "monte carlo"


def test_sampling_is_repeatable_for_a_seed():
    spot = (("As", "Ad"), (), [{("Kh", "Kc"): 1.0, ("Qd", "Qc"): 0.5}])
    assert equity(*spot).value == equity(*spot).value
    assert equity(*spot, seed=1).value != equity(*spot).value


def test_weights_are_honored_unlike_eval7s_own_helpers():
    rng = {("Ah", "Kh"): 1.0, ("Qd", "Qc"): 0.25}
    vs_ak = equity(("As", "Ad"), (), [{("Ah", "Kh"): 1.0}]).value
    vs_qq = equity(("As", "Ad"), (), [{("Qd", "Qc"): 1.0}]).value
    mixed = equity(("As", "Ad"), (), [rng]).value
    assert mixed == pytest.approx((vs_ak + 0.25 * vs_qq) / 1.25, abs=0.004)


@pytest.mark.parametrize(
    ("hero", "board"),
    [
        (("As", "Ad"), ("Ks", "7d", "2c", "9h", "3s")),
        (("Jh", "Th"), ("9h", "8c", "2h", "Kd")),
    ],
)
def test_turn_and_river_are_exact_and_weighted(hero, board):
    rng = {("Kh", "Kd"): 1.0, ("Qd", "Qc"): 0.5, ("Ah", "2d"): 0.2, ("9c", "9d"): 2.0}
    result = equity(hero, board, [rng])
    assert result.method == "exact"
    assert result.value == pytest.approx(brute_heads_up(hero, board, rng), abs=1e-12)


def test_flop_sampling_matches_brute_force_within_error():
    hero, board = ("Ac", "Kd"), ("Ks", "7d", "2c")
    rng = {("Qs", "Qh"): 1.0, ("7h", "7c"): 0.3, ("As", "Jd"): 0.7, ("Td", "9d"): 1.0}
    result = equity(hero, board, [rng])
    assert result.value == pytest.approx(
        brute_heads_up(hero, board, rng), abs=4 * result.stderr + 1e-3
    )


def test_combos_that_clash_with_known_cards_are_dropped():
    with_clash = equity(("As", "Ad"), (), [{("As", "Kh"): 1.0, ("Qd", "Qc"): 1.0}]).value
    without = equity(("As", "Ad"), (), [{("Qd", "Qc"): 1.0}]).value
    assert with_clash == pytest.approx(without, abs=0.005)


def test_multiway_aces_against_two_random_hands():
    # Known result: AA against two random hands wins about 73.5%.
    dead = ("As", "Ad")
    result = equity(dead, (), [any_two(dead), any_two(dead)], samples=20_000)
    assert result.method == "monte carlo"
    assert result.value == pytest.approx(0.735, abs=4 * result.stderr)


def test_multiway_ties_split_the_pot():
    # Royal flush on board: everyone plays the board and splits three ways.
    board = ("As", "Ks", "Qs", "Js", "Ts")
    result = equity(("2c", "3d"), board, [{("4h", "5h"): 1.0}, {("6c", "7c"): 1.0}], samples=300)
    assert result.value == pytest.approx(1 / 3)


@pytest.mark.parametrize(
    ("hero", "board", "ranges"),
    [
        (("As",), (), [{("Kh", "Kc"): 1.0}]),
        (("As", "Ad"), ("Ks",), [{("Kh", "Kc"): 1.0}]),
        (("As", "Ad"), (), []),
        (("As", "Ad"), (), [{("As", "Kc"): 1.0}]),
    ],
)
def test_bad_input_is_rejected(hero, board, ranges):
    with pytest.raises(ValueError):
        equity(hero, board, ranges)
