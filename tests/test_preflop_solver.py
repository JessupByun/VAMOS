import csv
from pathlib import Path

import numpy as np
import pytest

from vamos.preflop.matchups import CLASSES, INDEX
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.solver import Solver, action_values, evaluate
from vamos.preflop.tree import PUSH_FOLD, build

HRC = Path(__file__).parent / "corpus" / "hrc"
PAYOFFS = ChipPayoffs()


def solve(stack: float, iterations: int = 1000):
    solver = Solver(build(2, stack, PUSH_FOLD), PAYOFFS)
    solver.run(iterations)
    return solver.solution()


def test_push_fold_basics():
    solution = solve(10)
    assert abs(solution.ev.sum()) < 1e-9  # chips only change hands
    assert solution.nash_conv < 1e-5
    for strategy in solution.strategy.values():
        assert np.allclose(strategy.sum(axis=0), 1)
    _, push = solution.at(())
    _, call = solution.at(("SB:jam",))
    assert push[1, INDEX["AA"]] == pytest.approx(1)
    assert call[1, INDEX["AA"]] == pytest.approx(1)
    assert push[1, INDEX["72o"]] < 0.01


def hrc(kind: str) -> dict[float, dict[str, float]]:
    with (HRC / f"holdemresources_hu_{kind}.csv").open() as f:
        rows = list(csv.reader(f))
    head = [h.strip() for h in rows[0][1:]]
    return {
        round(float(r[0]), 2): {h: float(v) for h, v in zip(head, r[1:], strict=False) if v.strip()}
        for r in rows[1:]
    }


@pytest.mark.corpus
@pytest.mark.skipif(not HRC.exists(), reason="run scripts/fetch_hrc_pushfold.py")
@pytest.mark.parametrize("stack", [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 17, 20, 25])
def test_push_fold_matches_holdem_resources(stack):
    # Any hand played differently from HRC's published Nash solution must be one our solve
    # finds indifferent: both actions within 0.001bb, where equity sampling noise decides.
    push, call = hrc("push")[stack], hrc("call")[stack]
    solution = solve(stack, 2000)
    for path, reference, action in (((), push, "jam"), (("SB:jam",), call, "call")):
        node, strategy = solution.at(path)
        a = node.actions.index(action)
        values = action_values(solution.tree, PAYOFFS, solution.strategy, path)
        gap = np.abs(values[a] - values[1 - a])
        for k, hand in enumerate(CLASSES):
            if abs(strategy[a, k] - reference[hand]) > 0.05:
                assert gap[k] < 1e-3, (stack, action, hand, strategy[a, k], reference[hand])


@pytest.mark.parametrize(
    ("players", "bound"), [(3, 0.01), pytest.param(4, 0.02, marks=pytest.mark.slow)]
)
def test_chips_nearly_balance_with_more_players(players, bound):
    # Exact zero-sum only heads-up; with more players the bucketed multiway odds differ
    # slightly between seats (+0.009bb a hand three-handed after solving, 2026-10-02).
    tree = build(players, 50)
    uniform = {d.id: np.full((len(d.actions), 169), 1 / len(d.actions)) for d in tree.decisions}
    ev, _ = evaluate(tree, PAYOFFS, uniform)
    assert abs(ev.sum()) < bound
