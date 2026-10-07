import json

from vamos.profile_stats import SessionSpec, collect, summary

HERO, ALEX, RJ, GUEST = "heroid0001", "alexid0001", "rjid000001", "guestid001"
H, D, K, G = f'"jes @ {HERO}"', f'"alex @ {ALEX}"', f'"rj @ {RJ}"', f'"Yuri @ {GUEST}"'
WHO = {HERO: "jes", ALEX: "alex", RJ: "rj"}


def game(tmp_path, *hands: list[str]):
    rows = [line for hand in hands for line in hand]
    path = tmp_path / "pglTEST.jsonl"
    path.write_text(
        "".join(json.dumps({"order": 100 + i, "msg": m}) + "\n" for i, m in enumerate(rows))
    )
    return path


def hand(number: int, dealer: str, players: str, *lines: str) -> list[str]:
    return [
        f"-- starting hand #{number} (id: h{number})  No Limit Texas Hold'em (dealer: {dealer}) --",
        f"Player stacks: {players}",
        *lines,
        f"-- ending hand #{number} --",
    ]


THREE = f"#1 {H} (1000) | #2 {D} (1000) | #3 {K} (1000)"
# alex limps, rj raises, alex calls (his first answer to the raise); on the flop alex checks,
# rj bets, alex folds: a bet faced after checking still counts as faced.
LIMP_THEN_FOLD = hand(
    1,
    H,
    THREE,
    f"{D} posts a small blind of 10",
    f"{K} posts a big blind of 20",
    f"{H} folds",
    f"{D} calls 20",
    f"{K} raises to 80",
    f"{D} calls 80",
    "Flop:  [K♠, 7♦, 2♣]",
    f"{D} checks",
    f"{K} bets 80",
    f"{D} folds",
    f"Uncalled bet of 80 returned to {K}",
    f"{K} collected 160 from pot",
)
# Heads-up: rj opens, the guest folds.
HEADS_UP = hand(
    2,
    K,
    f"#1 {K} (1000) | #2 {G} (1000)",
    f"{K} posts a small blind of 10",
    f"{G} posts a big blind of 20",
    f"{K} raises to 60",
    f"{G} folds",
    f"Uncalled bet of 40 returned to {K}",
    f"{K} collected 40 from pot",
)


def run(tmp_path, *hands, after=0):
    path = game(tmp_path, *hands)
    return collect(
        [SessionSpec("O", "pglTEST", "2026-10-07", path, after)], lambda p, n: WHO.get(p)
    )


def test_counts_follow_the_rulebooks_definitions(tmp_path):
    players, _ = run(tmp_path, LIMP_THEN_FOLD)
    alex = summary(players["alex"])["stats_by_session"]["O/ring"]
    assert alex["vpip"] == "100% (1/1)" and alex["pfr"] == "0% (0/1)"
    assert alex["open_limp_unopened"] == "100% (1/1)"
    assert alex["limp_then_raised_call"] == "100% (1/1)"
    assert alex["flop_faced_bet_fold"] == "100% (1/1)"
    rj = summary(players["rj"])["stats_by_session"]["O/ring"]
    assert rj["iso_raise_over_limps"] == "100% (1/1)"
    assert rj["flop_cbet"] == "100% (1/1)"
    assert summary(players["alex"])["net_dollars_by_session"] == {"O": -80.0}  # whole chips


def test_heads_up_hands_are_kept_apart_and_unclaimed_players_are_not_named(tmp_path):
    players, unknown = run(tmp_path, HEADS_UP)
    rj = summary(players["rj"])
    assert rj["stats_by_session"]["O/headsup"]["pfr"] == "100% (1/1)"
    assert "O/ring" not in rj["stats_by_session"]
    assert unknown == {GUEST: {"names": ["Yuri"], "hands": {"O": 1}}}
    assert "Yuri" not in "\n".join(players["rj"].digest)
    assert "other player A" in "\n".join(players["rj"].digest)


def test_hands_counted_already_are_skipped(tmp_path):
    players, _ = run(tmp_path, LIMP_THEN_FOLD, HEADS_UP, after=1)
    assert dict(players["rj"].hands) == {"O": 1}
    assert "alex" not in players
