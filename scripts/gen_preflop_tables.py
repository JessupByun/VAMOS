"""
Solve the preflop baseline and write src/vamos/preflop/tables/ (see vamos.preflop.tables).

Full trees at 20-150bb and push/fold at 2-15bb, for 4, 3 and 2 players. Each solve runs
until its NashConv (exploitability, heads-up) is under the target or the iteration cap is
reached; both are recorded in the file's provenance and in the manifest.
Run: uv run python scripts/gen_preflop_tables.py [players ...] [standard|pushfold]
"""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from multiprocessing import Pool

from vamos.preflop import realization
from vamos.preflop.lookup import table_from
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.realization import Realization
from vamos.preflop.solver import Solver
from vamos.preflop.tables import DIR, MANIFEST, digest, encode, file_name
from vamos.preflop.tree import PUSH_FOLD, STANDARD, build

DEPTHS = (20, 35, 50, 75, 100, 150)
PUSH_FOLD_DEPTHS = tuple(range(2, 16))
# NashConv targets in mbb/hand and iteration caps. Multiway CFR has no convergence
# guarantee, so its targets are looser.
TARGET = {2: 0.5, 3: 3.0, 4: 5.0}
CAP = {2: 4000, 3: 1500, 4: 1000}
CHECK_EVERY = 100
# Four-handed solves hold about 2 GB of cached tables each.
WORKERS = 3


def solve(job: tuple[str, int, float]) -> tuple[str, str, dict]:
    menu_name, players, stack = job
    menu = STANDARD if menu_name == "standard" else PUSH_FOLD
    started = time.monotonic()
    tree = build(players, stack, menu)
    solver = Solver(tree, ChipPayoffs(flop=Realization(stack)))
    while True:
        solver.run(CHECK_EVERY)
        solution = solver.solution()
        nash_conv = solution.nash_conv * 1000
        if nash_conv < TARGET[players] or solver.iterations >= CAP[players]:
            break
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    provenance = (
        f"VAMOS discounted CFR, {solver.iterations} iterations, NashConv {nash_conv:.2f} "
        f"mbb/hand; flop model theta {realization.THETA.tolist()}, out of position "
        f"{realization.OUT_OF_POSITION}, cap {realization.CAP}, SPR scale "
        f"{realization.SPR_SCALE}; code {commit}; generated {datetime.now(UTC):%Y-%m-%d}"
    )
    data = encode(table_from(solution, provenance), menu_name)
    name = file_name(menu_name, players, stack)
    (DIR / name).write_bytes(data)
    summary = {
        "sha256": digest(data),
        "iterations": solver.iterations,
        "nash_conv_mbb": round(nash_conv, 3),
        "ev_sum_bb": round(float(solution.ev.sum()), 4),
        "seconds": round(time.monotonic() - started),
    }
    print(f"[VAMOS] {name}: {summary}", flush=True)
    return name, menu_name, summary


def main() -> None:
    # Workers inherit this environment; without it each starts its own pool of math-library
    # threads and the machine ends up oversubscribed many times over.
    for var in ("VECLIB_MAXIMUM_THREADS", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ.setdefault(var, "2")
    players = [int(a) for a in sys.argv[1:] if a.isdigit()] or [4, 3, 2]
    menus = [a for a in sys.argv[1:] if not a.isdigit()] or ["standard", "pushfold"]
    jobs = []
    if "standard" in menus:
        jobs += [("standard", p, float(d)) for p in players for d in DEPTHS]
    if "pushfold" in menus:
        jobs += [("pushfold", p, float(d)) for p in players for d in PUSH_FOLD_DEPTHS]
    DIR.mkdir(exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"files": {}}
    with Pool(WORKERS) as pool:
        for name, _, summary in pool.imap_unordered(solve, jobs):
            manifest["files"][name] = summary
            MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
