import json

import pytest

from vamos.brain.advisor import describe
from vamos.brain.answer import BadAnswer, Choice, Spot, settle, tidy

# Facing a 0.40 bet into 1.20 (pot 1.60 with the bet), 9.40 behind, blinds 0.10/0.20.
FACING = Spot(pot=160, to_call=40, street_bet=0, price=40, min_raise=40, behind=940, big_blind=20)
# Checked to us: pot 1.20, nothing to call.
CHECKED = Spot(pot=120, to_call=0, street_bet=0, price=0, min_raise=20, behind=940, big_blind=20)
# Limped to the big blind: pot 0.40, the big blind's 0.20 already in.
BB_OPTION = Spot(
    pot=40,
    to_call=0,
    street_bet=20,
    price=20,
    min_raise=20,
    behind=980,
    big_blind=20,
    legal=("check", "raise", "allin"),
)


def answer(*rows, note="ok", plan=""):
    return json.dumps(
        {
            "strategy": [{"action": a, "size_pct_pot": s, "freq": f} for a, s, f in rows],
            "reason": note,
            "plan": plan,
        }
    )


def test_a_pure_call():
    a = settle(answer(("call", 0, 1)), FACING, seed="n")
    assert a.chosen == Choice("call", 40, 1.0)
    assert (a.reason, a.plan) == ("ok", "")


def test_illegal_actions_are_dropped_and_frequencies_renormalized():
    # Checking is not possible facing a bet; a bet facing one is read as a raise.
    a = settle(
        answer(("check", 0, 0.5), ("bet", 50, 0.2), ("call", 0, 0.2), ("fold", 0, 0.1)), FACING, "n"
    )
    assert [(c.action, round(c.freq, 3)) for c in a.mix] == [
        ("raise", 0.4),
        ("call", 0.4),
        ("fold", 0.2),
    ]


def test_to_bb_sizes_a_raise_and_a_raise_with_no_bet_standing_is_a_bet():
    def rows(*items):
        return json.dumps(
            {
                "reason": "ok",
                "strategy": [
                    {"action": a, "to_bb": t, "size_pct_pot": 0, "freq": 1} for a, t in items
                ],
                "plan": "",
            }
        )

    raised = settle(rows(("raise", 6)), FACING, "n").chosen
    assert (raised.action, raised.to) == ("raise", 6 * FACING.big_blind)
    bet = settle(rows(("raise", 2)), CHECKED, "n").chosen
    assert (bet.action, bet.to) == ("bet", 2 * CHECKED.big_blind)
    # The big blind's option: nothing owed, but going over the big blind is a raise.
    option = settle(rows(("bet", 3.5)), BB_OPTION, "n").chosen
    assert (option.action, option.to) == ("raise", 70)
    assert describe(option, BB_OPTION) == "RAISE TO $0.70 (3.5bb)"
    # More than the stack is all in.
    assert settle(rows(("raise", 500)), FACING, "n").chosen.action == "allin"


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


def test_a_close_mix_is_drawn_from_and_stable_per_decision():
    mixed = answer(("call", 0, 0.55), ("fold", 0, 0.45))
    picks = {settle(mixed, FACING, seed=f"node{k}").chosen.action for k in range(40)}
    assert picks == {"call", "fold"}
    assert settle(mixed, FACING, "same").chosen == settle(mixed, FACING, "same").chosen


def test_a_clear_favorite_is_always_the_card():
    # 80/20 is Claude's confidence, not a solved mix: the top option every time.
    leaning = answer(("call", 0, 0.8), ("fold", 0, 0.2))
    picks = {settle(leaning, FACING, seed=f"node{k}").chosen.action for k in range(40)}
    assert picks == {"call"}


def test_unusable_answers():
    with pytest.raises(BadAnswer):
        settle("not json", FACING, "n")
    with pytest.raises(BadAnswer):
        settle(answer(("check", 0, 1)), FACING, "n")


def test_the_note_is_revealed():
    a = settle(
        answer(("call", 0, 1), note="Player A overbluffs", plan="Fold if Player A raises."),
        FACING,
        "n",
        reveal=lambda s: s.replace("Player A", "alex"),
    )
    assert (a.reason, a.plan) == ("alex overbluffs", "Fold if alex raises.")


def test_raising_closed_drops_raises_and_turns_all_in_into_a_call():
    closed = Spot(
        pot=160,
        to_call=40,
        street_bet=0,
        price=40,
        min_raise=40,
        behind=940,
        big_blind=20,
        legal=("fold", "call"),
    )
    a = settle(answer(("raise", 75, 0.5), ("allin", 0, 0.5)), closed, seed="n")
    assert a.mix == (Choice("call", 40, 1.0),)


def test_sizes_round_to_five_cents():
    # 40% of a 1.20 pot is 0.48; at 0.10/0.20 sizes step by a quarter big blind (0.05).
    assert settle(answer(("bet", 40, 1)), CHECKED, seed="n").chosen.to == 50  # 48 -> 50
    a = settle(answer(("raise", 37, 1)), FACING, seed="n")
    assert a.chosen.to % 5 == 0 and a.chosen.to >= FACING.price + FACING.min_raise


def test_sizes_sit_on_a_quarter_big_blind_at_any_stakes():
    from vamos.brain.answer import snap

    assert snap(48, 20) == 50 and snap(47, 20) == 45  # 0.10/0.20: 5 cents
    assert snap(24, 10) == 25 and snap(26, 10) == 25  # 0.05/0.10: 2.5 cents, whole cents
    assert snap(120, 50) == 125 and snap(118, 50) == 113  # 0.25/0.50: 12.5 cents, whole cents
    assert snap(101, 20, up=True) == 105  # never below a minimum


def test_options_under_half_a_percent_are_dropped():
    a = settle(answer(("fold", 0, 0), ("call", 0, 1)), FACING, seed="n")
    assert a.mix == (Choice("call", 40, 1.0),)


def test_a_raise_short_of_a_full_raise_is_all_in_and_nothing_owed_is_a_check():
    # 5bb behind facing an open to 4bb: PokerNow offers fold, call or all in.
    short = Spot(
        pot=140,
        to_call=60,
        street_bet=20,
        price=80,
        min_raise=60,
        behind=80,
        big_blind=20,
        legal=("fold", "call", "allin"),
    )
    a = settle(answer(("raise", 0, 0.7), ("fold", 0, 0.3)), short, seed="n")
    assert [(c.action, c.to) for c in a.mix] == [("allin", 100), ("fold", 0)]
    # The big blind's option: "call" and "fold" both mean checking.
    a = settle(answer(("call", 0, 0.6), ("fold", 0, 0.4)), BB_OPTION, seed="n")
    assert [(c.action, round(c.freq, 2)) for c in a.mix] == [("check", 1.0)]


def test_stray_closing_characters_are_trimmed_from_the_text():
    text = json.dumps(
        {
            "strategy": [{"action": "call", "size_pct_pot": 0, "freq": 1}],
            "reason": "He barrels light.'",
            "plan": "Check behind if the turn is a club.}",
        }
    )
    a = settle(text, FACING, "n")
    assert (a.reason, a.plan) == ("He barrels light.", "Check behind if the turn is a club.")
    assert tidy("Slow down on 9/5/4 turns..") == "Slow down on 9/5/4 turns."
    assert tidy("He might fold...") == "He might fold..."
    keeps = json.dumps(
        {
            "strategy": [{"action": "call", "size_pct_pot": 0, "freq": 1}],
            "reason": "Call (he bluffs often)",
            "plan": "",
        }
    )
    assert settle(keeps, FACING, "n").reason == "Call (he bluffs often)"
