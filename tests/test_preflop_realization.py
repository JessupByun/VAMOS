import json
import sys
from pathlib import Path

import numpy as np
import pytest

from vamos.preflop import matchups, multiway
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.realization import Realization, features, heads_up_share, weights
from vamos.preflop.solver import Solver
from vamos.preflop.tree import Terminal, build

EQUITY = matchups.load().equity
THETA = np.array([2.0, 1.0, 1.0, 1.5, 0.5])


def test_features():
    assert features("AA").tolist() == [1, 0, 0, 1, 1]
    assert features("T9s")[:3].tolist() == [0, 1, 1]
    assert features("72o")[2] == 0  # four ranks apart is no longer connected


def test_shares_of_a_pot_sum_to_one():
    w_oop, w_ip = weights(THETA) * 0.8, weights(THETA)
    mine = heads_up_share(EQUITY, w_oop, w_ip, spr=8)
    theirs = heads_up_share(EQUITY, w_ip, w_oop, spr=8)
    assert np.allclose(mine + theirs.T, 1)
    assert ((mine >= 0) & (mine <= 1)).all()


def test_all_in_is_raw_equity():
    w = weights(THETA)
    assert np.allclose(heads_up_share(EQUITY, w * 0.5, w, spr=0), EQUITY)


def test_acting_last_keeps_more():
    w = np.ones(169)
    oop = heads_up_share(EQUITY, w * 0.85, w, spr=10)
    assert (oop[EQUITY > 0.01] < EQUITY[EQUITY > 0.01]).all()


def test_cap_limits_the_tilt():
    g = np.log(weights(THETA, cap=1.0))
    assert np.abs(g).max() <= 1.0
    assert np.log(weights(THETA)).max() > 1.0


def test_last_to_act_after_the_flop_is_in_position():
    model = Realization(50, theta=THETA, out_of_position=0.8)
    # Four-handed, CO (0) and BB (3) see a flop: the CO acts last.
    terminal = Terminal((3.0, 0.0, 0.5, 3.0), (0, 3), "flop")
    assert (model._factor(terminal, 0), model._factor(terminal, 3)) == (1.0, 0.8)
    # Heads-up the small blind is the button and acts last after the flop.
    hu = Terminal((3.0, 3.0), (0, 1), "flop")
    assert (model._factor(hu, 0), model._factor(hu, 1)) == (1.0, 0.8)


def test_multiway_without_hand_features_or_position_is_raw_equity():
    model = Realization(50, theta=np.zeros(5), out_of_position=1.0)
    terminal = Terminal((3.0, 3.0, 3.0), (0, 1, 2), "flop")
    flat = [np.ones((169, 40))] * 2
    assert np.allclose(model.multiway(terminal, 0, [1, 2], flat), multiway.load().share[3][..., 0])


def test_the_big_blind_defends_less_than_at_raw_equity():
    def defend(flop):
        tree = build(2, 50)
        solver = Solver(tree, ChipPayoffs(flop=flop))
        solver.run(300)
        _, strategy = solver.solution().at(("SB:raise 2.5",))
        prior = matchups.COMBO_COUNTS / 1326
        return float(prior @ (1 - strategy[0]))

    assert defend(Realization(50)) < defend(None) - 0.1


GTO_WIZARD = Path(__file__).parent / "corpus" / "gtowizard" / "hu50.json"


@pytest.mark.corpus
@pytest.mark.skipif(not GTO_WIZARD.exists(), reason="private chart data not present")
def test_held_back_gto_wizard_spot():
    # The 3.5bb spot was not used for fitting. Thresholds sit just outside what was measured
    # when the weights were fitted (2026-10-02), so this guards against regressions.
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import fit_realization as fit

    raw = json.loads(GTO_WIZARD.read_text())["spots"]
    spot = fit.load_spot(fit.TEST, raw[fit.TEST])
    model = Realization(50)
    shares = fit.predict(spot, model.theta, model.out_of_position, model.cap)
    raw_error = np.abs(fit.raw_equity(spot) - spot.target).mean()
    assert np.abs(shares - spot.target).mean() < 0.8 * raw_error
    solution = fit.solve(spot, model.theta, model.out_of_position, model.cap)
    prior = matchups.COMBO_COUNTS / 1326
    checks = {"small blind first in": (3.0, 0.70), "big blind vs 3.5bb": (5.0, 0.80)}
    for name, (ours, theirs) in fit.nodes(spot, solution).items():
        fold_gap, agreement = checks[name]
        assert abs(100 * prior @ (ours[0] - theirs[0])) < fold_gap, name
        same = prior @ (ours.argmax(axis=0) == theirs.argmax(axis=0))
        assert same >= agreement, name


@pytest.mark.slow
def test_chips_nearly_balance_three_handed_at_a_solved_strategy():
    # Every seat values each multiway flop by its own exact hand and the others' buckets,
    # weighted by the hands they actually hold there. Weighting buckets as dealt instead
    # created +0.04bb a hand three-handed (+0.11 four-handed); measured +0.0055 now.
    solver = Solver(build(3, 50), ChipPayoffs(flop=Realization(50)))
    solver.run(200)
    assert abs(solver.solution().ev.sum()) < 0.015


@pytest.mark.parametrize(("players", "sb", "bb"), [(3, 1, 2), (4, 2, 3)])
def test_the_small_blind_is_out_of_position_against_the_big_blind(players, sb, bb):
    model = Realization(50, theta=THETA, out_of_position=0.8)
    terminal = Terminal(
        tuple(1.0 if p in (sb, bb) else 0.0 for p in range(players)), (sb, bb), "flop"
    )
    assert (model._factor(terminal, sb), model._factor(terminal, bb)) == (0.8, 1.0)


def test_heads_up_position_goes_to_the_right_player():
    # The small blind acts last after the flop heads-up, so it keeps more than its raw equity.
    payoffs, raw = ChipPayoffs(flop=Realization(50)), ChipPayoffs()
    terminal = Terminal((3.0, 3.0), (0, 1), "flop")
    reach = [np.ones(169), np.ones(169)]
    prior = matchups.COMBO_COUNTS / 1326
    assert prior @ payoffs(terminal, 0, reach) > prior @ raw(terminal, 0, reach)
    assert prior @ payoffs(terminal, 1, reach) < prior @ raw(terminal, 1, reach)


def test_three_way_flops_favor_the_last_to_act_and_vanish_when_stacks_are_short():
    model = Realization(50)
    flat = [np.ones((169, 40))] * 2
    deep = Terminal((1.0, 1.0, 1.0), (0, 1, 2), "flop")  # BTN acts last
    btn = model.multiway(deep, 0, [1, 2], flat)
    sb = model.multiway(deep, 1, [0, 2], flat)
    assert btn.mean() > sb.mean()
    raw = multiway.load().share[3][..., 0]
    assert btn.mean() > raw.mean()
    shallow = Terminal((49.9, 49.9, 49.9), (0, 1, 2), "flop")  # 0.1bb behind: nearly all in
    assert np.allclose(model.multiway(shallow, 0, [1, 2], flat), raw, atol=0.01)


def test_seats_in_a_three_way_flop_value_the_pot_consistently():
    # Each seat sees its own exact hand and the others' buckets; the seats' expected values
    # should still nearly add up to zero (measured -0.02bb in this 3bb pot). Leaving the
    # opponents' position out of each seat's view breaks that (-0.35bb).
    payoffs = ChipPayoffs(flop=Realization(50))
    terminal = Terminal((1.0, 1.0, 1.0), (0, 1, 2), "flop")
    prior = matchups.COMBO_COUNTS / 1326
    total = sum(float(prior @ payoffs(terminal, p, [np.ones(169)] * 3)) for p in range(3))
    assert abs(total) < 0.05
