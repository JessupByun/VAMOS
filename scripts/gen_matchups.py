"""
Generate src/vamos/preflop/data/matchups.npz: card-removal counts and all-in equity for
every pair of the 169 starting-hand classes (see vamos.preflop.matchups).
Run: uv run python scripts/gen_matchups.py [samples per pair, default 400000]
"""

import sys
import time
from datetime import UTC, datetime
from functools import partial
from importlib.metadata import version
from multiprocessing import Pool

import numpy as np

from vamos.preflop.matchups import CLASSES, DATA, assemble, row


def main() -> None:
    samples = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
    started = time.monotonic()
    with Pool() as pool:
        rows = pool.map(partial(row, samples=samples), range(len(CLASSES)), chunksize=1)
    counts, equity = assemble(rows)
    provenance = (
        f"eval7 {version('eval7')} Monte Carlo, {samples} samples per class pair, "
        f"one representative combo per row class; generated {datetime.now(UTC):%Y-%m-%d}"
    )
    DATA.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(DATA, counts=counts, equity=equity, provenance=np.array(provenance))
    print(f"[VAMOS] {DATA.name}: {provenance} ({time.monotonic() - started:.0f}s)")


if __name__ == "__main__":
    main()
