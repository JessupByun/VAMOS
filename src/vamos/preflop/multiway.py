"""
All-in equity in three- and four-way pots, for the preflop solver.

Exact joint equity for every triple of the 169 classes is 4.8 million entries (816 million
for four players), so each player's own class stays exact while opponents are grouped into
buckets of classes with similar heads-up equity profiles. Within a bucket an opponent's
hands count in proportion to their combos rather than to how often that opponent plays
them, which is the main approximation, measured in tests against exact multiway equity on
realistic ranges.

Opponents' hands are not independent of each other (two players rarely both hold aces).
Weighting cells as if they were overstated every player's share at once (3-way pots summed
to 101%), so `link` scales each cell by how much more or less often that mix of buckets
is dealt than independence predicts, measured from the same deals.

Tables are estimated by Monte Carlo over shared boards (scripts/gen_multiway.py):
share[i, b, c, s] is the mean pot share of seat s (0 is us holding class i, then opponents
from buckets b and c) when everyone is all in; four-way tables have one more bucket axis.
"""

from dataclasses import dataclass
from functools import cache
from itertools import combinations, permutations
from pathlib import Path

import eval7
import numpy as np

from vamos.cards import RANKS, SUITS, hand_class
from vamos.preflop.matchups import CLASSES, INDEX

DATA = Path(__file__).parent / "data"
# Opponent buckets per player count, split between pairs, suited and offsuit hands. Ranges
# that reach multiway pots keep the suited members of a mixed bucket and drop the offsuit
# ones, which mixed buckets cannot see: on solved 3- and 4-way spots they overrated every
# hand by 2.0 points on average, split buckets by 0.7 (measured 2026-10-02; a comparison on
# top-X% ranges, which take whole buckets, had wrongly favored mixed buckets).
BUCKETS = {3: (6, 17, 17), 4: (3, 6, 7)}

DECK = tuple(r + s for r in RANKS for s in SUITS)
COMBOS = np.array(list(combinations(range(52), 2)))  # (1326, 2) card indices
COMBO_CLASS = np.array([INDEX[hand_class(DECK[a], DECK[b])] for a, b in COMBOS])


def buckets(equity: np.ndarray, split: tuple[int, int, int], seed: int = 0) -> np.ndarray:
    """
    Group the 169 classes into buckets: `split` gives how many for pairs, suited and offsuit
    hands, each group clustered by its rows of heads-up equity (k-means). Buckets are
    numbered from strongest to weakest.
    """
    kinds = np.array([0 if len(c) == 2 else 1 if c[2] == "s" else 2 for c in CLASSES])
    labels = np.zeros(len(CLASSES), dtype=np.int64)
    offset = 0
    for kind, k in enumerate(split):
        members = np.flatnonzero(kinds == kind)
        labels[members] = offset + _kmeans(equity[members], k, seed)
        offset += k
    strength = np.array([equity[labels == b].mean() for b in range(offset)])
    rank = np.empty(offset, dtype=np.int64)
    rank[np.argsort(-strength)] = np.arange(offset)
    return rank[labels]


def _kmeans(equity: np.ndarray, k: int, seed: int, rounds: int = 200) -> np.ndarray:
    gen = np.random.default_rng(seed)
    centers = equity[gen.choice(len(equity), size=k, replace=False)]
    labels = np.zeros(len(equity), dtype=np.int64)
    for _ in range(rounds):
        distance = ((equity[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = distance.argmin(axis=1)
        moved = np.array(
            [
                equity[labels == b].mean(axis=0) if (labels == b).any() else centers[b]
                for b in range(k)
            ]
        )
        if np.allclose(moved, centers):
            break
        centers = moved
    return labels


def board_ranks(boards: np.ndarray) -> np.ndarray:
    """Every combo's hand rank on each board (higher wins), or -1 where a card is shared."""
    cards = [eval7.Card(c) for c in DECK]
    ranks = np.full((len(boards), len(COMBOS)), -1, dtype=np.int32)
    for n, board in enumerate(boards):
        table = [cards[c] for c in board]
        used = set(board.tolist())
        for k, (a, b) in enumerate(COMBOS):
            if a not in used and b not in used:
                ranks[n, k] = eval7.evaluate([cards[a], cards[b], *table])
    return ranks


def deals(
    hero_class: int,
    labels: np.ndarray,
    players: int,
    ranks: np.ndarray,
    count: int,
    seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Summed pot shares (cells, seats) and deal counts (cells,) over at least `count` random
    deals in which seat 0 holds `hero_class`; cells index the opponents' buckets in seat
    order.
    """
    gen = np.random.default_rng(seed)
    k = int(labels.max()) + 1
    shape = (k,) * (players - 1)
    cells = int(np.prod(shape))
    shares = np.zeros((cells, players))
    counts = np.zeros(cells)
    mine = np.flatnonzero(np.equal(COMBO_CLASS, hero_class))
    done = 0
    while done < count:
        n = 200_000
        board = gen.integers(len(ranks), size=n)
        others = [gen.integers(len(COMBOS), size=n) for _ in range(players - 1)]
        hands = np.column_stack([gen.choice(mine, size=n), *others])
        rank = ranks[board[:, None], hands]
        ok = (rank >= 0).all(axis=1)  # no hole card on the board
        cards = np.sort(COMBOS[hands].reshape(n, -1), axis=1)
        ok &= (np.diff(cards, axis=1) != 0).all(axis=1)  # no card dealt twice
        rank, hands = rank[ok], hands[ok]
        winners = rank == rank.max(axis=1, keepdims=True)
        share = winners / winners.sum(axis=1, keepdims=True)
        cell = np.ravel_multi_index(
            tuple(labels[COMBO_CLASS[hands[:, s]]] for s in range(1, players)), shape
        )
        for s in range(players):
            shares[:, s] += np.bincount(cell, weights=share[:, s], minlength=cells)
        counts += np.bincount(cell, minlength=cells)
        done += len(rank)
    return shares, counts


# Deals' worth of weight given to a class's average share in every cell, so that cells
# with few deals (opponent mixes that card removal makes rare, like three big pairs against
# aces) lean on the class's overall result instead of a handful of samples.
PRIOR_DEALS = 30


def pool(shares: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """
    Mean shares from summed (169, b, ..., seat) shares and (169, b, ...) counts, pooling
    cells that differ only in the order of opponents, whose seats are interchangeable.
    """
    players = shares.shape[-1]
    total, n = np.zeros_like(shares), np.zeros_like(counts)
    for perm in permutations(range(1, players)):
        axes = (0, *perm)
        total += shares.transpose(*axes, players)[..., list(axes)]
        n += counts.transpose(*axes)
    cells = tuple(range(1, players))
    mine = total[..., 0].sum(axis=cells) / n.sum(axis=cells)
    prior = np.stack([mine, *[(1 - mine) / (players - 1)] * (players - 1)], axis=-1)
    prior = prior.reshape(len(mine), *(1,) * (players - 1), players)
    return (total + PRIOR_DEALS * prior) / (n + PRIOR_DEALS)[..., None]


def link(counts: np.ndarray) -> np.ndarray:
    """
    How often each mix of opponent buckets is dealt relative to independence, given our
    class: (169, b, ...) observed deals over the deals expected if opponents' hands were
    independent, pooled over opponent order and pulled toward 1 where deals are few.
    """
    opponents = counts.ndim - 1
    n = sum(counts.transpose(0, *perm) for perm in permutations(range(1, opponents + 1)))
    total = n.reshape(len(n), -1).sum(axis=1)
    marginal = n.sum(axis=tuple(range(2, opponents + 1))) / total[:, None]  # (169, b)
    expected = total.reshape(-1, *[1] * opponents)
    for axis in range(1, opponents + 1):
        shape = [len(n)] + [1] * opponents
        shape[axis] = -1
        expected = expected * marginal.reshape(shape)
    return (n + PRIOR_DEALS) / (expected + PRIOR_DEALS)


@dataclass(frozen=True)
class MultiwayTables:
    labels: dict[int, np.ndarray]  # players -> bucket of each class
    share: dict[int, np.ndarray]  # players -> (169, b, ..., seat) mean all-in pot share
    link: dict[int, np.ndarray]  # players -> (169, b, ...) dealt vs independent
    provenance: str


@cache
def load() -> MultiwayTables:
    labels, share, linked, provenance = {}, {}, {}, []
    for players in BUCKETS:
        with np.load(DATA / f"multiway{players}.npz") as data:
            labels[players] = data["labels"]
            stored = data["share"].astype(np.float64)
            # Stored at half precision; renormalize so every pot is shared out exactly.
            share[players] = stored / stored.sum(axis=-1, keepdims=True)
            linked[players] = data["link"].astype(np.float64)
            provenance.append(str(data["provenance"]))
    return MultiwayTables(labels, share, linked, "; ".join(provenance))
