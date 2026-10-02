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


def _walk(tree: Tree, path: tuple[str, ...]) -> Decision:
    node = tree.root
    for step in path:
        assert isinstance(node, Decision)
        node = node.children[node.actions.index(step.split(":", 1)[1])]
    assert isinstance(node, Decision)
    return node


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
            values = np.stack([self._update(c, player, reach, weight) for c in node.children])
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
        out = {}
        for i, total in self.total.items():
            s = total.sum(axis=0)
            uniform = np.full_like(total, 1 / len(total))
            out[i] = np.where(s > 0, total / np.where(s > 0, s, 1), uniform)
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
        assert isinstance(node, Decision)
        a = node.actions.index(step.split(":", 1)[1])
        reach[node.player] = reach[node.player] * strategy[node.id][a]
        node = node.children[a]
    assert isinstance(node, Decision)
    return np.stack(
        [_value(c, node.player, reach, payoffs, strategy, best=False) for c in node.children]
    )
