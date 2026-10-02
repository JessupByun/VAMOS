"""
Solved preflop tables on disk: one gzipped JSON file per player count and stack depth, in
a format that does not depend on the solver, so tables from another tool (HRC Pro) can
replace them without changing the runtime.

    {"format": "vamos-preflop/1", "players": 4, "stack": 50, "menu": "standard",
     "provenance": "...", "hands": [169 class labels],
     "spots": [{"path": ["CO:raise 2.5", "BTN:fold"], "position": "SB",
                "actions": ["fold", "call", "raise 10", "raise 12.5"],
                "strategy": [[169 frequencies] per action]}]}

Frequencies are rounded to 0.001. `manifest.json` lists every file with its SHA-256, and a
test checks that the committed files still match it.
"""

import gzip
import hashlib
import json
from functools import cache
from pathlib import Path

import numpy as np

from vamos.preflop.lookup import Spot, Table
from vamos.preflop.matchups import CLASSES

FORMAT = "vamos-preflop/1"
DIR = Path(__file__).parent / "tables"
MANIFEST = DIR / "manifest.json"


def encode(table: Table, menu: str) -> bytes:
    spots = [
        {
            "path": list(path),
            "position": spot.position,
            "actions": list(spot.actions),
            "strategy": np.round(spot.strategy, 3).tolist(),
        }
        for path, spot in table.spots.items()
    ]
    body = {
        "format": FORMAT,
        "players": table.players,
        "stack": table.stack,
        "menu": menu,
        "provenance": table.provenance,
        "hands": list(CLASSES),
        "spots": spots,
    }
    text = json.dumps(body, separators=(",", ":"), sort_keys=True)
    # mtime=0 keeps the bytes, and so the hash, the same for the same table.
    return gzip.compress(text.encode(), mtime=0)


def decode(data: bytes) -> tuple[Table, str]:
    body = json.loads(gzip.decompress(data))
    if body["format"] != FORMAT:
        raise ValueError(f"unknown preflop table format {body['format']!r}")
    order = [CLASSES.index(h) for h in body["hands"]]
    spots = {}
    for spot in body["spots"]:
        strategy = np.zeros((len(spot["actions"]), len(CLASSES)))
        strategy[:, order] = np.array(spot["strategy"])
        spots[tuple(spot["path"])] = Spot(spot["position"], tuple(spot["actions"]), strategy)
    table = Table(body["players"], float(body["stack"]), spots, body["provenance"])
    return table, body["menu"]


def file_name(menu: str, players: int, stack: float) -> str:
    return f"{menu}-{players}p-{stack:g}bb.json.gz"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@cache
def load_all() -> dict[tuple[str, int], list[Table]]:
    """Every committed table, grouped by (menu, players), shallowest first."""
    if not MANIFEST.exists():
        return {}
    manifest = json.loads(MANIFEST.read_text())
    grouped: dict[tuple[str, int], list[Table]] = {}
    for name in manifest["files"]:
        table, menu = decode((DIR / name).read_bytes())
        grouped.setdefault((menu, table.players), []).append(table)
    for tables in grouped.values():
        tables.sort(key=lambda t: t.stack)
    return grouped
