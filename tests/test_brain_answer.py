import json

import pytest

from vamos.brain.answer import BadAnswer, Choice, Spot, settle

# Facing a 0.40 bet into 1.20 (pot 1.60 with the bet), 9.40 behind, blinds 0.10/0.20.
FACING = Spot(pot=160, to_call=40, street_bet=0, price=40, min_raise=40, behind=940, big_blind=20)
# Checked to us: pot 1.20, nothing to call.
CHECKED = Spot(pot=120, to_call=0, street_bet=0, price=0, min_raise=20, behind=940, big_blind=20)


def answer(*rows, note="ok"):
    return json.dumps(
        {
            "strategy": [{"action": a, "size_pct_pot": s, "freq": f} for a, s, f in rows],
            "note": note,
        }
    )


def test_a_pure_call():
    a = settle(answer(("call", 0, 1)), FACING, seed="n")
    assert a.chosen == Choice("call", 40, 1.0)
    assert a.note == "ok"


def test_illegal_actions_are_dropped_and_frequencies_renormalized():
    # Checking is not possible facing a bet; betting is not possible either.
    a = settle(
        answer(("check", 0, 0.5), ("bet", 50, 0.2), ("call", 0, 0.2), ("fold", 0, 0.1)), FACING, "n"
    )
    assert [(c.action, round(c.freq, 3)) for c in a.mix] == [("call", 0.667), ("fold", 0.333)]


def test_bet_and_raise_sizes_are_pot_relative():
    bet = settle(answer(("bet", 50, 1)), CHECKED, "n").chosen
    assert (bet.action, bet.to) == ("bet", 60)  # half of 1.20
    raised = settle(answer(("raise", 100, 1)), FACING, "n").chosen
    # Pot-size raise: call 0.40, then the pot after calling (2.00) on top -> to 2.40.
    assert (raised.action, raised.to) == ("raise", 240)


def test_sizes_are_snapped_to_the_minimum_and_to_all_in_near_the_stack():
    tiny = settle(answer(("raise", 1, 1)), FACING, "n").chosen
    assert tiny.to == 80  # min raise: 0.40 more than the 0.40 bet
    huge = settle(answer(("bet", 760, 1)), CHECKED, "n").chosen  # 9.12 of 9.40 behind
    assert (huge.action, huge.to) == ("allin", 940)


def test_equal_actions_after_snapping_merge():
    a = settle(answer(("bet", 760, 0.3), ("allin", 0, 0.3), ("check", 0, 0.4)), CHECKED, "n")
    assert [(c.action, round(c.freq, 2)) for c in a.mix] == [("allin", 0.6), ("check", 0.4)]


def test_the_sample_follows_the_frequencies_and_is_stable_per_decision():
    mixed = answer(("call", 0, 0.5), ("fold", 0, 0.5))
    picks = {settle(mixed, FACING, seed=f"node{k}").chosen.action for k in range(40)}
    assert picks == {"call", "fold"}
    assert settle(mixed, FACING, "same").chosen == settle(mixed, FACING, "same").chosen


def test_unusable_answers():
    with pytest.raises(BadAnswer):
        settle("not json", FACING, "n")
    with pytest.raises(BadAnswer):
        settle(answer(("check", 0, 1)), FACING, "n")


def test_the_note_is_revealed():
    a = settle(
        answer(("call", 0, 1), note="Player A overbluffs"),
        FACING,
        "n",
        reveal=lambda s: s.replace("Player A", "alex"),
    )
    assert a.note == "alex overbluffs"
