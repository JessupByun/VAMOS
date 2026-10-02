"""
Discounted CFR over the 169 hand classes (Brown & Sandholm 2019, alpha 1.5, beta 0,
gamma 2), with alternating updates and every quantity a 169-vector per action.

Payoffs come from a `Payoffs` object, which turns a terminal into each player's
counterfactual value given everyone's reach; it owns card removal and the model of what
happens after the flop. For two players the result is an equilibrium of the model game and
its exploitability is exact; for more players CFR has no such guarantee, so solutions
report NashConv (the sum of every player's best-response gain) and are checked for
stability across runs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from vamos.preflop.matchups import COMBO_COUNTS
from vamos.preflop.tree import Decision, Node, Terminal, Tree

ALPHA, BETA, GAMMA = 1.5, 0.0, 2.0
# A hand reaching a decision less often than this (weighted over iterations) is answered
# from the current strategy rather than the average.
RARE = 1e-3
PRIOR = COMBO_COUNTS / COMBO_COUNTS.sum()  # how often each class is dealt


class Payoffs(Protocol):
    def __call__(self, terminal: Terminal, player: int, reach: list[np.ndarray]) -> np.ndarray:
        """Counterfactual value per class of `player` at `terminal`, in big blinds."""
        ...


@dataclass
class Solution:
    tree: Tree
    strategy: dict[int, np.ndarray]  # decision id -> (actions, 169) average strategy
    ev: np.ndarray  # per player, big blinds per hand
    nash_conv: float  # sum over players of best-response gain, big blinds per hand
    iterations: int

    def at(self, path: tuple[str, ...]) -> tuple[Decision, np.ndarray]:
        node = _walk(self.tree, path)
        return node, self.strategy[node.id]


def _step(tree: Tree, node: Node, step: str) -> int:
    """The index of `step` ("position:action") at `node`; ValueError if it does not fit."""
    position, action = step.split(":", 1)
    if not isinstance(node, Decision) or tree.positions[node.player] != position:
        raise ValueError(f"{step!r} is not a decision at this point")
    return node.actions.index(action)


def _walk(tree: Tree, path: tuple[str, ...]) -> Decision:
    node = tree.root
    for step in path:
        node = node.children[_step(tree, node, step)]
    if not isinstance(node, Decision):
        raise ValueError(f"{path!r} does not end at a decision")
    return node


def _with(reach: list[np.ndarray], player: int, value: np.ndarray) -> list[np.ndarray]:
    out = list(reach)
    out[player] = value
    return out


def _matching(regret: np.ndarray) -> np.ndarray:
    positive = np.maximum(regret, 0)
    total = positive.sum(axis=0)
    uniform = np.full_like(regret, 1 / len(regret))
    return np.where(total > 0, positive / np.where(total > 0, total, 1), uniform)


class Solver:
    def __init__(self, tree: Tree, payoffs: Payoffs):
        self.tree = tree
        self.payoffs = payoffs
        shape = {d.id: (len(d.actions), len(PRIOR)) for d in tree.decisions}
        self.regret = {i: np.zeros(s) for i, s in shape.items()}
        self.total = {i: np.zeros(s) for i, s in shape.items()}
        self.iterations = 0

    def _update(self, node: Node, player: int, reach: list[np.ndarray], weight: float):
        if isinstance(node, Terminal):
            return self.payoffs(node, player, reach)
        sigma = _matching(self.regret[node.id])
        mover = node.player
        if mover == player:
            # Our own reach is carried down only for the average strategy, which is weighted
            # by how often we reach each decision; payoffs never read it.
            values = np.stack(
                [
                    self._update(c, player, _with(reach, player, reach[player] * s), weight)
                    for c, s in zip(node.children, sigma, strict=True)
                ]
            )
            value = (sigma * values).sum(axis=0)
            self.regret[node.id] += values - value
            self.total[node.id] += weight * reach[player] * sigma
            return value
        value = np.zeros(len(PRIOR))
        for a, child in enumerate(node.children):
            if not sigma[a].any():
                continue
            nxt = list(reach)
            nxt[mover] = reach[mover] * sigma[a]
            value += self._update(child, player, nxt, weight)
        return value

    def _discount(self, player: int, t: int) -> None:
        up, down = t**ALPHA / (t**ALPHA + 1), t**BETA / (t**BETA + 1)
        for d in self.tree.decisions:
            if d.player == player:
                r = self.regret[d.id]
                r *= np.where(r > 0, up, down)

    def run(self, iterations: int, report: Callable[[int], None] | None = None) -> None:
        ones = [np.ones(len(PRIOR)) for _ in range(self.tree.players)]
        for _ in range(iterations):
            self.iterations += 1
            t = self.iterations
            for player in range(self.tree.players):
                self._update(self.tree.root, player, ones, float(t) ** GAMMA)
                self._discount(player, t)
            if report:
                report(t)

    def average(self) -> dict[int, np.ndarray]:
        """
        The average strategy, except for hands that almost never reach a decision: their
        average is mostly early, near-uniform iterates (72o facing a 3-bet after opening
        came out a third each fold / call / all-in), so they get the current regret-matched
        strategy, which keeps learning there because regrets do not depend on own reach.
        A loose player reaches such spots, so they need a sensible answer.
        """
        most = sum(float(t) ** GAMMA for t in range(1, self.iterations + 1))
        out = {}
        for i, total in self.total.items():
            s = total.sum(axis=0)
            reached = s > RARE * most
            average = total / np.where(reached, s, 1)
            out[i] = np.where(reached, average, _matching(self.regret[i]))
        return out

    def solution(self) -> Solution:
        strategy = self.average()
        ev, best = evaluate(self.tree, self.payoffs, strategy)
        return Solution(self.tree, strategy, ev, float((best - ev).sum()), self.iterations)


def _value(
    node: Node,
    player: int,
    reach: list[np.ndarray],
    payoffs: Payoffs,
    strategy: dict[int, np.ndarray],
    best: bool,
) -> np.ndarray:
    if isinstance(node, Terminal):
        return payoffs(node, player, reach)
    sigma = strategy[node.id]
    if node.player == player:
        values = np.stack(
            [_value(c, player, reach, payoffs, strategy, best) for c in node.children]
        )
        return values.max(axis=0) if best else (sigma * values).sum(axis=0)
    value = np.zeros(len(PRIOR))
    for a, child in enumerate(node.children):
        if not sigma[a].any():
            continue
        nxt = list(reach)
        nxt[node.player] = reach[node.player] * sigma[a]
        value += _value(child, player, nxt, payoffs, strategy, best)
    return value


def evaluate(
    tree: Tree, payoffs: Payoffs, strategy: dict[int, np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    """Each player's EV under `strategy`, and their best-response EV against the others."""
    ones = [np.ones(len(PRIOR)) for _ in range(tree.players)]
    ev, best = np.zeros(tree.players), np.zeros(tree.players)
    for p in range(tree.players):
        ev[p] = PRIOR @ _value(tree.root, p, ones, payoffs, strategy, best=False)
        best[p] = PRIOR @ _value(tree.root, p, ones, payoffs, strategy, best=True)
    return ev, best


def action_values(
    tree: Tree, payoffs: Payoffs, strategy: dict[int, np.ndarray], path: tuple[str, ...]
) -> np.ndarray:
    """Counterfactual value of each action at the decision reached by `path`, per class."""
    reach = [np.ones(len(PRIOR)) for _ in range(tree.players)]
    node = tree.root
    for step in path:
        a = _step(tree, node, step)
        assert isinstance(node, Decision)
        reach[node.player] = reach[node.player] * strategy[node.id][a]
        node = node.children[a]
    if not isinstance(node, Decision):
        raise ValueError(f"{path!r} does not end at a decision")
    return np.stack(
        [_value(c, node.player, reach, payoffs, strategy, best=False) for c in node.children]
    )
