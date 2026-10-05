import numpy as np

from vamos.engine.advice import preflop_advice
from vamos.engine.state import decision
from vamos.log.hands import build_hands
from vamos.preflop.lookup import Spot, Table
from vamos.preflop.matchups import INDEX

P = {k: f'"{k} @ {k * 10}"' for k in "ABC"}
ID = {k: k * 10 for k in "ABC"}


def pushfold_tables():
    """A heads-up push/fold chart where AA jams and everything else folds."""
    actions = ("fold", "raise 10")
    strategy = np.zeros((2, 169))
    strategy[0] = 1
    strategy[:, INDEX["AA"]] = [0, 1]
    return {("pushfold", 2): [Table(2, 10.0, {(): Spot("SB", actions, strategy)})]}


def spot_after(lines, hero, players="AB", stacks=None):
    """A hand dealt to `players` with A on the button; blinds 10/20, stacks in cents."""
    stacks = stacks or [1000] * len(players)
    seats = " | ".join(
        f"#{i + 1} {P[p]} ({s})" for i, (p, s) in enumerate(zip(players, stacks, strict=True))
    )
    text = [
        f"-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {P['A']}) --",
        f"Player stacks: {seats}",
        *lines,
    ]
    hand = build_hands((100 + i, line) for i, line in enumerate(text)).hands[-1]
    return hand, decision(hand, ID[hero])


HU = [f"{P['A']} posts a small blind of 10", f"{P['B']} posts a big blind of 20"]
# Three-handed with A on the button: B is the small blind, C the big blind.
THREE = [f"{P['B']} posts a small blind of 10", f"{P['C']} posts a big blind of 20"]


def advise(lines, hero, players="AB", stacks=None, tables=None):
    hand, spot = spot_after(lines, hero, players, stacks)
    return preflop_advice(hand, spot, ID[hero], tables or {})


def test_heads_up_short_stacks_use_the_push_fold_chart():
    advice = advise(["Your hand is A♠, A♦", *HU], "A", stacks=(200, 200), tables=pushfold_tables())
    assert (advice["source"], advice["action"]) == ("Chart", "ALL IN 200 (10bb)")


def test_the_strongest_hands_raise_at_a_standard_size():
    # Heads-up the small blind opens 2.5bb; three-handed first in, 3bb.
    assert advise(["Your hand is A♠, A♦", *HU], "A") == {
        "source": "Rule",
        "action": "RAISE TO 50 (2.5bb)",
        "mix": ["raise 2.5bb 100%"],
        "pick": 0,
        "pickPct": 100,
        "deviation": False,
    }
    assert advise(["Your hand is A♠, Q♦", *THREE], "A", "ABC")["action"] == "RAISE TO 60 (3bb)"
    # Over a limp the big blind isolates to 4bb.
    limped = ["Your hand is A♠, A♦", *THREE, f"{P['A']} calls 20", f"{P['B']} folds"]
    assert advise(limped, "C", "ABC")["action"] == "RAISE TO 80 (4bb)"


def test_a_three_bet_is_3x_in_position_and_4x_out_of_it():
    opened = ["Your hand is K♠, K♦", *THREE, f"{P['A']} raises to 60"]
    # The small blind is out of position against the button.
    assert advise(opened, "B", "ABC")["action"] == "RAISE TO 240 (12bb)"
    heads_up = ["Your hand is Q♠, Q♦", *HU, f"{P['A']} raises to 60"]
    assert advise(heads_up, "B")["action"] == "RAISE TO 240 (12bb)"


def test_a_raise_of_much_of_the_stack_goes_all_in():
    opened = ["Your hand is A♠, K♦", *THREE, f"{P['A']} raises to 60"]
    advice = advise(opened, "C", "ABC", stacks=(1000, 1000, 300))
    assert advice["action"] == "ALL IN 300 (15bb)"


def test_facing_an_all_in_nobody_else_can_match_the_strong_hands_call():
    jammed = [
        "Your hand is A♠, A♦",
        *THREE,
        f"{P['A']} raises to 1000 and go all in",
        f"{P['B']} folds",
    ]
    assert advise(jammed, "C", "ABC")["action"] == "CALL 980 (49bb)"


def test_trash_folds_checks_when_free_and_waits_for_claude_heads_up_or_blind_against_blind():
    facing = ["Your hand is 7♠, 2♦", *THREE, f"{P['A']} raises to 50", f"{P['B']} folds"]
    assert advise(facing, "C", "ABC")["action"] == "FOLD"
    limped = ["Your hand is 7♠, 2♦", *THREE, f"{P['A']} calls 20", f"{P['B']} calls 20"]
    assert advise(limped, "C", "ABC")["action"] == "CHECK"
    # Folded to the small blind: blind against blind, real ranges are much wider.
    assert advise(["Your hand is 7♠, 2♦", *THREE, f"{P['A']} folds"], "B", "ABC") is None
    assert advise(["Your hand is 7♠, 2♦", *HU, f"{P['A']} raises to 60"], "B") is None


def test_everything_in_between_and_strong_hands_facing_a_three_bet_go_to_claude():
    assert advise(["Your hand is 10♠, 9♠", *THREE], "A", "ABC") is None
    three_bet = ["Your hand is A♠, A♦", *THREE, f"{P['A']} raises to 60", f"{P['B']} raises to 240"]
    assert advise(three_bet, "C", "ABC") is None
    # Facing a single raise only the 3-bet set raises: JJ is Claude's.
    assert advise(["Your hand is J♠, J♦", *THREE, f"{P['A']} raises to 60"], "B", "ABC") is None


def test_nothing_after_the_flop():
    lines = [
        "Your hand is A♠, A♦",
        *HU,
        f"{P['A']} calls 20",
        f"{P['B']} checks",
        "Flop:  [K♠, 7♦, 2♣]",
    ]
    assert advise(lines, "B") is None


def test_a_re_raise_of_the_hero_goes_to_claude_but_a_limp_then_raise_is_one_raise():
    # The hero opened and got 3-bet: a 4-bet decision, Claude's even with queens.
    three_bet = ["Your hand is Q♠, Q♦", *THREE, f"{P['A']} raises to 60", f"{P['B']} raises to 240"]
    assert advise([*three_bet, f"{P['C']} folds"], "A", "ABC") is None
    # The hero limped and faces one raise: aces still raise.
    limp_raise = [
        "Your hand is A♠, A♦",
        *THREE,
        f"{P['A']} calls 20",
        f"{P['B']} folds",
        f"{P['C']} raises to 80",
    ]
    assert advise(limp_raise, "A", "ABC")["action"] == "RAISE TO 240 (12bb)"


def test_trash_does_not_fold_at_a_cheap_price():
    # A 1.25bb all-in from a short stack: 8% equity to call is not a fold for 72o.
    short = [
        "Your hand is 7♠, 2♦",
        *THREE,
        f"{P['A']} raises to 25 and go all in",
        f"{P['B']} folds",
    ]
    assert advise(short, "C", "ABC", stacks=(25, 1000, 1000)) is None


def test_a_straddle_or_dead_money_leaves_the_raises_to_claude():
    straddled = [
        "Your hand is A♠, A♦",
        f"{P['B']} posts a small blind of 10",
        f"{P['C']} posts a big blind of 20",
        f"{P['A']} posts a straddle of 40",
    ]
    assert advise(straddled, "B", "ABC") is None


def test_trash_never_folds_to_an_all_in():
    # A 3bb shove with the small blind out: 72o needs 31%, and calls a wide shover.
    jam = ["Your hand is 7♠, 2♦", *THREE, f"{P['A']} raises to 60 and go all in", f"{P['B']} folds"]
    assert advise(jam, "C", "ABC", stacks=(60, 1000, 1000)) is None


def test_facing_one_all_in_only_aces_and_kings_answer_by_rule():
    jam = [*THREE, f"{P['A']} raises to 1000 and go all in", f"{P['B']} folds"]
    assert advise(["Your hand is K♠, K♦", *jam], "C", "ABC")["action"] == "CALL 980 (49bb)"
    assert advise(["Your hand is Q♠, Q♦", *jam], "C", "ABC") is None
    assert advise(["Your hand is A♠, K♦", *jam], "C", "ABC") is None


def test_trash_folds_only_at_a_price_as_steep_as_its_source_spot():
    # The list comes from the big blind facing a 2.5bb open: 1.5 to win 5.5, 27%.
    def facing(size):
        return ["Your hand is K♠, 4♦", *THREE, f"{P['A']} raises to {size}", f"{P['B']} folds"]

    assert advise(facing(50), "C", "ABC")["action"] == "FOLD"
    assert advise(facing(60), "C", "ABC")["action"] == "FOLD"
    # A min-raise asks 22%: without rake the top of the trash list can defend.
    assert advise(facing(40), "C", "ABC") is None


def test_a_squeeze_is_not_a_blind_battle():
    # The button opened and folded to the small blind's 3-bet: the big blind faces a 3-bet.
    squeeze = [
        "Your hand is 7♠, 2♦",
        *THREE,
        f"{P['A']} raises to 60",
        f"{P['B']} raises to 240",
        f"{P['A']} folds",
    ]
    assert advise(squeeze, "C", "ABC")["action"] == "FOLD"


def test_a_raise_of_much_of_what_others_can_match_goes_all_in():
    # The big blind covers everyone: a 4x 3-bet (12bb) is most of the opener's 10bb stack.
    short_open = ["Your hand is A♠, A♦", *THREE, f"{P['A']} raises to 60", f"{P['B']} folds"]
    advice = advise(short_open, "C", "ABC", stacks=(200, 1000, 3000))
    assert advice["action"] == "ALL IN 3,000 (150bb)"


def test_below_the_shallowest_chart_the_rules_answer():
    # The chart starts at 10bb here; at 5bb the push/fold spot falls to the rules.
    advice = advise(["Your hand is A♠, A♦", *HU], "A", stacks=(100, 100), tables=pushfold_tables())
    assert (advice["source"], advice["action"]) == ("Rule", "ALL IN 100 (5bb)")
