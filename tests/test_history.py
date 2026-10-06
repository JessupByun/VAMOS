from vamos.history import hand_detail, hand_list
from vamos.log.hands import build_hands

HERO, ALEX = "heroid0001", "Qw3rTy7UiO"
H = f'"Hero @ {HERO}"'
D = f'"alex @ {ALEX}"'


def log_of(*rows: str):
    return build_hands((100 + i, row) for i, row in enumerate(rows))


def hand(number: int, dealer: str, *lines: str, ended: bool = True) -> list[str]:
    rows = [
        f"-- starting hand #{number} (id: h{number})  No Limit Texas Hold'em (dealer: {dealer}) --",
        f"Player stacks: #1 {H} (1000) | #2 {D} (1000)",
        *lines,
    ]
    return [*rows, f"-- ending hand #{number} --"] if ended else rows


WON = hand(
    1,
    H,
    "Your hand is A♠, A♦",
    f"{H} posts a small blind of 10",
    f"{D} posts a big blind of 20",
    f"{H} raises to 50",
    f"{D} calls 50",
    "Flop:  [K♠, 7♦, 2♣]",
    f"{D} checks",
    f"{H} bets 50",
    f"{D} folds",
    f"Uncalled bet of 50 returned to {H}",
    f"{H} collected 100 from pot",
)
FOLDED = hand(
    2,
    D,
    "Your hand is 7♠, 2♦",
    f"{D} posts a small blind of 10",
    f"{H} posts a big blind of 20",
    f"{D} raises to 60",
    f"{H} folds",
    f"Uncalled bet of 40 returned to {D}",
    f"{D} collected 40 from pot",
)
LIVE = hand(3, H, "Your hand is K♠, K♦", f"{H} posts a small blind of 10", ended=False)


def test_the_menu_lists_finished_hands_newest_first():
    log = log_of(*WON, *FOLDED, *LIVE)
    decisions = {"h1:0:2": {"street": 0}, "h1:1:5": {"street": 1}}
    assert hand_list(log, HERO, decisions) == [
        {
            "key": "h2",
            "number": 2,
            "heroCards": ["7s", "2d"],
            "position": "BB",
            "resultBb": -1.0,
            "folded": True,
            "decisions": 0,
        },
        {
            "key": "h1",
            "number": 1,
            "heroCards": ["As", "Ad"],
            "position": "BTN",
            "resultBb": 2.5,
            "folded": False,
            "decisions": 2,
        },
    ]


def test_a_hand_replays_street_by_street_with_advice_before_the_heros_action():
    advice = {"source": "Rule", "action": "RAISE TO 50 (2.5bb)"}
    decisions = {
        "h1:0:2": {"street": 0, "advice": advice, "facts": {"pot": "1.5bb"}},
        "h1:1:5": {"street": 1, "advice": None, "aiOff": True},
    }
    detail = hand_detail(log_of(*WON), "h1", HERO, decisions)
    preflop, flop = detail["streets"]
    assert [e.get("text") or e["kind"] for e in preflop["events"]] == [
        "posts the small blind",
        "posts the big blind",
        "decision",
        "raises to 2.5bb",
        "calls 2.5bb",
    ]
    assert preflop["events"][2]["advice"] == advice and preflop["events"][3]["hero"] is True
    assert (flop["name"], flop["board"], flop["potBb"]) == ("Flop", ["Ks", "7d", "2c"], 5.0)
    assert [e.get("text") or e["kind"] for e in flop["events"]] == [
        "checks",
        "decision",
        "bets 2.5bb",
        "folds",
    ]
    assert flop["events"][1]["aiOff"] is True
    assert (detail["result"], detail["resultBb"]) == ("50", 2.5)  # whole chips at 10/20
    assert detail["winners"] == [{"name": "Hero", "amount": "100", "amountBb": 5.0, "hero": True}]


def test_a_decision_the_hero_never_acted_on_ends_its_street():
    # The hero's turn came, then the hand ended without the hero acting (a timeout fold
    # logged later, or a disconnect): the advice still shows, at the end of the street.
    detail = hand_detail(log_of(*LIVE), "h3", HERO, {"h3:0:9": {"street": 0, "advice": None}})
    assert detail["streets"][0]["events"][-1]["kind"] == "decision"
    assert detail["ended"] is False
    assert hand_detail(log_of(*LIVE), "nope", HERO, {}) is None
