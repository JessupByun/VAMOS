"""
Generate src/vamos/preflop/data/multiway{3,4}.npz: bucketed all-in pot shares for three-
and four-way pots (see vamos.preflop.multiway).
Run: uv run python scripts/gen_multiway.py
"""

import tempfile
import time
from datetime import UTC, datetime
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from vamos.preflop import matchups
from vamos.preflop.multiway import BUCKETS, DATA, board_ranks, buckets, deals, pool

BOARDS = 50_000
DEALS = {3: 2_000_000, 4: 3_000_000}  # per class of ours
SEED = 20261002

_ranks: np.ndarray | None = None


def _load(path: str) -> None:
    global _ranks
    _ranks = np.load(path, mmap_mode="r")


def _deals(hero: int, labels: np.ndarray, players: int) -> tuple[np.ndarray, np.ndarray]:
    assert _ranks is not None
    return deals(hero, labels, players, _ranks, DEALS[players], SEED + 1000 * players + hero)


def main() -> None:
    started = time.monotonic()
    gen = np.random.default_rng(SEED)
    boards = np.array([gen.choice(52, size=5, replace=False) for _ in range(BOARDS)])
    with Pool() as workers:
        ranks = np.concatenate(workers.map(board_ranks, np.array_split(boards, 64)))
    print(f"[VAMOS] board ranks: {ranks.shape} ({time.monotonic() - started:.0f}s)")
    equity = matchups.load().equity
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "ranks.npy")
        np.save(path, ranks)
        del ranks
        for players, k in BUCKETS.items():
            labels = buckets(equity, k)
            with Pool(initializer=_load, initargs=(path,)) as workers:
                rows = workers.map(
                    partial(_deals, labels=labels, players=players), range(len(matchups.CLASSES))
                )
            shape = (len(matchups.CLASSES), *(k,) * (players - 1))
            shares = np.stack([r[0] for r in rows]).reshape(*shape, players)
            counts = np.stack([r[1] for r in rows]).reshape(shape)
            provenance = (
                f"{players}-way: {k} opponent buckets (k-means on heads-up equity rows), "
                f"{DEALS[players]} deals per class over {BOARDS} boards, seed {SEED}, "
                f"fewest deals in a cell {int(counts.min())}; "
                f"generated {datetime.now(UTC):%Y-%m-%d}"
            )
            np.savez_compressed(
                DATA / f"multiway{players}.npz",
                labels=labels,
                share=pool(shares, counts).astype(np.float32),
                counts=counts.astype(np.int32),
                provenance=np.array(provenance),
            )
            print(f"[VAMOS] {provenance} ({time.monotonic() - started:.0f}s)")


if __name__ == "__main__":
    main()
