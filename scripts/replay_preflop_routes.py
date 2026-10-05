"""
How the hero's preflop decisions in saved games would be answered today: the push/fold
chart, a rule (fold, check or raise), or Claude. For tracking rule coverage over time.
Run: uv run python scripts/replay_preflop_routes.py [data/games/<game>.jsonl ...]
"""

import json
import sys
from collections import Counter
from pathlib import Path

from vamos.config import DATA_DIR
from vamos.engine.advice import preflop_advice
from vamos.engine.state import decision
from vamos.log.hands import Street, build_hands
from vamos.preflop import tables


def hero_names() -> set[str]:
    path = DATA_DIR / "settings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {str(n).strip().lower() for n in saved.get("heroNames", [])}


def routes(path: Path, names: set[str], table_sets) -> Counter:
    rows = sorted((int(e["order"]), e["msg"]) for e in map(json.loads, path.open()) if "order" in e)
    log = build_hands(rows)
    out: Counter = Counter()
    for hand in log.hands:
        hero = next(
            (s.player.id for s in hand.seats if s.player.name.strip().lower() in names), None
        )
        if hero is None or not hand.hero_cards:
            continue
        mine = [
            a
            for a in hand.actions
            if a.street == Street.PREFLOP
            and a.player == hero
            and a.kind in ("fold", "check", "call", "raise")
        ]
        for action in mine:
            before = [r for r in rows if r[0] < action.order]
            part = next((h for h in build_hands(before).hands if h.number == hand.number), None)
            spot = decision(part, hero) if part else None
            if spot is None or spot.street != Street.PREFLOP:
                continue
            advice = preflop_advice(part, spot, hero, table_sets)
            if advice is None:
                out["Claude"] += 1
            else:
                kind = advice["action"].split()[0].lower()
                out[f"{advice['source'].lower()} {kind}"] += 1
    return out


def main() -> None:
    paths = [Path(a) for a in sys.argv[1:]] or sorted((DATA_DIR / "games").glob("*.jsonl"))
    names, table_sets = hero_names(), tables.load_all()
    total: Counter = Counter()
    for path in paths:
        counts = routes(path, names, table_sets)
        total += counts
        print(f"{path.name}: {sum(counts.values())} decisions")
    n = sum(total.values())
    for route, count in total.most_common():
        print(f"  {route:12} {count:4}  {100 * count / n:4.0f}%")


if __name__ == "__main__":
    main()
