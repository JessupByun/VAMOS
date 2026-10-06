import json

from vamos.history import hand_detail, hand_list
from vamos.log.hands import build_hands

HERO, ALEX = "heroid0001", "Qw3rTy7UiO"
H = f'"Hero @ {HERO}"'
D = f'"alex @ {ALEX}"'


def log_of(*rows: str):
    return build_hands((100 + i, row) for i, row in enumerate(rows))


def hand(number: int, dealer: str, *lines: str, ended: bool = True, alex: int = 1000) -> list[str]:
    rows = [
        f"-- starting hand #{number} (id: h{number})  No Limit Texas Hold'em (dealer: {dealer}) --",
        f"Player stacks: #1 {H} (1000) | #2 {D} ({alex})",
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
            "result": "$20 (1bb)",
            "folded": True,
            "decisions": 0,
        },
        {
            "key": "h1",
            "number": 1,
            "heroCards": ["As", "Ad"],
            "position": "BTN",
            "resultBb": 2.5,
            "result": "$50 (2.5bb)",
            "folded": False,
            "decisions": 2,
        },
    ]


def test_a_hand_replays_street_by_street_with_advice_before_the_heros_action():
    advice = {"source": "Rule", "action": "RAISE TO $50 (2.5bb)"}
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
        "raises to $50 (2.5bb)",
        "calls $30 (1.5bb)",  # what the big blind added
    ]
    assert preflop["events"][2]["advice"] == advice and preflop["events"][3]["hero"] is True
    assert (flop["name"], flop["board"]) == ("Flop", ["Ks", "7d", "2c"])
    assert (flop["pot"], flop["potBb"]) == ("$100 (5bb)", 5.0)
    assert [e.get("text") or e["kind"] for e in flop["events"]] == [
        "checks",
        "decision",
        "bets $50 (2.5bb)",
        "folds",
    ]
    assert flop["events"][1]["aiOff"] is True
    # Whole chips at 10/20.
    assert (detail["result"], detail["resultBb"]) == ("$50 (2.5bb)", 2.5)
    you, alex = detail["players"]
    assert (you["stack"], you["net"], you["netBb"]) == ("$1,000 (50bb)", "$50 (2.5bb)", 2.5)
    assert (alex["net"], alex["netBb"], alex["folded"]) == ("$50 (2.5bb)", -2.5, True)


def test_a_decision_the_hero_never_acted_on_ends_its_street():
    # The hero's turn came, then the hand ended without the hero acting (a timeout fold
    # logged later, or a disconnect): the advice still shows, at the end of the street.
    detail = hand_detail(log_of(*LIVE), "h3", HERO, {"h3:0:9": {"street": 0, "advice": None}})
    assert detail["streets"][0]["events"][-1]["kind"] == "decision"
    assert detail["ended"] is False
    assert hand_detail(log_of(*LIVE), "nope", HERO, {}) is None


def logged(node: str, alex_at: str, alex_bb: float = 50.0, **entry) -> dict:
    """An entry of data/advice.jsonl: the hand as sent, under pseudonyms, and the answer."""
    sent = {
        "players": [
            {"name": "Hero", "position": "BTN", "stack_bb": 50.0, "behind_bb": 47.5},
            {"name": "Player B", "position": alex_at, "stack_bb": alex_bb, "behind_bb": 47.5},
        ],
        "facts": {"to_call_bb": 0},
    }
    answer = {
        "reason": "Player B checks too much; Hero's top pair is ahead.",
        "strategy": [],
        "plan": "Fold if Player B raises.'",
    }
    return {
        "game": "g",
        "node": node,
        "kind": "advice",
        "model": "claude-opus-5-5",
        "state": "done",
        "reason": None,
        "spot": f"Hand so far (amounts in big blinds):\n{json.dumps(sent)}\n\nWhat should Hero do?",
        "answer": json.dumps(answer),
        "mix": [["bet", 5000, 0.8], ["check", 0, 0.2]],  # cents: whole chips at 10/20
        "chosen": ["bet", 5000],
        **entry,
    }


def test_hands_from_before_the_record_show_claudes_logged_answers_with_real_names():
    failed_ask = logged(
        "h1:1:5", "BB", kind="ask", state="failed", reason="timed out", answer=None, mix=None
    )
    entries = [logged("h1:1:5", "BB"), failed_ask]
    log = log_of(*WON)
    assert hand_list(log, HERO, {}, entries)[0]["decisions"] == 1
    detail = hand_detail(log, "h1", HERO, {}, entries)
    assert detail["partial"] is True
    [decision] = [e for e in detail["streets"][1]["events"] if e["kind"] == "decision"]
    advice = decision["advice"]
    assert (advice["source"], advice["action"], advice["state"]) == (
        "Claude",
        "BET $50 (2.5bb)",
        "failed",
    )
    first, ask = advice["answers"]
    # Equal stacks: the position tells who Player B was.
    assert first["why"] == "alex checks too much; your top pair is ahead."
    assert first["plan"] == "Fold if alex raises."
    assert (first["mix"], first["pick"], first["deviation"]) == (
        ["bet $50 (2.5bb) 80%", "check 20%"],
        0,
        False,
    )
    # The effort was not logged then, so it is not claimed; "Ask deeper" always ran medium.
    assert first["model"] == "Claude Opus 5.5" and first["preset"] is None
    assert (ask["model"], ask["state"], ask["reason"], ask["action"]) == (
        "Claude Opus 5.5 · medium",
        "failed",
        "timed out",
        None,
    )


def test_a_logged_name_follows_the_starting_stack_when_positions_were_counted_differently():
    log = log_of(*hand(1, H, *WON[2:-1], alex=600))
    entries = [logged("h1:1:5", "SB", alex_bb=30.0, preset="standard")]
    detail = hand_detail(log, "h1", HERO, {}, entries)
    [decision] = [e for e in detail["streets"][1]["events"] if e["kind"] == "decision"]
    [answer] = decision["advice"]["answers"]
    assert answer["why"].startswith("alex checks") and answer["model"] == "Claude Opus 5.5 · low"


def test_what_the_card_recorded_wins_over_the_advice_log():
    recorded = {"h1:1:5": {"street": 1, "advice": {"source": "Claude", "action": "CHECK"}}}
    detail = hand_detail(log_of(*WON), "h1", HERO, recorded, [logged("h1:1:5", "BB")])
    [decision] = [e for e in detail["streets"][1]["events"] if e["kind"] == "decision"]
    assert decision["advice"]["action"] == "CHECK" and detail["partial"] is False


def test_each_street_shows_the_board_so_far():
    turned = hand(
        4,
        H,
        "Your hand is Q♠, Q♦",
        f"{H} posts a small blind of 10",
        f"{D} posts a big blind of 20",
        f"{H} calls 20",
        f"{D} checks",
        "Flop:  [K♠, 7♦, 2♣]",
        f"{D} checks",
        f"{H} checks",
        "Turn: K♠, 7♦, 2♣ [9♥]",
        f"{D} bets 40",
        f"{H} folds",
        f"Uncalled bet of 40 returned to {D}",
        f"{D} collected 40 from pot",
    )
    detail = hand_detail(log_of(*turned), "h4", HERO, {})
    flop, turn = detail["streets"][1:]
    assert flop["board"] == ["Ks", "7d", "2c"]
    assert (turn["name"], turn["board"]) == ("Turn", ["Ks", "7d", "2c", "9h"])
    # A call says what it added: the small blind completing puts in half a big blind.
    assert detail["streets"][0]["events"][2]["text"] == "calls $10 (0.5bb)"


def test_an_all_in_shows_each_players_cards_and_the_pot_without_the_uncalled_bet():
    shoved = hand(
        5,
        H,
        "Your hand is J♠, K♠",
        f"{H} posts a small blind of 10",
        f"{D} posts a big blind of 20",
        f"{H} raises to 1000 and go all in",
        f"{D} calls 600 and go all in",
        f"Uncalled bet of 400 returned to {H}",
        "Flop:  [10♠, 7♦, A♠]",
        "Turn: 10♠, 7♦, A♠ [5♠]",
        "River: 10♠, 7♦, A♠, 5♠ [J♦]",
        f"{D} shows a 6♣, K♦.",
        f"{H} shows a J♠, K♠.",
        f"{H} collected 1200 from pot",
        alex=600,
    )
    detail = hand_detail(log_of(*shoved), "h5", HERO, {})
    # The 400 that came back was never in the pot.
    assert [s["pot"] for s in detail["streets"][1:]] == ["$1,200 (60bb)"] * 3
    you, alex = detail["players"]
    assert (you["name"], you["cards"], you["folded"]) == ("Hero", ["Js", "Ks"], False)
    assert (alex["name"], alex["cards"], alex["position"]) == ("alex", ["6c", "Kd"], "BB")
    assert (you["netBb"], alex["netBb"]) == (30.0, -30.0)
    assert detail["streets"][0]["events"][2]["text"] == "raises to $1,000 (50bb), all in"
