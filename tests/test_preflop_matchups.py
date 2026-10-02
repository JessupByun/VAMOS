import numpy as np
import pytest

from vamos.engine.equity import equity
from vamos.preflop.matchups import CLASSES, COMBO_COUNTS, INDEX, combos, load

M = load()


def test_every_class_has_its_combos():
    assert len(CLASSES) == 169
    assert COMBO_COUNTS.sum() == 1326
    assert (len(combos("AA")), len(combos("AKs")), len(combos("AKo"))) == (6, 4, 12)


def test_counts_are_symmetric_and_cover_every_compatible_pair():
    assert (M.counts == M.counts.T).all()
    # Whatever we hold, 50 cards remain: C(50, 2) = 1225 opponent combos per combo of ours.
    assert (M.counts.sum(axis=1) == COMBO_COUNTS * 1225).all()
    assert M.counts[INDEX["AA"], INDEX["AKs"]] == 12  # 6 aces pairs x 2 unblocked AKs
    assert M.counts[INDEX["AA"], INDEX["KK"]] == 36
    assert np.allclose(M.given.sum(axis=1), 1)


def test_equities_are_complementary():
    assert np.allclose(M.equity + M.equity.T, 1)
    assert np.allclose(np.diag(M.equity), 0.5)


def test_aces_against_kings():
    assert M.equity[INDEX["AA"], INDEX["KK"]] == pytest.approx(0.82, abs=0.004)


@pytest.mark.parametrize(("mine", "theirs"), [("AKs", "QJs"), ("AKo", "AQs"), ("T9s", "88")])
def test_one_representative_combo_stands_for_the_whole_class(mine, theirs):
    # Average over every combo of the row class, not just the stored representative.
    total = weight = 0.0
    for combo in combos(mine):
        live = [c for c in combos(theirs) if not set(c) & set(combo)]
        total += len(live) * equity(combo, (), [dict.fromkeys(live, 1.0)]).value
        weight += len(live)
    # Both sides are Monte Carlo (400k samples stored, 200k here): about 0.1% each.
    assert M.equity[INDEX[mine], INDEX[theirs]] == pytest.approx(total / weight, abs=0.004)
