import numpy as np
import pytest

from vamos.log.hands import build_hands
from vamos.preflop.lookup import Move, Spot, Table, advise, live_moves, table_from, translate
from vamos.preflop.matchups import INDEX
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.realization import Realization
from vamos.preflop.solver import Solver
from vamos.preflop.tree import build


def uniform(actions):
    return np.full((len(actions), 169), 1 / len(actions))


def heads_up_table(stack: float) -> Table:
    """A hand-made heads-up table with the standard menu's first two decisions."""
    root = ("fold", "call", "raise 2.5", "raise 3")
    answer = ("fold", "call", "raise 9", "raise 12")
    strategy = uniform(root)
    strategy[:, INDEX["AA"]] = [0, 0, 0, 1]
    spots = {
        (): Spot("SB", root, strategy),
        ("SB:raise 3",): Spot("BB", answer, uniform(answer)),
        ("SB:raise 2.5",): Spot("BB", ("fold", "call", "raise 7.5", "raise 10"), uniform(answer)),
    }
    return Table(2, stack, spots)


@pytest.mark.parametrize(
    ("move", "action"),
    [
        (Move("SB", "raise", 4.0, False), "raise 3"),  # a 4bb open counts as the 3bb open
        (Move("SB", "raise", 2.6, False), "raise 2.5"),
        (Move("SB", "call", 1.0, False), "call"),
        (Move("SB", "fold", None, False), "fold"),
    ],
)
def test_live_moves_map_to_the_nearest_solved_action(move, action):
    assert translate(heads_up_table(50), [move]) == (f"SB:{action}",)


def test_spots_the_tree_cannot_represent_get_no_path():
    table = heads_up_table(50)
    # An open shove where only small opens were solved.
    assert translate(table, [Move("SB", "raise", 50.0, True)]) is None
    # The big blind cannot act first.
    assert translate(table, [Move("BB", "fold", None, False)]) is None
    # Checking is not an option facing a raise.
    assert (
        translate(table, [Move("SB", "raise", 3.0, False), Move("BB", "check", None, False)])
        is None
    )


def test_advice_blends_the_two_nearest_depths():
    shallow, deep = heads_up_table(20), heads_up_table(50)
    deep.spots[()].strategy[:, INDEX["72o"]] = [1, 0, 0, 0]
    shallow.spots[()].strategy[:, INDEX["72o"]] = [0, 1, 0, 0]
    # 31.6bb is halfway between 20 and 50 in log terms.
    advice = advise([shallow, deep], [], "SB", ("7h", "2c"), depth=31.62, seed="n1")
    assert dict(advice.mix) == pytest.approx(
        {"fold": 0.5, "call": 0.5, "raise 2.5": 0, "raise 3": 0}, abs=1e-3
    )
    assert advice.depths == (20, 50)
    assert advice.action in ("fold", "call")
    # The same decision always samples the same action.
    again = advise([shallow, deep], [], "SB", ("7h", "2c"), depth=31.62, seed="n1")
    assert again.action == advice.action
    # Beyond the deepest table, the deepest one alone is used.
    assert advise([shallow, deep], [], "SB", ("As", "Ad"), depth=80, seed="n2").mix[0] == (
        "raise 3",
        1.0,
    )


def test_no_advice_when_it_is_not_the_heros_spot():
    assert advise([heads_up_table(50)], [], "BB", ("As", "Ad"), depth=50, seed="x") is None


P = {k: f'"{k} @ {k * 10}"' for k in "ABCDE"}


def hand(text: str):
    log = build_hands((100 + i, line) for i, line in enumerate(text.strip("\n").split("\n")))
    return log.hands[-1]


def test_live_moves_from_a_four_handed_hand():
    h = hand(f"""\
-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {P["B"]}) --
Player stacks: #1 {P["A"]} (1000) | #2 {P["B"]} (1000) | #3 {P["C"]} (1000) | #4 {P["D"]} (1000)
{P["C"]} posts a small blind of 10
{P["D"]} posts a big blind of 20
{P["A"]} raises to 60
{P["B"]} folds
{P["C"]} calls 60
""")
    assert live_moves(h) == [
        Move("CO", "raise", 3.0, False),
        Move("BTN", "fold", None, False),
        Move("SB", "call", 3.0, False),
    ]


def test_heads_up_button_is_the_small_blind_and_straddles_are_off_the_tree():
    hu = hand(f"""\
-- starting hand #2 (id: h2)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (1000) | #2 {P["B"]} (1000)
{P["A"]} posts a small blind of 10
{P["B"]} posts a big blind of 20
{P["A"]} calls 20
""")
    assert live_moves(hu) == [Move("SB", "call", 1.0, False)]
    straddled = hand(f"""\
-- starting hand #3 (id: h3)  No Limit Texas Hold'em (dealer: {P["A"]}) --
Player stacks: #1 {P["A"]} (1000) | #2 {P["B"]} (1000) | #3 {P["C"]} (1000)
{P["B"]} posts a small blind of 10
{P["C"]} posts a big blind of 20
{P["A"]} posts a straddle of 40
""")
    assert live_moves(straddled) is None


def test_a_solved_table_round_trips_through_lookup():
    solver = Solver(build(2, 50), ChipPayoffs(flop=Realization(50)))
    solver.run(200)
    table = table_from(solver.solution())
    advice = advise([table], [Move("SB", "raise", 3.0, False)], "BB", ("As", "Ad"), 50, "s")
    assert advice is not None
    assert advice.mix[0][0] in ("raise 9", "raise 12")  # aces 3-bet


def test_no_advice_for_a_hand_the_solve_learned_nothing_for():
    table = heads_up_table(50)
    root = table.spots[()]
    unlearned = np.zeros(169, dtype=bool)
    unlearned[INDEX["72o"]] = True
    table.spots[()] = Spot(root.position, root.actions, root.strategy, unlearned)
    assert advise([table], [], "SB", ("7h", "2c"), depth=50, seed="u") is None
    assert advise([table], [], "SB", ("As", "Ad"), depth=50, seed="u") is not None


def test_exactly_on_a_solved_depth_the_neighbor_covers_a_spot_its_own_table_does_not():
    # At exactly 50bb the 20bb table has zero weight in the blend; when the 50bb table has
    # nothing for the hand, the 20bb table answers alone instead of dividing by zero.
    shallow, deep, deeper = heads_up_table(20), heads_up_table(50), heads_up_table(100)
    root = deep.spots[()]
    unlearned = np.zeros(169, dtype=bool)
    unlearned[INDEX["72o"]] = True
    deep.spots[()] = Spot(root.position, root.actions, root.strategy, unlearned)
    shallow.spots[()].strategy[:, INDEX["72o"]] = [1, 0, 0, 0]
    advice = advise([shallow, deep, deeper], [], "SB", ("7h", "2c"), depth=50, seed="z")
    assert advice is not None and advice.mix[0] == ("fold", 1.0) and advice.depths == (20,)


def test_a_column_rounded_short_of_one_never_draws_a_zero_percent_action():
    # Stored to 0.001, AA's column here sums to 0.999 with a 0% "call" sorted last: the
    # missing 0.001 must not fall to it.
    table = heads_up_table(50)
    table.spots[()].strategy[:, INDEX["AA"]] = [0, 0, 0.826, 0.173]
    drawn = {
        advise([table], [], "SB", ("As", "Ad"), depth=50, seed=f"h{i}:0:2").action
        for i in range(5000)
    }
    assert drawn == {"raise 2.5", "raise 3"}
    mix = dict(advise([table], [], "SB", ("As", "Ad"), depth=50, seed="x").mix)
    assert sum(mix.values()) == pytest.approx(1.0)
