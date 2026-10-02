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


def test_facing_a_bet():
    h, spot = hand(DEEP, *FLOP, f"{P[V1]} bets 0.80", f"{P[V2]} folds")
    assert legal(h, HERO, spot) == ["fold", "call", "raise", "allin"]
    s = betting(h, HERO, spot)
    assert (s.pot, s.to_call, s.price, s.min_raise) == (260, 80, 80, 80)
    facts = claude_facts(h, spot, {"need": 24, "madeHand": "top pair", "equity": 80})
    assert facts["pot_bb"] == 13.0 and facts["to_call_bb"] == 4.0
    assert facts["effective_stack_bb"] == 43.0  # the bettor's 10.00 less 1.40 in
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
