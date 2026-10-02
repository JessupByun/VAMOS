import numpy as np

from vamos.engine.advice import preflop_advice
from vamos.engine.state import decision
from vamos.log.hands import build_hands
from vamos.preflop.lookup import Spot, Table
from vamos.preflop.matchups import INDEX

P = {k: f'"{k} @ {k * 10}"' for k in "AB"}
HERO, VILLAIN = "AAAAAAAAAA", "BBBBBBBBBB"


def pure(actions, choice, hands=("AA",)):
    strategy = np.zeros((len(actions), 169))
    strategy[actions.index("fold") if "fold" in actions else 0] = 1
    for hand in hands:
        strategy[:, INDEX[hand]] = 0
        strategy[actions.index(choice), INDEX[hand]] = 1
    return strategy


ROOT = ("fold", "call", "raise 2.5", "raise 3")
ANSWER = ("fold", "call", "raise 9", "raise 12")


def tables(stack=50.0):
    mixed = pure(ROOT, "raise 3")
    mixed[:, INDEX["T9s"]] = [0, 0.4, 0, 0.6]
    spots = {
        (): Spot("SB", ROOT, mixed),
        ("SB:raise 3",): Spot("BB", ANSWER, pure(ANSWER, "raise 12")),
    }
    return {("standard", 2): [Table(2, stack, spots)], ("pushfold", 2): [Table(2, 10.0, spots)]}


def spot_after(lines, hero=HERO, stacks=(1000, 1000)):
    text = [
        f"-- starting hand #1 (id: h1)  No Limit Texas Hold'em (dealer: {P['A']}) --",
        f"Player stacks: #1 {P['A']} ({stacks[0]}) | #2 {P['B']} ({stacks[1]})",
        *lines,
    ]
    hand = build_hands((100 + i, line) for i, line in enumerate(text)).hands[-1]
    return hand, decision(hand, hero)


BLINDS = [f"{P['A']} posts a small blind of 10", f"{P['B']} posts a big blind of 20"]


def test_a_clear_raise_shows_no_mix_line():
    hand, spot = spot_after(["Your hand is A♠, A♦", *BLINDS])
    advice = preflop_advice(hand, spot, HERO, tables())
    assert advice == {
        "source": "INSTANT · preflop baseline",
        "action": "RAISE TO 60 (3bb)",
        "mix": None,
    }


def test_a_mixed_hand_shows_its_mix():
    hand, spot = spot_after(["Your hand is 10♠, 9♠", *BLINDS])
    advice = preflop_advice(hand, spot, HERO, tables())
    assert advice["mix"] == ["raise 3bb 60%", "call 40%"]
    assert advice["action"] in ("RAISE TO 60 (3bb)", "CALL 10 (0.5bb)")


def test_a_three_bet_is_scaled_to_the_live_open():
    # A 4bb open stands for the tree's 3bb open, so its 4x 3-bet (12bb) becomes 16bb.
    hand, spot = spot_after(
        ["Your hand is A♠, A♦", *BLINDS, f"{P['A']} raises to 80"], hero=VILLAIN
    )
    assert preflop_advice(hand, spot, VILLAIN, tables())["action"] == "RAISE TO 320 (16bb)"


def test_a_raise_beyond_the_stack_is_all_in():
    lines = ["Your hand is A♠, A♦", *BLINDS, f"{P['A']} raises to 60"]
    hand, spot = spot_after(lines, hero=VILLAIN, stacks=(1000, 200))
    advice = preflop_advice(hand, spot, VILLAIN, tables())
    assert advice["action"] == "ALL IN 200 (10bb)"


def test_no_advice_after_the_flop_or_off_the_tree():
    lines = [
        "Your hand is A♠, A♦",
        *BLINDS,
        f"{P['A']} calls 20",
        f"{P['B']} checks",
        "Flop:  [K♠, 7♦, 2♣]",
    ]
    hand, spot = spot_after(lines, hero=VILLAIN)
    assert preflop_advice(hand, spot, VILLAIN, tables()) is None
    hand, spot = spot_after(
        ["Your hand is A♠, A♦", *BLINDS, f"{P['A']} raises to 1000 and go all in"], hero=VILLAIN
    )
    assert preflop_advice(hand, spot, VILLAIN, tables())["action"] is None
