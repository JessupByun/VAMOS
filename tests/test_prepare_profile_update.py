"""scripts/prepare_profile_update.py: new games labelled, work folders written, profiles installed."""

import importlib.util
import json
from pathlib import Path

import pytest

from vamos.profiles import parse_profile

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_profile_update.py"
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
def script(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("prepare_profile_update", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data, profiles = tmp_path / "data", tmp_path / "profiles"
    (data / "games").mkdir(parents=True)
    profiles.mkdir()
    (profiles / "alex.md").write_text(PROFILE.format(player=ALEX))
    (data / "settings.json").write_text('{"heroNames": ["jes"]}')
    module.DATA_DIR, module.PROFILES_DIR = data, profiles
    module.REGISTRY = data / "profile-sessions.json"
    module.WORK, module.HISTORY = data / "profile-work", data / "profile-history"
    return module


def add_game(script, game: str, start: int) -> None:
    path = script.DATA_DIR / "games" / f"{game}.jsonl"
    path.write_text(
        "".join(json.dumps({"order": start + i, "msg": m}) + "\n" for i, m in enumerate(GAME_ROWS))
    )


def test_labels_go_on_from_the_last_letter_used(script):
    assert script.next_label({"HU", "A", "N"}) == "O"
    assert script.next_label({"Z"}) == "AA"
    assert script.next_label({"AZ", "B"}) == "BA"
    assert script.next_label(set()) == "A"


def test_what_a_profile_has_absorbed(script):
    profile = parse_profile(
        "rj",
        "---\nname: rj\nsessions: [A 2026-09-30, O 2026-10-07]\n"
        "games: {O: pglX through hand 40}\nexcluded: [D 2026-10-04 at his request]\n---\nbody\n",
    )
    assert script.absorbed(profile) == ({"A"}, {"O": 40}, {"D"})


def test_a_new_game_is_labelled_and_prepared_for_the_players_in_it(script):
    # Noon UTC, so the game is dated 2026-10-07 in every time zone the date is taken in.
    add_game(script, "pglNEW", 179_137_440_000_000)
    registry = {"sessions": {"A": {"date": "2026-09-30"}}, "excluded": {}}
    assert script.register_new_games(registry) == ["B"]
    assert registry["sessions"]["B"] == {"game": "pglNEW", "date": "2026-10-07"}
    work = script.prepare(registry, None)
    players = json.loads((work / "players.json").read_text())
    assert [p["key"] for p in players] == ["alex"]
    assert "B: pglNEW through hand 1" in players[0]["sessions"]
    stats = json.loads((work / "alex.stats.json").read_text())
    assert stats["hands_by_session"] == {"B": 1}
    assert "### B1" in (work / "alex.digest.md").read_text()


def test_a_game_already_absorbed_or_excluded_is_not_prepared(script):
    add_game(script, "pglOLD", 100)
    registry = {"sessions": {"A": {"date": "2026-09-30", "game": "pglOLD"}}, "excluded": {}}
    assert script.register_new_games(registry) == []
    assert script.prepare(registry, None) is None
    add_game(script, "pglTEST", 200)
    registry["excluded"]["pglTEST"] = "a test"
    assert script.register_new_games(registry) == []


def test_install_keeps_both_versions_and_refuses_a_profile_the_panel_cannot_read(script):
    work = script.WORK / "w"
    (work / "new").mkdir(parents=True)
    (work / "sessions.json").write_text('{"B": {}}')
    old = (script.PROFILES_DIR / "alex.md").read_text()
    good = (
        old.replace("hands: 10", "hands: 11")
        + "- Went to showdown 50% (1/2), won at showdown 0% (0/1)\n"
    )
    (work / "new" / "alex.md").write_text(good)
    script.install(work)
    assert (script.PROFILES_DIR / "alex.md").read_text() == good
    kept = sorted(p.read_text() for p in (script.HISTORY / "alex").glob("*.md"))
    assert sorted([old, good]) == kept
    (work / "new" / "alex.md").write_text(good.replace("- VPIP 50% (5/10), PFR 10% (1/10)\n", ""))
    with pytest.raises(SystemExit):
        script.install(work)
    assert (script.PROFILES_DIR / "alex.md").read_text() == good
