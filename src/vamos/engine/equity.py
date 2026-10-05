"""
Hero equity against opponents' weighted ranges.

eval7 evaluates hands fast, but its range helpers silently ignore combo weights (and its
"exact" helper does not deal remaining cards), so weighting is done here:
- heads-up turn and river: exact enumeration over weighted combos and runouts;
- heads-up preflop and flop: eval7's C Monte Carlo per group of equal-weight combos,
  recombined by the live weight of each group;
- multiway: weighted Monte Carlo in Python.
Ties count half. Every method is checked against brute force in tests.
"""

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import eval7
import eval7.xorshift_rand
import numpy as np

type Combo = tuple[str, str]
type Range = dict[Combo, float]

DECK = tuple(r + s for r in "23456789TJQKA" for s in "shdc")
_CARD = {c: eval7.Card(c) for c in DECK}

HEADS_UP_SAMPLES = 200_000
MULTIWAY_SAMPLES = 10_000
# eval7 parses a range string on construction (and warns about a deprecated pyparsing call
# each time), so one instance is built once and refilled per call.
_GROUP = eval7.HandRange("AA")


@dataclass(frozen=True)
class Equity:
    value: float
    stderr: float  # 0 when exact
    method: str  # "exact" or "monte carlo"


def any_two(dead: Sequence[str] = ()) -> Range:
    """Every two-card combo not using a dead card, equally weighted."""
    live = [c for c in DECK if c not in set(dead)]
    return {(a, b): 1.0 for a, b in combinations(live, 2)}


def _live(rng: Range, dead: set[str]) -> Range:
    return {c: w for c, w in rng.items() if w > 0 and c[0] not in dead and c[1] not in dead}


def _exact_heads_up(hero: Sequence[str], board: Sequence[str], rng: Range) -> Equity:
    known = set(hero) | set(board)
    live = _live(rng, known)
    total = won = 0.0
    for combo, weight in live.items():
        rest = [c for c in DECK if c not in known and c not in combo]
        runouts = list(combinations(rest, 5 - len(board)))
        score = 0.0
        for runout in runouts:
            cards = [_CARD[c] for c in (*board, *runout)]
            mine = eval7.evaluate([_CARD[hero[0]], _CARD[hero[1]], *cards])
            theirs = eval7.evaluate([_CARD[combo[0]], _CARD[combo[1]], *cards])
            score += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
        won += weight * score / len(runouts)
        total += weight
    if total == 0:
        raise ValueError("no live combos in the opponent's range")
    return Equity(won / total, 0.0, "exact")


def _sampled_heads_up(
    hero: Sequence[str], board: Sequence[str], rng: Range, samples: int, seed: int
) -> Equity:
    live = _live(rng, set(hero) | set(board))
    if not live:
        raise ValueError("no live combos in the opponent's range")
    # eval7 seeds itself from the clock; a fixed seed gives the same spot the same number.
    # Its xorshift state must not be zero (that state never changes and the sampler hangs).
    eval7.xorshift_rand.seed(random.Random(seed).getrandbits(62) | 1)
    groups: dict[float, list[Combo]] = defaultdict(list)
    for combo, weight in live.items():
        groups[weight].append(combo)
    total = sum(w * len(c) for w, c in groups.items())
    hero_cards = [_CARD[c] for c in hero]
    board_cards = [_CARD[c] for c in board]
    value = variance = 0.0
    for weight, combos in groups.items():
        share = weight * len(combos) / total
        _GROUP.hands = [((_CARD[a], _CARD[b]), 1.0) for a, b in combos]
        n = max(2_000, round(samples * share))
        eq = eval7.py_hand_vs_range_monte_carlo(hero_cards, _GROUP, board_cards, n)
        value += share * eq
        variance += share**2 * eq * (1 - eq) / n
    return Equity(value, math.sqrt(variance), "monte carlo")


def _sampled_multiway(
    hero: Sequence[str], board: Sequence[str], ranges: Sequence[Range], samples: int, seed: int
) -> Equity:
    gen = np.random.default_rng(seed)
    rand = random.Random(seed)
    known = set(hero) | set(board)
    draws = []
    for rng in ranges:
        live = _live(rng, known)
        if not live:
            raise ValueError("no live combos in an opponent's range")
        combos = list(live)
        weights = np.fromiter(live.values(), float)
        # Each opponent's hands for every sample, drawn up front by weight.
        picks = gen.choice(len(combos), size=samples * 2, p=weights / weights.sum())
        draws.append([combos[i] for i in picks])
    hero_cards = [_CARD[c] for c in hero]
    board_cards = [_CARD[c] for c in board]
    deck = [c for c in DECK if c not in known]
    need = 5 - len(board)
    total = 0.0
    done = attempts = 0
    while done < samples and attempts < samples * 2:
        hands = [draw[attempts] for draw in draws]
        attempts += 1
        used = {card for hand in hands for card in hand}
        if len(used) < 2 * len(hands):
            continue  # two opponents drew overlapping hands
        # Over-draw so the runout never needs a card an opponent holds.
        runout = [c for c in rand.sample(deck, need + len(used)) if c not in used][:need]
        cards = board_cards + [_CARD[c] for c in runout]
        mine = eval7.evaluate([*hero_cards, *cards])
        theirs = [eval7.evaluate([_CARD[a], _CARD[b], *cards]) for a, b in hands]
        best = max(theirs)
        if mine > best:
            total += 1.0
        elif mine == best:
            total += 1.0 / (theirs.count(mine) + 1)
        done += 1
    if done == 0:
        raise ValueError("opponents' ranges cannot all be dealt at once")
    value = total / done
    return Equity(value, math.sqrt(value * (1 - value) / done), "monte carlo")


def equity(
    hero: Sequence[str],
    board: Sequence[str],
    ranges: Sequence[Range],
    samples: int | None = None,
    seed: int = 0,
) -> Equity:
    """Hero's share of the pot at showdown against every opponent's range at once."""
    if len(hero) != 2 or len(board) not in (0, 3, 4, 5) or not ranges:
        raise ValueError("need two hero cards, a 0/3/4/5-card board and at least one range")
    if len(ranges) > 1:
        return _sampled_multiway(hero, board, ranges, samples or MULTIWAY_SAMPLES, seed)
    if len(board) >= 4:
        return _exact_heads_up(hero, board, ranges[0])
    return _sampled_heads_up(hero, board, ranges[0], samples or HEADS_UP_SAMPLES, seed)
