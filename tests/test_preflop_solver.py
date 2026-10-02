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
    ("players", "bound"), [(3, 0.015), pytest.param(4, 0.04, marks=pytest.mark.slow)]
)
def test_chips_nearly_balance_with_more_players(players, bound):
    # Exact zero-sum only heads-up; with more players the bucketed multiway odds differ
    # slightly between seats. A uniform strategy sends whole buckets into multiway pots,
    # the worst case for buckets split by hand type: +0.010bb a hand three-handed and
    # +0.032 four-handed (2026-10-02); solved strategies balance better (+0.004 three-handed).
    tree = build(players, 50)
    uniform = {d.id: np.full((len(d.actions), 169), 1 / len(d.actions)) for d in tree.decisions}
    ev, _ = evaluate(tree, PAYOFFS, uniform)
    assert abs(ev.sum()) < bound


def test_paths_must_name_the_player_to_act():
    solution = solve(10)
    with pytest.raises(ValueError):
        solution.at(("BB:jam",))
    with pytest.raises(ValueError):
        solution.at(("SB:jam", "BB:call"))  # ends at a showdown, not a decision


def test_the_average_strategy_is_weighted_by_own_reach():
    # DCFR averages each decision's strategies weighted by how often the player reaches it.
    # Without that weighting this solve sits at 1.04 mbb/hand after 300 iterations; with it,
    # 0.72 (measured 2026-10-02).
    from vamos.preflop.realization import Realization

    solver = Solver(build(2, 50), ChipPayoffs(flop=Realization(50)))
    solver.run(300)
    assert solver.solution().nash_conv * 1000 < 0.85


def test_best_response_matches_a_closed_form():
    # Heads-up push/fold at 10bb with the small blind jamming a fixed range: the big blind's
    # best response can be computed hand by hand straight from the matchup tables.
    from vamos.preflop.matchups import load
    from vamos.preflop.solver import PRIOR

    m = load()
    tree = build(2, 10, PUSH_FOLD)
    jam = (m.equity @ PRIOR > 0.55).astype(float)  # a fixed jamming range
    calls = np.full(169, 0.5)
    strategy = {
        tree.root.id: np.stack([1 - jam, jam]),
        tree.decisions[1].id: np.stack([1 - calls, calls]),
    }
    _, best = evaluate(tree, PAYOFFS, strategy)
    jams = m.given @ jam  # for each class of the big blind, how often the small blind jams
    folds = m.given @ (1 - jam)
    call = np.array([(m.given[j] * jam) @ (20 * (1 - m.equity[:, j]) - 10) for j in range(169)])
    big_blind = folds * 0.5 + np.maximum(-jams, call)
    assert best[1] == pytest.approx(PRIOR @ big_blind, abs=1e-9)
    # And the small blind's best response against the big blind's fixed calling range.
    calling = calls
    jam_value = (m.given * (1 - calling)) @ np.ones(169) * 1.0 + np.array(
        [(m.given[i] * calling) @ (20 * m.equity[i] - 10) for i in range(169)]
    )
    small_blind = np.maximum(-0.5, jam_value)
    assert best[0] == pytest.approx(PRIOR @ small_blind, abs=1e-9)


def test_action_values_agree_with_the_evs():
    solution = solve(10)
    values = action_values(solution.tree, PAYOFFS, solution.strategy, ())
    _, strategy = solution.at(())
    from vamos.preflop.solver import PRIOR

    assert PRIOR @ (strategy * values).sum(axis=0) == pytest.approx(solution.ev[0], abs=1e-9)
    # Aces gain far more by jamming than by folding (about 3.5bb at 10bb).
    assert values[1, 0] - values[0, 0] > 3


def test_hands_that_never_reach_a_spot_still_get_a_sensible_answer():
    # 72o never opens, so it never faces a 3-bet in the solve; its average there would be
    # early near-uniform iterates. A loose player can get there, so it must fold.
    from vamos.preflop.realization import Realization

    solver = Solver(build(2, 50), ChipPayoffs(flop=Realization(50)))
    solver.run(300)
    node, strategy = solver.solution().at(("SB:raise 2.5", "BB:raise 10"))
    assert strategy[node.actions.index("fold"), INDEX["72o"]] > 0.95
    assert strategy[node.actions.index("jam"), INDEX["AA"]] > 0.95
