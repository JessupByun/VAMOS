"""
How the hero's preflop decisions in saved games would be answered today: the push/fold
chart, a rule (fold, check or raise), or Claude. For tracking rule coverage over time.

The split depends mostly on table size (heads-up nearly everything goes to Claude) and on
the cards the hero was dealt, so it is broken down by players dealt and by situation, and
each figure comes with what random cards would give at the same spots. Saved games are
pruned after 3 days, so each run measures the games listed at the top.
Run: uv run python scripts/replay_preflop_routes.py [data/games/<game>.jsonl ...]
"""

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from vamos.cards import ALL_HAND_CLASSES
from vamos.config import DATA_DIR
from vamos.engine.advice import preflop_advice
from vamos.engine.state import Decision, decision
from vamos.log.hands import Hand, Street, build_hands
from vamos.preflop import tables


@dataclass
class Spot:
    hand: Hand
    spot: Decision
    hero: str
    route: str


def hero_names() -> set[str]:
    path = DATA_DIR / "settings.json"
    saved = json.loads(path.read_text()) if path.exists() else {}
    return {str(n).strip().lower() for n in saved.get("heroNames", [])}


def route(advice: dict | None) -> str:
    if advice is None:
        return "Claude"
    verb = advice["action"].split()[0].lower()
    return f"{advice['source'].lower()} {'all in' if verb == 'all' else verb}"


def situation(hand: Hand, hero: str) -> str:
    before = [a for a in hand.actions if a.street == Street.PREFLOP and a.player != hero]
    raises = sum(a.kind == "raise" for a in before)
    if raises >= 2 or any(a.kind == "raise" and a.player == hero for a in hand.actions):
        return "3-bet or more"
    if raises == 1:
        return "facing one raise"
    return "facing limps" if any(a.kind == "call" for a in before) else "unopened"


def table_size(players: int) -> str:
    return {2: "heads-up", 3: "3-handed", 4: "4-handed"}.get(players, "5+ handed")


def spots(path: Path, names: set[str], table_sets) -> tuple[list[Spot], Counter]:
    """The hero's preflop decisions in one saved game, and the hands dealt by table size."""
    rows = sorted((int(e["order"]), e["msg"]) for e in map(json.loads, path.open()) if "order" in e)
    found: list[Spot] = []
    dealt: Counter = Counter()
    for hand in build_hands(rows).hands:
        hero = next(
            (s.player.id for s in hand.seats if s.player.name.strip().lower() in names), None
        )
        if hero is None or not hand.hero_cards:
            continue
        dealt[table_size(len(hand.seats))] += 1
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
            if part is None or spot is None or spot.street != Street.PREFLOP:
                continue
            advice = preflop_advice(part, spot, hero, table_sets)
            found.append(Spot(part, spot, hero, route(advice)))
    return found, dealt


def _cards(cls: str) -> tuple[str, str]:
    if len(cls) == 2:
        return cls[0] + "s", cls[1] + "h"
    return cls[0] + "s", cls[1] + ("s" if cls[2] == "s" else "h")


def _combos(cls: str) -> int:
    return 6 if len(cls) == 2 else 4 if cls[2] == "s" else 12


CLASS_WEIGHTS = {c: _combos(c) / 1326 for c in ALL_HAND_CLASSES}


def random_routes(s: Spot, table_sets) -> Counter:
    """The share of each route at this spot if the hero held random cards."""
    out: Counter = Counter()
    for cls, weight in CLASS_WEIGHTS.items():
        dealt = replace(s.spot, hero_cards=_cards(cls))
        out[route(preflop_advice(s.hand, dealt, s.hero, table_sets))] += weight
    return out


def table(title: str, groups: dict[str, list[Spot]], expected: dict[int, Counter], hands=None):
    """Claude's share per group, observed and with random cards (and per 100 hands dealt)."""
    extra = "  Claude per 100 hands" if hands else ""
    print(f"\n{title:18} decisions  Claude  random cards{extra}")
    for name, members in groups.items():
        claude = sum(s.route == "Claude" for s in members)
        rand = sum(expected[id(s)]["Claude"] for s in members)
        line = (
            f"{name:18} {len(members):9}  {100 * claude / len(members):5.0f}%"
            f"  {100 * rand / len(members):11.0f}%"
        )
        if hands and hands.get(name):
            line += f"  {100 * claude / hands[name]:21.0f}"
        print(line)


def main() -> None:
    paths = [Path(a) for a in sys.argv[1:]] or sorted((DATA_DIR / "games").glob("*.jsonl"))
    names, table_sets = hero_names(), tables.load_all()
    every: list[Spot] = []
    hands_by_size: Counter = Counter()
    for path in paths:
        found, dealt = spots(path, names, table_sets)
        every += found
        hands_by_size += dealt
        print(
            f"{path.name}: {sum(dealt.values())} hands dealt to the hero, "
            f"{len(found)} preflop decisions"
        )
    if not every:
        print("No preflop decisions found.")
        return
    expected = {id(s): random_routes(s, table_sets) for s in every}
    n = len(every)
    observed = Counter(s.route for s in every)
    rand_total: Counter = sum(expected.values(), Counter())
    print(f"\nAll games: {n} decisions")
    print(f"{'route':18} decisions  share  random cards")
    for name in sorted(set(observed) | set(rand_total), key=lambda r: -observed[r]):
        print(
            f"{name:18} {observed[name]:9}  {100 * observed[name] / n:4.0f}%"
            f"  {100 * rand_total[name] / n:11.0f}%"
        )
    by_size: dict[str, list[Spot]] = defaultdict(list)
    by_situation: dict[str, list[Spot]] = defaultdict(list)
    for s in every:
        by_size[table_size(s.spot.players_dealt)].append(s)
        by_situation[situation(s.hand, s.hero)].append(s)
    order = ["heads-up", "3-handed", "4-handed", "5+ handed"]
    table("players dealt", {k: by_size[k] for k in order if by_size[k]}, expected, hands_by_size)
    kinds = ["unopened", "facing limps", "facing one raise", "3-bet or more"]
    table("situation", {k: by_situation[k] for k in kinds if by_situation[k]}, expected)


if __name__ == "__main__":
    main()
