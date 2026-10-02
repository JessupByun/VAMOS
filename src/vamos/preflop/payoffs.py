"""
Terminal values for the preflop solver.

Card removal is exact between two players: for our class i, an opponent's class j comes
with probability counts[i, j] / sum(counts[i]) (`Matchups.given`). With more players each
opponent is weighted by our cards alone, not by each other's (a named approximation: the
other players' hands are treated as independent given ours).
"""

import numpy as np

from vamos.preflop.matchups import Matchups, load
from vamos.preflop.tree import Terminal


class HeadsUpShowdown:
    """
    Fold and all-in payoffs, and pots that see a flop valued by `flop_share` (raw equity
    unless a realization model is given). Pots with three or more players are not handled.
    """

    def __init__(self, matchups: Matchups | None = None, flop_share=None):
        m = matchups or load()
        self.given = m.given
        self.showdown = m.given * m.equity  # P(j | i) times i's equity against j
        self.flop_share = flop_share

    def _reach(self, reach: np.ndarray) -> np.ndarray:
        return self.given @ reach

    def __call__(self, terminal: Terminal, player: int, reach: list[np.ndarray]) -> np.ndarray:
        others = [q for q in range(len(reach)) if q != player]
        spent = terminal.committed[player]
        if terminal.kind == "fold" or player not in terminal.active:
            won = terminal.pot if terminal.active == (player,) else 0.0
            value = np.full(len(self.given), won - spent)
            for q in others:
                value = value * self._reach(reach[q])
            return value
        if len(terminal.active) > 2:
            raise NotImplementedError("pots with three or more players")
        (rival,) = [q for q in terminal.active if q != player]
        if terminal.kind == "showdown" or self.flop_share is None:
            weighted = self.showdown
        else:
            weighted = self.given * self.flop_share(terminal, player, rival)
        value = terminal.pot * (weighted @ reach[rival]) - spent * self._reach(reach[rival])
        for q in others:
            if q != rival:
                value = value * self._reach(reach[q])
        return value
