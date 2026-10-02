"""
Heads-up card-removal counts and all-in equity between the 169 starting-hand classes.

Preflop strategies are per class, but payoffs depend on the cards: holding an ace makes an
opponent's aces rarer. For classes i and j, a relabeling of suits maps any combo of i to
any other while leaving j's combos as a set unchanged, so one representative combo of i
against every combo of j it does not collide with gives the exact class-vs-class average.

COUNTS[i, j] is the number of card-compatible combo pairs; EQUITY[i, j] is i's all-in
equity against j (ties count half) averaged over those pairs, by Monte Carlo
(scripts/gen_matchups.py; provenance is stored alongside the arrays).
"""

from dataclasses import dataclass
from functools import cache
from itertools import combinations
from pathlib import Path

import numpy as np

from vamos.cards import ALL_HAND_CLASSES, SUITS
from vamos.engine.equity import equity

CLASSES = ALL_HAND_CLASSES
INDEX = {c: k for k, c in enumerate(CLASSES)}
DATA = Path(__file__).parent / "data" / "matchups.npz"


def combos(hand_class: str) -> list[tuple[str, str]]:
    high, low = hand_class[0], hand_class[1]
    if len(hand_class) == 2:
        return [(high + a, low + b) for a, b in combinations(SUITS, 2)]
    if hand_class[2] == "s":
        return [(high + s, low + s) for s in SUITS]
    return [(high + a, low + b) for a in SUITS for b in SUITS if a != b]


COMBO_COUNTS = np.array([len(combos(c)) for c in CLASSES])


def row(i: int, samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Counts and equity of class i against every class j >= i."""
    rep = combos(CLASSES[i])[0]
    counts = np.zeros(len(CLASSES), dtype=np.int64)
    values = np.full(len(CLASSES), np.nan)
    for j in range(i, len(CLASSES)):
        live = [c for c in combos(CLASSES[j]) if not set(c) & set(rep)]
        counts[j] = len(combos(CLASSES[i])) * len(live)
        if j == i:
            values[j] = 0.5  # a class against itself is symmetric
        elif live:
            values[j] = equity(rep, (), [dict.fromkeys(live, 1.0)], samples=samples).value
    return counts, values


def assemble(rows: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    n = len(CLASSES)
    counts = np.zeros((n, n), dtype=np.int64)
    values = np.full((n, n), 0.5)
    for i, (c, v) in enumerate(rows):
        counts[i, i:] = c[i:]
        counts[i:, i] = c[i:]
        values[i, i + 1 :] = v[i + 1 :]
        # Each pair of combos is the same deal seen from the other side; ties count half
        # for both, so the two equities sum to exactly one.
        values[i + 1 :, i] = 1 - v[i + 1 :]
    return counts, np.nan_to_num(values, nan=0.5)


@dataclass(frozen=True)
class Matchups:
    counts: np.ndarray  # (169, 169) compatible combo pairs
    equity: np.ndarray  # (169, 169) all-in equity of the row class
    provenance: str

    @property
    def given(self) -> np.ndarray:
        """P(opponent holds class j | we hold class i): each row sums to one."""
        return self.counts / self.counts.sum(axis=1, keepdims=True)


@cache
def load() -> Matchups:
    with np.load(DATA) as data:
        return Matchups(data["counts"], data["equity"], str(data["provenance"]))
