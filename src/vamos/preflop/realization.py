"""
How much of its equity a hand keeps when the pot goes to a flop without an all-in.

Raw equity overvalues hands that rarely get to a showdown: out of position, dominated or
unconnected, they are bluffed off or pay off better hands (valued at raw equity, the big
blind defends 97% against a button open; solvers defend about 35-40%). Each class gets a
weight from four features, w = exp(theta . features), and every player but the last to
act after the flop has it scaled by `OUT_OF_POSITION`. The pot is split in proportion to
equity times weight,

    share = e * w_mine / (e * w_mine + sum over opponents of e_opp * w_opp),

so heads-up the shares of a pot always sum to one (in 3-4 way pots, where opponents are
bucketed, to within a few percent; see docs/preflop.md). As the stack-to-pot ratio falls the split
blends back to raw equity, reaching it at an all-in: blend = 1 - exp(-spr / SPR_SCALE).

The weights are tuned on GTO Wizard's heads-up 50bb solution with a 2.1bb small-blind
open (scripts/fit_realization.py): the big blind's pot shares after calling, and VAMOS's
own solve of the same game compared hand by hand with GTO Wizard's strategies. Checked on
its 3.5bb open, which was not used for fitting or choosing but was looked at while the
procedure was developed: the small blind folds / limps / raises 1.8 / 58.4 / 39.8% (GTO
Wizard 0.2 / 67.6 / 32.2) and the big blind folds / calls / 3-bets 46.7 / 37.0 / 16.3%
(51.4 / 28.3 / 20.3), against 27.4 / 56.1 / 16.5% at raw equity. The player in position,
and pots with three or four players, use the same model with nothing to check it against:
approximate.
"""

from dataclasses import dataclass, field
from functools import cache

import numpy as np

from vamos.cards import RANKS
from vamos.preflop import matchups, multiway
from vamos.preflop.matchups import CLASSES
from vamos.preflop.tree import POSITIONS, POSTFLOP_ORDER, Terminal

FEATURES = ("pair", "suited", "connected", "high", "low")
# Fitted 2026-10-02 by scripts/fit_realization.py: the lowest tuning loss of four variants
# (with or without the low card, with or without the cap) from four fixed starts each.
THETA = np.array([1.0706, 0.642, 0.7371, 0.5682, 0.0])
OUT_OF_POSITION = 0.7741
# Largest tilt, in log-weight, any hand type may give a matchup; None is no limit. Offered
# to the fit as a variant, which did not choose it.
CAP: float | None = None
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

    @property
    def weights(self) -> np.ndarray:
        return self.w

    def multiway(
        self, terminal: Terminal, player: int, opponents: list[int], bucket_weights: list
    ) -> np.ndarray:
        # share = e0 w0 / (e0 w0 + sum e_k w_k) = 1 / (1 + sum (e_k / e0) (w_k / w0)), with
        # the equity ratios computed once per player count.
        players = len(opponents) + 1
        factors = [self._factor(terminal, s) for s in (player, *opponents)]
        mine = self.w * factors[0]
        others = 1.0
        for k, (ratio, w) in enumerate(zip(_equity_ratios(players), bucket_weights, strict=True)):
            shape = [len(mine)] + [1] * (players - 1)
            shape[1 + k] = -1
            others = others + ratio * (w * factors[1 + k] / mine[:, None]).reshape(shape)
        raw = multiway.load().share[players][..., 0]
        return raw + blend(self.spr(terminal)) * (1 / others - raw)


@cache
def _equity_ratios(players: int) -> list[np.ndarray]:
    """Each opponent seat's all-in share over ours, per cell (infinite where ours is 0)."""
    share = multiway.load().share[players]
    with np.errstate(divide="ignore"):
        return [share[..., k] / share[..., 0] for k in range(1, players)]
