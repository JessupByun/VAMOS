import numpy as np
import pytest

from vamos.engine.equity import equity
from vamos.preflop import matchups, multiway
from vamos.preflop.matchups import CLASSES, COMBO_COUNTS, INDEX, combos
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.tree import Terminal

PRIOR = COMBO_COUNTS / COMBO_COUNTS.sum()
PAYOFFS = ChipPayoffs()


def all_in_equity(reach: list[np.ndarray]) -> np.ndarray:
    """Our equity per class when everyone is all in, from the solver's own payoffs."""
    players = len(reach)
    # value = pot * won - spent * still_in, read off two pots with the same chips from us.
    small = Terminal(tuple([10.0] * players), tuple(range(players)), "showdown")
    large = Terminal((10.0, *[30.0] * (players - 1)), tuple(range(players)), "showdown")
    v1, v2 = PAYOFFS(small, 0, reach), PAYOFFS(large, 0, reach)
    won = (v2 - v1) / (large.pot - small.pot)
    still_in = (small.pot * won - v1) / 10.0
    return won / still_in


@pytest.mark.parametrize("players", [3, 4])
def test_shares_conserve_the_pot(players):
    # Averaged over every hand we could hold, our share against random hands is exactly 1/n.
    mine = all_in_equity([np.ones(169)] * players)
    assert PRIOR @ mine == pytest.approx(1 / players, abs=0.001)


def test_aces_against_two_random_hands():
    assert all_in_equity([np.ones(169)] * 3)[INDEX["AA"]] == pytest.approx(0.734, abs=0.005)


def test_tables_are_symmetric_in_opponents_and_shares_sum_to_one():
    tables = multiway.load()
    three, four = tables.share[3], tables.share[4]
    assert np.allclose(three.sum(axis=-1), 1, atol=1e-5)
    assert np.allclose(four.sum(axis=-1), 1, atol=1e-5)
    assert np.allclose(three, three.transpose(0, 2, 1, 3)[..., [0, 2, 1]], atol=1e-6)
    assert np.allclose(tables.link[3], tables.link[3].transpose(0, 2, 1))


def test_link_is_one_when_opponents_are_independent():
    gen = np.random.default_rng(0)
    m = gen.random((5, 4))
    m /= m.sum(axis=1, keepdims=True)
    assert np.allclose(multiway.link(1e6 * np.einsum("ib,ic->ibc", m, m)), 1)
    assert np.allclose(multiway.link(1e7 * np.einsum("ib,ic,id->ibcd", m, m, m)), 1)


@pytest.mark.parametrize("players", [3, 4])
def test_buckets_run_from_strongest_to_weakest(players):
    labels = multiway.load().labels[players]
    strength = matchups.load().equity.mean(axis=1)
    means = [strength[labels == b].mean() for b in range(labels.max() + 1)]
    assert labels[INDEX["AA"]] == 0
    assert means == sorted(means, reverse=True)


def top(fraction: float, kinds: str = "pso") -> np.ndarray:
    """The strongest `fraction` of combos among pairs (p), suited (s) and offsuit (o) hands."""
    strength = matchups.load().equity @ PRIOR
    kind = np.array(["p" if len(c) == 2 else c[2] for c in CLASSES])
    reach, combos_in = np.zeros(169), 0
    for c in np.argsort(-strength):
        if kind[c] not in kinds:
            continue
        if combos_in >= fraction * 1326:
            break
        reach[c] = 1
        combos_in += COMBO_COUNTS[c]
    return reach


RANGES = [
    (top(0.15), top(0.4)),
    (top(0.08), top(0.25)),
    (top(0.5), top(0.5)),
    (top(0.2), top(0.3), top(0.5)),
    (top(0.1), top(0.1), top(0.4)),
    # Ranges that take part of a bucket, as solved ranges do (suited hands without their
    # offsuit counterparts).
    (top(0.15, "ps"), top(0.3)),
    (top(0.2, "o"), top(0.15, "ps")),
    (top(0.12, "ps"), top(0.2, "o"), top(0.4)),
]


@pytest.mark.slow
def test_bucketed_equity_against_exact():
    # Opponents' hands within a bucket count by combos rather than by their actual range.
    # Measured 2026-10-02 with split buckets; on solved 3- and 4-way spots the error is
    # +0.7 points on average (docs/preflop.md).
    errors = []
    for ranges in RANGES:
        approx = all_in_equity([np.ones(169), *ranges])
        exact_ranges = [
            {c: 1.0 for k in np.flatnonzero(r) for c in combos(CLASSES[k])} for r in ranges
        ]
        for hand in ("AA", "AKs", "AKo", "QJs", "T9s", "76s", "22", "A5s", "K9o", "72o"):
            exact = equity(combos(hand)[0], (), exact_ranges, samples=60_000).value
            errors.append(approx[INDEX[hand]] - exact)
    errors = np.array(errors)
    print(
        f"mean {errors.mean():+.4f} mean abs {np.abs(errors).mean():.4f} max {np.abs(errors).max():.4f}"
    )
    assert np.abs(errors).mean() < MEAN_BOUND
    assert np.abs(errors).max() < MAX_BOUND


MEAN_BOUND, MAX_BOUND = 0.008, 0.03  # measured 0.0052 and 0.0193 (2026-10-02)
