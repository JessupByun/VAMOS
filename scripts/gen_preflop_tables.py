"""
Solve the heads-up push/fold chart at 2-10bb and write src/vamos/preflop/tables/ with its
manifest (see vamos.preflop.tables). Each solve runs until its NashConv (exploitability) is
under the target or the iteration cap is reached; both are recorded in the file's
provenance and in the manifest.
Run: uv run python scripts/gen_preflop_tables.py
"""

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from multiprocessing import Pool

from vamos.preflop.lookup import table_from
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.solver import Solver
from vamos.preflop.tables import DIR, MANIFEST, digest, encode, file_name
from vamos.preflop.tree import PUSH_FOLD, build

MENU = "pushfold"
DEPTHS = tuple(range(2, 11))
TARGET = 0.5  # NashConv in mbb/hand
CAP = 4000  # iterations
CHECK_EVERY = 100
WORKERS = 3


def solve(stack: float) -> tuple[str, dict]:
    started = time.monotonic()
    solver = Solver(build(2, stack, PUSH_FOLD), ChipPayoffs())
    while True:
        solver.run(CHECK_EVERY)
        solution = solver.solution()
        nash_conv = solution.nash_conv * 1000
        if nash_conv < TARGET or solver.iterations >= CAP:
            break
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    provenance = (
        f"VAMOS discounted CFR, {solver.iterations} iterations, NashConv {nash_conv:.2f} "
        f"mbb/hand; code {commit}; generated {datetime.now(UTC):%Y-%m-%d}"
    )
    data = encode(table_from(solution, provenance), MENU)
    name = file_name(MENU, 2, stack)
    (DIR / name).write_bytes(data)
    summary = {
        "sha256": digest(data),
        "iterations": solver.iterations,
        "nash_conv_mbb": round(nash_conv, 3),
        "ev_sum_bb": round(float(solution.ev.sum()), 4),
        "seconds": round(time.monotonic() - started),
    }
    print(f"[VAMOS] {name}: {summary}", flush=True)
    return name, summary


def main() -> None:
    # Workers inherit this environment; without it each starts its own pool of math-library
    # threads and the machine ends up oversubscribed many times over.
    for var in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ.setdefault(var, "2")
    DIR.mkdir(exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"files": {}}
    with Pool(WORKERS) as pool:
        for name, summary in pool.imap_unordered(solve, [float(d) for d in DEPTHS]):
            manifest["files"][name] = summary
            MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
