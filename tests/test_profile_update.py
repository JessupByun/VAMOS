"""vamos.profile_update: new games labelled, work folders written, profiles written, installed and undone."""

import json
from datetime import date, datetime

import pytest

from vamos.brain.costs import Ledger
from vamos.brain.provider import FakeProvider, Reply
from vamos.profile_update import (
    InstallError,
    Paths,
    Update,
    _net,
    absorbed,
    correct_headlines,
    headlines,
    install,
    next_label,
    prepare,
    register_new_games,
    undo,
)
from vamos.profiles import parse_profile

ALEX = "alexid0001"
PROFILE = """---
name: alex
tag: Calling station
player_id: {player}
hands: 10
sessions: [A 2026-09-30]
games: {{}}
net: -1.00 (A -1.00)
last_updated: 2026-10-01
---

## Summary
Calls too much.

## Key stats
- VPIP 50% (5/10), PFR 10% (1/10)
- 3-bet vs an open 5% (1/20). Facing a 3-bet: fold 0% (0/1)
- Flop: C-bets 50% (1/2). Faced a bet: fold 40% (2/5)
"""
GAME_ROWS = [
    '-- starting hand #1 (id: h1)  No Limit Texas Hold\'em (dealer: "jes @ heroid0001") --',
    f'Player stacks: #1 "jes @ heroid0001" (1000) | #2 "alex @ {ALEX}" (1000)',
    '"jes @ heroid0001" posts a small blind of 10',
    f'"alex @ {ALEX}" posts a big blind of 20',
    '"jes @ heroid0001" raises to 60',
    f'"alex @ {ALEX}" calls 60',
    "Flop:  [K♠, 7♦, 2♣]",
    f'"alex @ {ALEX}" checks',
    '"jes @ heroid0001" bets 60',
    f'"alex @ {ALEX}" folds',
    'Uncalled bet of 60 returned to "jes @ heroid0001"',
    '"jes @ heroid0001" collected 120 from pot',
    "-- ending hand #1 --",
]


@pytest.fixture
def paths(tmp_path):
    data, profiles = tmp_path / "data", tmp_path / "profiles"
    (data / "games").mkdir(parents=True)
    profiles.mkdir()
    (profiles / "alex.md").write_text(PROFILE.format(player=ALEX))
    (profiles / "_GUIDELINES.md").write_text("# Profile guidelines\nname: alex  # an example\n")
    (data / "settings.json").write_text('{"heroNames": ["jes"]}')
    # Session A is already in the profile, so a new game is B.
    (data / "profile-sessions.json").write_text(
        json.dumps({"sessions": {"A": {"date": "2026-09-30"}}, "excluded": {}})
    )
    return Paths(data, profiles)


def add_game(paths, game: str, start: int) -> None:
    path = paths.games / f"{game}.jsonl"
    path.write_text(
        "".join(json.dumps({"order": start + i, "msg": m}) + "\n" for i, m in enumerate(GAME_ROWS))
    )


def test_labels_go_on_from_the_last_letter_used():
    assert next_label({"HU", "A", "N"}) == "O"
    assert next_label({"Z"}) == "AA"
    assert next_label({"AZ", "B"}) == "BA"
    assert next_label(set()) == "A"


def test_what_a_profile_has_absorbed():
    profile = parse_profile(
        "rj",
        "---\nname: rj\nsessions: [A 2026-09-30, O 2026-10-07]\n"
        "games: {O: pglX through hand 40}\nexcluded: [D 2026-10-04 at his request]\n---\nbody\n",
    )
    assert absorbed(profile) == ({"A"}, {"O": 40}, {"D"})


def test_a_new_game_is_labelled_and_prepared_for_the_players_in_it(paths):
    # Noon UTC, so the game is dated 2026-10-07 in every time zone the date is taken in.
    add_game(paths, "pglNEW", 179_137_440_000_000)
    registry = {"sessions": {"A": {"date": "2026-09-30"}}, "excluded": {}}
    assert register_new_games(paths, registry) == ["B"]
    assert registry["sessions"]["B"] == {"game": "pglNEW", "date": "2026-10-07"}
    work = prepare(paths, registry).work
    players = json.loads((work / "players.json").read_text())
    assert [p["key"] for p in players] == ["alex"]
    assert "B: pglNEW through hand 1" in players[0]["sessions"]
    stats = json.loads((work / "alex.stats.json").read_text())
    assert stats["hands_by_session"] == {"B": 1}
    assert "### B1" in (work / "alex.digest.md").read_text()


def test_a_game_already_absorbed_or_excluded_is_not_prepared(paths):
    add_game(paths, "pglOLD", 100)
    registry = {"sessions": {"A": {"date": "2026-09-30", "game": "pglOLD"}}, "excluded": {}}
    assert register_new_games(paths, registry) == []
    assert prepare(paths, registry) is None
    add_game(paths, "pglTEST", 200)
    registry["excluded"]["pglTEST"] = "a test"
    assert register_new_games(paths, registry) == []


def test_install_keeps_both_versions_and_refuses_a_profile_the_panel_cannot_read(paths):
    work = paths.work / "w"
    (work / "new").mkdir(parents=True)
    (work / "sessions.json").write_text('{"B": {}}')
    old = (paths.profiles_dir / "alex.md").read_text()
    good = (
        old.replace("hands: 10", "hands: 11")
        + "- Went to showdown 50% (1/2), won at showdown 0% (0/1)\n"
    )
    (work / "new" / "alex.md").write_text(good)
    install(paths, work)
    assert (paths.profiles_dir / "alex.md").read_text() == good
    kept = sorted(p.read_text() for p in (paths.history / "alex").glob("*.md"))
    assert sorted([old, good]) == kept
    (work / "new" / "alex.md").write_text(good.replace("- VPIP 50% (5/10), PFR 10% (1/10)\n", ""))
    with pytest.raises(InstallError):
        install(paths, work)
    assert (paths.profiles_dir / "alex.md").read_text() == good


OLD_KEY_STATS = """## Key stats
Source: sessions A to C.
- VPIP 50% (5/10), PFR 10% (1/10)
- 3-bet vs an open 5% (1/20). Facing a 3-bet: fold 0% (0/1)
- Flop: C-bets 50% (1/2). Faced a bet: fold 40% (2/5)
- Went to showdown 50% (1/2), won at showdown 0% (0/1)
"""


def test_headlines_add_the_new_ring_counts_to_the_current_totals():
    new_ring = {"vpip": "50% (2/4)", "pfr": "25% (1/4)", "won_at_showdown": "100% (1/1)"}
    assert headlines(OLD_KEY_STATS, new_ring) == [
        "- VPIP 50% (7/14), PFR 14% (2/14)",
        "- 3-bet vs an open 5% (1/20). Facing a 3-bet: fold 0% (0/1)",
        "- Flop: C-bets 50% (1/2). Faced a bet: fold 40% (2/5)",
        "- Went to showdown 50% (1/2), won at showdown 50% (1/2)",
    ]
    # A new profile starts from the new counts; a line the current profile lacks is unchecked.
    assert headlines("", new_ring)[0] == "- VPIP 50% (2/4), PFR 25% (1/4)"
    assert headlines("## Key stats\n- VPIP 9%\n", new_ring)[0] is None


def test_code_corrects_the_headline_lines_and_adds_a_missing_one():
    exact = headlines(OLD_KEY_STATS, {})
    written = OLD_KEY_STATS.replace("- VPIP 50% (5/10)", "- VPIP 52% (6/10)").replace(
        "- Went to showdown 50% (1/2), won at showdown 0% (0/1)\n", ""
    )
    body = (
        "## Summary\nLoose.\n\n"
        + written
        + "- Recent: VPIP 60% (3/5)\n\n## Preflop\n- VPIP by seat\n"
    )
    fixed, changed = correct_headlines(body, exact)
    assert changed == 2
    assert "- VPIP 50% (5/10), PFR 10% (1/10)" in fixed
    assert fixed.index("- Went to showdown") < fixed.index("- VPIP 50%")  # after the Source line
    assert "- VPIP by seat" in fixed and "- Recent: VPIP 60% (3/5)" in fixed


def test_the_net_adds_each_new_session_to_the_total_and_its_part():
    assert _net("-1.00 (A -1.00)", {"B": -60.0}) == "-61.00 (A -1.00, B -60.00)"
    assert _net("+5.00 (A +2.00, B +3.00)", {"B": 1.5}) == "+6.50 (A +2.00, B +4.50)"
    assert _net(None, {"A": 2.0}) == "+2.00 (A +2.00)"
    assert _net(-3.5, {}) == "-3.50"


ANSWER = """TAG: Calling station
## Summary
Player A calls too much; Hero should value bet thinner. Player Z is nobody.

## Key stats
Source: sessions A and B.
- VPIP 99% (9/9), PFR 10% (1/10)
- 3-bet vs an open 5% (1/20). Facing a 3-bet: fold 0% (0/1)
- Flop: C-bets 50% (1/2). Faced a bet: fold 40% (2/5)
- Went to showdown 50% (1/2), won at showdown 0% (0/1)

## Changelog
- 2026-10-13: added B.
"""


def test_an_update_writes_each_profile_with_the_ai_installs_it_and_can_be_undone(paths):
    add_game(paths, "pglNEW", 179_137_440_000_000)
    (paths.games / "pglNEW.notes.json").write_text(json.dumps({ALEX: "alex tilts after jes wins"}))
    old = (paths.profiles_dir / "alex.md").read_text()
    (paths.profiles_dir / "alex.md").write_text(
        old.replace("- VPIP", "- Went to showdown 50% (1/2), won at showdown 0% (0/1)\n- VPIP")
    )
    old = (paths.profiles_dir / "alex.md").read_text()
    usage = {"input_tokens": 1000, "output_tokens": 2000}
    fake = FakeProvider([Reply(ANSWER, "end_turn", usage)])
    ledger = Ledger(paths.data_dir / "costs.jsonl")
    reports = []
    update = Update(paths, fake, ledger, reports.append, today=lambda: date(2026, 10, 13))
    plan = update.plan()
    assert plan["players"] == [{"key": "alex", "hands": 1}]
    assert plan["estimate"] > 0
    result = update.run()
    assert result["state"] == "done" and result["canUndo"]
    assert result["players"][0]["state"] == "updated"

    # No real name or nickname reached the AI: the profile is Player A, the hero is Hero.
    [request] = fake.requests
    sent = json.dumps(request.system) + json.dumps(request.messages)
    assert "alex" not in sent.lower().replace("alexid", "") and "jes" not in sent
    assert "Player A tilts after Hero wins" in sent
    assert request.schema is None and request.preset.effort == "high"

    new = parse_profile("alex", (paths.profiles_dir / "alex.md").read_text())
    assert new.tag == "Calling station"
    assert new.meta["hands"] == 11
    assert new.meta["games"] == {"B": "pglNEW through hand 1"}
    assert [str(s) for s in new.meta["sessions"]] == ["A 2026-09-30", "B 2026-10-07"]
    assert new.meta["net"] == "-61.00 (A -1.00, B -60.00)"
    assert str(new.meta["last_updated"]) == "2026-10-13"
    # Pseudonyms back to names (one without a profile stays nameless); the headline from code.
    assert "alex calls too much; Hero should value bet thinner" in new.body
    assert "an unknown player is nobody" not in new.body  # Player Z was never registered
    assert "- VPIP 50% (5/10), PFR 10% (1/10)" in new.body
    assert "corrected 1 headline" in result["players"][0]["summary"]
    assert ledger.totals(datetime.now())["allTime"] > 0
    assert [r["state"] for r in reports][-1] == "done"

    assert undo(paths) == ["alex"]
    assert (paths.profiles_dir / "alex.md").read_text() == old
    assert undo(paths) == []


def test_a_failed_call_installs_nothing_and_says_why(paths):
    from vamos.brain.provider import CallFailed

    add_game(paths, "pglNEW", 179_137_440_000_000)
    old = (paths.profiles_dir / "alex.md").read_text()
    update = Update(
        paths,
        FakeProvider([CallFailed("out of API credits")]),
        Ledger(paths.data_dir / "c.jsonl"),
        lambda r: None,
    )
    update.plan()
    result = update.run()
    assert result["state"] == "failed"
    assert result["players"][0]["summary"] == "out of API credits"
    assert (paths.profiles_dir / "alex.md").read_text() == old


def test_nothing_new_means_nothing_to_update(paths):
    update = Update(paths, FakeProvider([]), Ledger(paths.data_dir / "c.jsonl"), lambda r: None)
    assert update.plan() == {"state": "none", "players": []}
    assert update.run()["state"] == "none"
