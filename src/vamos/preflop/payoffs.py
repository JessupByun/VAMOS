"""
Terminal values for the heads-up push/fold solver, as each player's counterfactual value
per class.

Card removal is exact: for our class i, the opponent's class j comes with probability
counts[i, j] / sum(counts[i]) (`Matchups.given`). A push/fold hand ends in a fold or an
all-in showdown. Pots that see a flop, and three or four players, have no value here: the
flop model and the multiway tables went with the retired full-depth tables (2026-10-05,
docs/preflop.md), so solving such a tree raises rather than silently using raw equity.
"""

import numpy as np

from vamos.preflop import matchups
from vamos.preflop.tree import Terminal


class ChipPayoffs:
    def __init__(self, tables: matchups.Matchups | None = None):
        m = tables or matchups.load()
        self.given = m.given
        self.equity = m.equity
        self.showdown = m.given * m.equity  # P(j | i) times i's equity against j

    def reach(self, reach: np.ndarray) -> np.ndarray:
        """How likely the opponent with this reach is still in, for each class of ours."""
        return self.given @ reach

    def __call__(self, terminal: Terminal, player: int, reach: list[np.ndarray]) -> np.ndarray:
        if len(reach) != 2 or terminal.kind == "flop":
            raise ValueError("only heads-up hands that end in a fold or an all-in are valued")
        other = 1 - player
        spent = terminal.spent(player)
        if len(terminal.active) == 2:
            return terminal.pot * (self.showdown @ reach[other]) - spent * self.reach(reach[other])
        won = terminal.pot if terminal.active == (player,) else 0.0
        return (won - spent) * self.reach(reach[other])
