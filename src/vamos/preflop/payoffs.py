"""
Terminal values for the preflop solver, as each player's counterfactual value per class.

Card removal is exact between two players: for our class i, an opponent's class j comes
with probability counts[i, j] / sum(counts[i]) (`Matchups.given`). With three or four
players dealt, players who folded carry no card removal (named approximation: our blockers
do not change how often the others fold), because weighting every player by each seat's
own cards gives each seat different odds for the same deal and the chips stop summing to
zero (+0.023bb a hand three-handed, against +0.0075bb this way, measured 2026-10-02).
Pots with three or four players still in use the bucketed tables in `multiway`.

Pots that see a flop are valued by a flop model; without one they are valued at raw
equity, as if checked down, which is known to overvalue weak hands (kept for tests and
comparison only).
"""

from typing import Protocol

import numpy as np

from vamos.preflop import matchups, multiway
from vamos.preflop.tree import Terminal

PRIOR = matchups.COMBO_COUNTS / matchups.COMBO_COUNTS.sum()


class FlopModel(Protocol):
    def heads_up(self, terminal: Terminal, player: int, rival: int) -> np.ndarray:
        """(169, 169) share of the pot for `player`'s class against `rival`'s class."""
        ...

    def multiway(self, terminal: Terminal, player: int, opponents: list[int]) -> np.ndarray:
        """(169, b, ...) share of the pot for `player` against opponents' buckets."""
        ...


class ChipPayoffs:
    def __init__(
        self,
        tables: matchups.Matchups | None = None,
        joint: multiway.MultiwayTables | None = None,
        flop: FlopModel | None = None,
    ):
        m = tables or matchups.load()
        self.given = m.given
        self.equity = m.equity
        self.showdown = m.given * m.equity  # P(j | i) times i's equity against j
        self.joint = joint
        self.flop = flop

    def reach(self, reach: np.ndarray) -> np.ndarray:
        """How likely an opponent with this reach is still in, for each class of ours."""
        return self.given @ reach

    def __call__(self, terminal: Terminal, player: int, reach: list[np.ndarray]) -> np.ndarray:
        spent = terminal.committed[player]
        players = len(reach)
        if players == 2:
            # Heads-up everything is exact, including how often the other player folds.
            (other,) = [q for q in range(2) if q != player]
            if terminal.active == (player, other) or terminal.active == (other, player):
                return self._heads_up(terminal, player, other, reach[other])
            won = terminal.pot if terminal.active == (player,) else 0.0
            return (won - spent) * self.reach(reach[other])
        folded = [q for q in range(players) if q != player and q not in terminal.active]
        dropped = float(np.prod([PRIOR @ reach[q] for q in folded]))
        if player not in terminal.active:
            # Our cards are gone; only how often the hand gets here matters.
            return np.full(len(PRIOR), -spent * dropped * self._together(terminal.active, reach))
        opponents = [q for q in terminal.active if q != player]
        if not opponents:
            return np.full(len(PRIOR), (terminal.pot - spent) * dropped)
        if len(opponents) == 1:
            value = self._heads_up(terminal, player, opponents[0], reach[opponents[0]])
        else:
            won, still_in = self._multiway(terminal, player, opponents, reach)
            value = terminal.pot * won - spent * still_in
        return value * dropped

    def _together(self, active: tuple[int, ...], reach: list[np.ndarray]) -> float:
        """How often the players in `active` all hold hands that reach here, jointly."""
        first, *rest = active
        if not rest:
            return float(PRIOR @ reach[first])
        if len(rest) == 1:
            together = self.reach(reach[rest[0]])
        else:
            together = self._multiway(None, first, rest, reach)[1]
        return float(PRIOR @ (reach[first] * together))

    def _heads_up(self, terminal: Terminal, player: int, rival: int, reach: np.ndarray):
        if terminal.kind == "showdown" or self.flop is None:
            weighted = self.showdown
        else:
            weighted = self.given * self.flop.heads_up(terminal, player, rival)
        spent = terminal.committed[player]
        return terminal.pot * (weighted @ reach) - spent * self.reach(reach)

    def _multiway(
        self,
        terminal: Terminal | None,
        player: int,
        opponents: list[int],
        reach: list[np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray]:
        """Our expected pot share and how often the opponents are all in, per class."""
        joint = self.joint or multiway.load()
        players = len(opponents) + 1
        labels = joint.labels[players]
        onehot = np.eye(labels.max() + 1)[labels]  # (169, buckets)
        # For each class of ours, how likely each opponent holds a hand from each bucket.
        weights = [(self.given * reach[q]) @ onehot for q in opponents]
        link = joint.link[players]
        if terminal is None or terminal.kind == "showdown" or self.flop is None:
            share = joint.share[players][..., 0]
        else:
            share = self.flop.multiway(terminal, player, opponents)
        spec = "ib,ic,ibc->i" if players == 3 else "ib,ic,id,ibcd->i"
        return np.einsum(spec, *weights, link * share), np.einsum(spec, *weights, link)
