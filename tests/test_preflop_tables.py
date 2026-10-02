"""Quality gates for the committed preflop tables (docs/preflop.md)."""

import json

import numpy as np
import pytest

from vamos.preflop.matchups import COMBO_COUNTS, INDEX
from vamos.preflop.tables import DIR, MANIFEST, digest, load_all

PRIOR = COMBO_COUNTS / COMBO_COUNTS.sum()
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="no tables generated yet")


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())["files"]


def test_files_match_the_manifest():
    files = manifest()
    assert files
    assert sorted(p.name for p in DIR.glob("*.json.gz")) == sorted(files)
    for name, summary in files.items():
        assert digest((DIR / name).read_bytes()) == summary["sha256"], name


def test_every_table_met_its_convergence_target():
    targets = {2: 0.5, 3: 3.0, 4: 5.0}
    for name, summary in manifest().items():
        players = int(name.split("-")[1][0])
        assert summary["nash_conv_mbb"] < targets[players], (name, summary)


def test_every_mix_is_a_distribution():
    for tables in load_all().values():
        for table in tables:
            for spot in table.spots.values():
                assert ((spot.strategy >= 0) & (spot.strategy <= 1)).all()
                assert np.allclose(spot.strategy.sum(axis=0), 1, atol=0.005)


def test_aces_never_fold():
    for tables in load_all().values():
        for table in tables:
            for path, spot in table.spots.items():
                if "fold" in spot.actions and not spot.unlearned[INDEX["AA"]]:
                    fold = spot.strategy[spot.actions.index("fold"), INDEX["AA"]]
                    assert fold < 0.01, (table.players, table.stack, path, fold)


def played(strategy: np.ndarray, actions: tuple[str, ...], reach: np.ndarray) -> float:
    """Share of the reaching combos that do not fold."""
    folds = strategy[actions.index("fold")] if "fold" in actions else 0
    return float((reach * (1 - folds)).sum() / reach.sum())


@pytest.mark.parametrize("players", [3, 4])
def test_fewer_players_behind_play_more_hands(players):
    for table in load_all()[("standard", players)]:
        path: tuple[str, ...] = ()
        shares = []
        while path in table.spots and table.spots[path].position != "BB":
            spot = table.spots[path]
            shares.append(played(spot.strategy, spot.actions, PRIOR))
            path = (*path, f"{spot.position}:fold")
        assert shares == sorted(shares), (players, table.stack, shares)


def test_fewer_hands_continue_against_a_three_bet_than_defend_an_open():
    for table in load_all()[("standard", 2)]:
        root = table.spots[()]
        opened = PRIOR * root.strategy[root.actions.index("raise 3")]
        defend = table.spots[("SB:raise 3",)]
        defending = played(defend.strategy, defend.actions, PRIOR) * PRIOR.sum()
        three_bet = next(a for a in defend.actions if a.startswith("raise"))
        facing = table.spots.get(("SB:raise 3", f"BB:{three_bet}"))
        if facing is None:
            continue
        continuing = played(facing.strategy, facing.actions, opened) * opened.sum()
        assert continuing < defending, table.stack


def test_a_bigger_open_is_defended_less():
    for table in load_all()[("standard", 2)]:
        small = table.spots[("SB:raise 2.5",)]
        big = table.spots[("SB:raise 3",)]
        assert played(big.strategy, big.actions, PRIOR) <= played(
            small.strategy, small.actions, PRIOR
        ), table.stack


def test_the_main_lines_are_learned():
    # Every hand at the first decision and at the answer to each first action was learned;
    # deeper spots nobody reaches give no advice.
    for tables in load_all().values():
        for table in tables:
            first = table.spots[()]
            assert not first.unlearned.any()
            for action in first.actions:
                spot = table.spots.get((f"{first.position}:{action}",))
                assert spot is None or not spot.unlearned.any(), (
                    table.players,
                    table.stack,
                    action,
                )
