from vamos.brain.spot import betting, claude_facts, legal
from vamos.engine.state import decision
from vamos.log.hands import build_hands

HERO, V1, V2 = "heroid0001", "villain001", "villain002"
P = {HERO: f'"jes @ {HERO}"', V1: f'"alex @ {V1}"', V2: f'"rj @ {V2}"'}


def hand(stacks, *lines):
    rows = [
        f"-- starting hand #4 (id: h4)  No Limit Texas Hold'em (dealer: {P[HERO]}) --",
        "Player stacks: "
        + " | ".join(
            f"#{i + 1} {P[pid]} ({stack})" for i, (pid, stack) in enumerate(stacks.items())
        ),
        "Your hand is A♠, K♦",
        f"{P[V1]} posts a small blind of 0.10",
        f"{P[V2]} posts a big blind of 0.20",
        *lines,
    ]
    built = build_hands((100 + i, line) for i, line in enumerate(rows)).hands[-1]
    return built, decision(built, HERO)


FLOP = [
    f"{P[HERO]} raises to 0.60",
    f"{P[V1]} calls 0.60",
    f"{P[V2]} calls 0.60",
    "Flop:  [K♠, 7♦, 2♣]",
]
DEEP = {HERO: "10.00", V1: "10.00", V2: "10.00"}


def test_checked_to_the_hero():
    h, spot = hand(DEEP, *FLOP, f"{P[V1]} checks", f"{P[V2]} checks")
    assert legal(h, HERO, spot) == ["check", "bet", "allin"]
    s = betting(h, HERO, spot)
    assert (s.pot, s.to_call, s.price, s.min_raise, s.behind) == (180, 0, 0, 20, 940)


def test_the_big_blind_option_is_a_raise():
    rows = [
        f"-- starting hand #5 (id: h5)  No Limit Texas Hold'em (dealer: {P[V1]}) --",
        f"Player stacks: #1 {P[V1]} (10.00) | #2 {P[HERO]} (10.00)",
        "Your hand is 7♣, 6♦",
        f"{P[V1]} posts a small blind of 0.10",
        f"{P[HERO]} posts a big blind of 0.20",
        f"{P[V1]} calls 0.20",
    ]
    h = build_hands((100 + i, line) for i, line in enumerate(rows)).hands[-1]
    spot = decision(h, HERO)
    assert legal(h, HERO, spot) == ["check", "raise", "allin"]
    facts = claude_facts(h, HERO, spot, {}, {V1: "Player A"})
    assert (facts["min_raise_to_bb"], "min_bet_bb" in facts) == (2.0, False)


def test_facing_a_bet():
    h, spot = hand(DEEP, *FLOP, f"{P[V1]} bets 0.80", f"{P[V2]} folds")
    assert legal(h, HERO, spot) == ["fold", "call", "raise", "allin"]
    s = betting(h, HERO, spot)
    assert (s.pot, s.to_call, s.price, s.min_raise) == (260, 80, 80, 80)
    facts = claude_facts(h, HERO, spot, {"need": 24, "madeHand": "top pair"}, {V1: "Player A"})
    assert facts["pot_bb"] == 13.0 and facts["to_call_bb"] == 4.0
    # The hero's 9.40 behind; the bettor can match it (8.60 behind plus the 0.80 already bet).
    assert facts["effective_stack_bb"] == 47.0
    assert facts["opponents_in_hand"] == 1


def test_a_short_all_in_does_not_reopen_the_raise():
    stacks = {HERO: "10.00", V1: "10.00", V2: "1.50"}
    h, spot = hand(
        stacks,
        *FLOP,
        f"{P[V1]} checks",
        f"{P[V2]} checks",
        f"{P[HERO]} bets 0.80",
        f"{P[V1]} calls 0.80",
        f"{P[V2]} raises to 0.90 and go all in",
    )
    assert legal(h, HERO, spot) == ["fold", "call"]


def test_a_full_bet_after_the_hero_acted_reopens_it():
    h, spot = hand(
        DEEP,
        *FLOP,
        f"{P[V1]} bets 0.40",
        f"{P[V2]} raises to 1.20",
        f"{P[HERO]} calls 1.20",
        f"{P[V1]} raises to 3.00",
    )
    assert legal(h, HERO, spot) == ["fold", "call", "raise", "allin"]


def test_nobody_left_to_raise():
    stacks = {HERO: "10.00", V1: "3.00", V2: "10.00"}
    h, spot = hand(stacks, *FLOP, f"{P[V1]} bets 2.40 and go all in", f"{P[V2]} folds")
    assert legal(h, HERO, spot) == ["fold", "call"]


def test_the_betting_state_carries_what_the_rules_allow():
    stacks = {HERO: "10.00", V1: "3.00", V2: "10.00"}
    h, spot = hand(stacks, *FLOP, f"{P[V1]} bets 2.40 and go all in", f"{P[V2]} folds")
    assert betting(h, HERO, spot).legal == ("fold", "call")


def test_the_bet_faced_is_sized_against_the_pot_it_went_into():
    h, spot = hand(DEEP, *FLOP, f"{P[V1]} bets 0.80", f"{P[V2]} folds")
    facts = claude_facts(h, HERO, spot, {}, {V1: "Player A"})
    assert facts["bet_faced"] == {
        "by": "Player A",
        "to_bb": 4.0,
        "pot_before_it_bb": 9.0,
        "pct_of_pot": 44,
        "all_in": False,
    }
    assert facts["min_raise_to_bb"] == 8.0 and "min_bet_bb" not in facts
    assert facts["hero_acts_last"] is True  # the hero has the button
    assert "hero_equity_vs_random_hands_pct" not in facts


def test_facing_an_all_in_bigger_than_the_hero():
    stacks = {HERO: "5.00", V1: "20.00", V2: "10.00"}
    h, spot = hand(stacks, *FLOP, f"{P[V1]} bets 15.00 and go all in", f"{P[V2]} folds")
    facts = claude_facts(h, HERO, spot, {"need": 0}, {V1: "Player A"})
    assert facts["call_puts_hero_all_in"] is True
    assert "stack_to_pot_ratio_at_street_start" not in facts
    assert facts["effective_stack_bb"] == 22.0  # the hero's 4.40 behind, not 0
    assert facts["chips_hero_cannot_win_bb"] == 53.0  # 15.00 bet, 4.40 matched
    assert facts["bet_faced"]["all_in"] is True


def test_checked_to_the_hero_with_a_minimum_bet():
    h, spot = hand(DEEP, *FLOP, f"{P[V1]} checks", f"{P[V2]} checks")
    facts = claude_facts(h, HERO, spot, {}, {})
    assert facts["min_bet_bb"] == 1.0 and "bet_faced" not in facts


def test_position_ignores_who_is_all_in():
    # Heads-up, the hero in the big blind is first to act after the flop, also when the
    # button has just gone all in.
    rows = [
        f"-- starting hand #9 (id: h9)  No Limit Texas Hold'em (dealer: {P[V1]}) --",
        f"Player stacks: #1 {P[HERO]} (10.00) | #2 {P[V1]} (20.00)",
        "Your hand is 2♦, 6♣",
        f"{P[V1]} posts a small blind of 0.10",
        f"{P[HERO]} posts a big blind of 0.20",
        f"{P[V1]} calls 0.20",
        f"{P[HERO]} checks",
        "Flop:  [J♣, 5♥, 4♣]",
        f"{P[HERO]} bets 0.20",
        f"{P[V1]} raises to 19.80 and go all in",
    ]
    built = build_hands((100 + i, line) for i, line in enumerate(rows)).hands[-1]
    spot = decision(built, HERO)
    assert claude_facts(built, HERO, spot, {}, {})["hero_acts_last"] is False


def test_a_pot_sized_raise_reads_100_and_a_raise_short_of_a_full_one_is_all_in_only():
    h, spot = hand(
        DEEP,
        *FLOP,
        f"{P[V1]} checks",
        f"{P[V2]} checks",
        f"{P[HERO]} bets 1.80",
        f"{P[V1]} raises to 7.20",
        f"{P[V2]} folds",
    )
    facts = claude_facts(h, HERO, spot, {}, {V1: "Player A"})
    assert facts["bet_faced"]["pct_of_pot"] == 100  # 5.40 on top of calling into 5.40
    # 7.60 behind cannot make a full raise to 12.60, so PokerNow offers only all in.
    assert legal(h, HERO, spot) == ["fold", "call", "allin"]
    assert "min_raise_to_bb" not in facts


def test_a_short_all_in_does_not_hide_the_bet_before_it():
    stacks = {HERO: "10.00", V1: "10.00", V2: "2.00"}
    h, spot = hand(stacks, *FLOP, f"{P[V1]} bets 1.00", f"{P[V2]} raises to 1.40 and go all in")
    faced = claude_facts(h, HERO, spot, {}, {V1: "Player A", V2: "Player B"})["bet_faced"]
    assert (faced["by"], faced["pct_of_pot"], faced["then_short_all_in_to_bb"]) == (
        "Player A",
        56,
        7.0,
    )
