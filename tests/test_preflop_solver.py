import csv
from pathlib import Path

import numpy as np
import pytest

from vamos.preflop.matchups import CLASSES, INDEX
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.solver import Solver, action_values, evaluate
from vamos.preflop.tables import load_all
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


@pytest.mark.corpus
@pytest.mark.skipif(not HRC.exists(), reason="run scripts/fetch_hrc_pushfold.py")
@pytest.mark.parametrize("stack", range(2, 11))
def test_the_shipped_charts_are_worth_what_holdem_resources_is(stack):
    # The committed tables, not a fresh solve: the small blind's value with both players on
    # the shipped chart equals its value with both on HRC's published one.
    table = next(t for t in load_all()[("pushfold", 2)] if t.stack == stack)
    tree = build(2, stack, PUSH_FOLD)
    shipped = {d.id: table.spots[d.path].strategy for d in tree.decisions}
    push, call = hrc("push")[stack], hrc("call")[stack]
    theirs = {}
    for d, reference in zip(tree.decisions, (push, call), strict=True):
        aggressive = np.array([reference.get(hand, 0.0) for hand in CLASSES])
        theirs[d.id] = np.stack([1 - aggressive, aggressive])
    ours, _ = evaluate(tree, PAYOFFS, shipped)
    published, _ = evaluate(tree, PAYOFFS, theirs)
    assert ours[0] == pytest.approx(published[0], abs=1e-4)


def test_paths_must_name_the_player_to_act():
    solution = solve(10)
    with pytest.raises(ValueError):
        solution.at(("BB:jam",))
    with pytest.raises(ValueError):
        solution.at(("SB:jam", "BB:call"))  # ends at a showdown, not a decision


def test_only_hands_that_end_in_a_fold_or_an_all_in_are_valued():
    # The flop model is gone, so a tree whose pots see a flop must not be solved at raw
    # equity by mistake.
    with pytest.raises(ValueError):
        Solver(build(2, 50), PAYOFFS).run(1)


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
