"""Quality gates for the committed preflop tables (docs/preflop.md)."""

import json

import numpy as np

from vamos.preflop.matchups import COMBO_COUNTS, INDEX
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.solver import evaluate
from vamos.preflop.tables import DIR, MANIFEST, digest, load_all
from vamos.preflop.tree import PUSH_FOLD, build

PRIOR = COMBO_COUNTS / COMBO_COUNTS.sum()


def test_the_tables_are_committed():
    # The backend quietly turns the chart off without them, so their absence must fail here.
    assert MANIFEST.exists()


def manifest() -> dict:
    return json.loads(MANIFEST.read_text())["files"]


def test_files_match_the_manifest():
    files = manifest()
    assert files
    assert sorted(p.name for p in DIR.glob("*.json.gz")) == sorted(files)
    for name, summary in files.items():
        assert digest((DIR / name).read_bytes()) == summary["sha256"], name


def test_every_table_met_its_convergence_target():
    for name, summary in manifest().items():
        assert summary["nash_conv_mbb"] < 0.5, (name, summary)


def test_every_table_is_near_nash_when_recomputed():
    # Not just the number each file reports: both players' best-response gains against the
    # shipped strategies, recomputed.
    payoffs = ChipPayoffs()
    for table in load_all()[("pushfold", 2)]:
        tree = build(2, table.stack, PUSH_FOLD)
        strategy = {}
        for d in tree.decisions:
            spot = table.spots[d.path]
            assert spot.actions == d.actions
            strategy[d.id] = spot.strategy
        ev, best = evaluate(tree, payoffs, strategy)
        assert (best - ev).sum() * 1000 < 0.5, table.stack


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
                if "fold" not in spot.actions or table.untrained[path][INDEX["AA"]]:
                    continue
                fold = spot.strategy[spot.actions.index("fold"), INDEX["AA"]]
                assert fold < 0.001, (table.players, table.stack, path, fold)


def played(strategy: np.ndarray, actions: tuple[str, ...], reach: np.ndarray) -> float:
    """Share of the reaching combos that do not fold."""
    folds = strategy[actions.index("fold")] if "fold" in actions else 0
    return float((reach * (1 - folds)).sum() / reach.sum())


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
