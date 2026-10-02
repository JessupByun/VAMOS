"""
How much of its equity a hand keeps when the pot goes to a flop without an all-in.

Raw equity overvalues hands that rarely get to a showdown: out of position, dominated or
unconnected, they are bluffed off or pay off better hands (valued at raw equity, the big
blind defends 97% against a button open; solvers defend about 35-40%). Each class gets a
weight from four features, w = exp(theta . features), and every player but the last to
act after the flop has it scaled by `OUT_OF_POSITION`. The pot is split in proportion to
equity times weight,

    share = e * w_mine / (e * w_mine + sum over opponents of e_opp * w_opp),

so the shares of a pot always sum to one. As the stack-to-pot ratio falls the split
blends back to raw equity, reaching it at an all-in: blend = 1 - exp(-spr / SPR_SCALE).

The weights are tuned on GTO Wizard's heads-up 50bb solution with a 2.1bb small-blind
open and checked once on its 3.5bb open, held back (scripts/fit_realization.py): the
big blind's pot shares after calling, and VAMOS's own solve of the same game compared hand
by hand with GTO Wizard's strategies. On the held-back spot the small blind folds / limps /
raises 1.8 / 63.9 / 34.2% (GTO Wizard 0.2 / 67.6 / 32.2) and the big blind folds / calls /
3-bets 54.6 / 31.2 / 14.2% (51.4 / 28.3 / 20.3), against 27.4 / 56.1 / 16.5% at raw
equity. Known misses: against the small open the big blind folds 26% instead of 17%, and
a higher low card counts slightly against a hand (it overlaps with connectedness). The
player in position, and pots with three or four players, use the same model with nothing
to check it against: approximate.
"""

from dataclasses import dataclass, field

import numpy as np

from vamos.cards import RANKS
from vamos.preflop import matchups, multiway
from vamos.preflop.matchups import CLASSES
from vamos.preflop.tree import POSITIONS, POSTFLOP_ORDER, Terminal

FEATURES = ("pair", "suited", "connected", "high", "low")
# Fitted 2026-10-02 by scripts/fit_realization.py, the best of four variants on the tuning
# spot (with or without the low card, with or without the cap).
THETA = np.array([2.8142, 1.0593, 1.2693, 1.3583, -0.7426])
OUT_OF_POSITION = 0.7199
# Largest tilt, in log-weight, any hand type may give a matchup. Without one, pairs got so
# much weight that weak hands kept almost nothing against them and both players overfolded.
CAP: float | None = 2.8346
# Assumed, not fitted (one reference depth cannot pin down its shape): by a stack-to-pot
# ratio of 3 about two thirds of the effect is present, by 9 about 95%.
SPR_SCALE = 3.0


def features(hand_class: str) -> np.ndarray:
    high, low = RANKS.index(hand_class[0]), RANKS.index(hand_class[1])
    pair = high == low
    gap = high - low - 1
    return np.array(
        [
            float(pair),
            float(hand_class.endswith("s")),
            0.0 if pair else max(0, 4 - gap) / 4,  # connectors 1, one-gappers 0.75 ...
            high / 12,
            low / 12,
        ]
    )


FEATURE_TABLE = np.stack([features(c) for c in CLASSES])


def weights(theta: np.ndarray, cap: float | None = None) -> np.ndarray:
    """Each class's weight, measured from the average hand and limited to +-cap in log."""
    g = FEATURE_TABLE @ theta
    g = g - (matchups.COMBO_COUNTS * g).sum() / matchups.COMBO_COUNTS.sum()
    if cap is not None:
        g = cap * np.tanh(g / cap)
    return np.exp(g)


def blend(spr: float) -> float:
    return 1 - np.exp(-spr / SPR_SCALE)


def heads_up_share(
    equity: np.ndarray, w_mine: np.ndarray, w_theirs: np.ndarray, spr: float
) -> np.ndarray:
    """(169, 169) pot share of our class (rows) against theirs (columns)."""
    a = equity * w_mine[:, None]
    b = (1 - equity) * w_theirs[None, :]
    realized = a / (a + b)
    return equity + blend(spr) * (realized - equity)


@dataclass
class Realization:
    """The flop model for the preflop solver (see `payoffs.FlopModel`)."""

    stack: float
    theta: np.ndarray = field(default_factory=lambda: THETA.copy())
    out_of_position: float = OUT_OF_POSITION
    cap: float | None = CAP

    def __post_init__(self):
        self.w = weights(self.theta, self.cap)
        tables = matchups.load()
        self.equity = tables.equity
        self._cache: dict[tuple, np.ndarray] = {}

    def spr(self, terminal: Terminal) -> float:
        behind = self.stack - terminal.ante - max(terminal.committed)
        return behind / terminal.pot

    def _factor(self, terminal: Terminal, seat: int) -> float:
        """Every player but the last to act after the flop is out of position."""
        positions = POSITIONS[len(terminal.committed)]
        order = POSTFLOP_ORDER[len(terminal.committed)]
        last = max(terminal.active, key=lambda p: order.index(positions[p]))
        return 1.0 if seat == last else self.out_of_position

    def heads_up(self, terminal: Terminal, player: int, rival: int) -> np.ndarray:
        key = (
            "hu",
            self._factor(terminal, player),
            self._factor(terminal, rival),
            self.spr(terminal),
        )
        if key not in self._cache:
            self._cache[key] = heads_up_share(self.equity, self.w * key[1], self.w * key[2], key[3])
        return self._cache[key]

    def multiway(self, terminal: Terminal, player: int, opponents: list[int]) -> np.ndarray:
        factors = tuple(self._factor(terminal, s) for s in (player, *opponents))
        key = ("mw", factors, self.spr(terminal))
        if key not in self._cache:
            self._cache[key] = self._multiway(len(opponents) + 1, factors, key[2])
        return self._cache[key]

    def _multiway(self, players: int, factors: tuple[float, ...], spr: float) -> np.ndarray:
        tables = multiway.load()
        labels = tables.labels[players]
        share = tables.share[players]  # (169, b, ..., seat) mean all-in shares
        counts = matchups.COMBO_COUNTS
        # An opponent bucket's weight is the combo-weighted mean of its classes' weights.
        bucket_w = np.array(
            [
                (counts[labels == b] * self.w[labels == b]).sum() / counts[labels == b].sum()
                for b in range(labels.max() + 1)
            ]
        )
        seat_w = [self.w * factors[0]]
        for seat in range(1, players):
            seat_w.append(bucket_w * factors[seat])
        weighted = share.copy()
        for seat, w in enumerate(seat_w):
            shape = [1] * (players + 1)
            shape[seat] = -1
            weighted[..., seat] *= w.reshape(shape[:-1])
        realized = weighted[..., 0] / weighted.sum(axis=-1)
        raw = share[..., 0]
        return raw + blend(spr) * (realized - raw)
