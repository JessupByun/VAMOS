"""
The preflop game trees the solver works on: equal stacks, blinds of 0.5 and 1bb, no antes,
and a fixed menu of raise sizes. Players are numbered in preflop order (the first to act is
0, the big blind last); amounts are in big blinds.

With equal stacks an all-in can only be called all in, so there are no side pots: a hand
ends preflop when one player is left, when everyone left is all in, or when betting closes
and the rest is played after the flop.

Only the heads-up PUSH_FOLD game is solved now (its hands end in a fold or an all-in). The
STANDARD menu, with opens, 3-bets and limps, built the retired full-depth tables; its trees
reach a flop, which nothing values any more (payoffs.py).
"""

from dataclasses import dataclass, replace
from typing import Literal

POSITIONS = {2: ("SB", "BB"), 3: ("BTN", "SB", "BB"), 4: ("CO", "BTN", "SB", "BB")}
BLINDS = {"SB": 0.5, "BB": 1.0}


@dataclass(frozen=True)
class Menu:
    """The raise sizes each player may choose from, as raise-to amounts in big blinds."""

    opens: tuple[float, ...] = (2.5, 3.0)
    # Multiples of the raise faced; each caller in between adds one more raise to the size.
    three_bets: tuple[float, ...] = (3.0, 4.0)
    iso: float = 4.0  # over one limper; one more big blind per extra limper
    limps: bool = True
    # Calling a 3-bet without having put chips in voluntarily: off (agreed 2026-10-02),
    # solvers rarely do it short-handed.
    cold_calls: bool = False
    open_jam: bool = False


STANDARD = Menu()
PUSH_FOLD = Menu(opens=(), three_bets=(), limps=False, open_jam=True)


@dataclass
class Terminal:
    committed: tuple[float, ...]
    active: tuple[int, ...]  # players still in the hand
    kind: Literal["fold", "showdown", "flop"]

    @property
    def pot(self) -> float:
        return sum(self.committed)

    def spent(self, player: int) -> float:
        return self.committed[player]


@dataclass
class Decision:
    id: int
    player: int
    path: tuple[str, ...]  # "position:action" for every action so far
    committed: tuple[float, ...]
    actions: tuple[str, ...]
    children: list["Decision | Terminal"]


type Node = Decision | Terminal


@dataclass(frozen=True)
class _State:
    committed: tuple[float, ...]
    folded: frozenset[int] = frozenset()
    acted: frozenset[int] = frozenset()  # acted voluntarily since the last raise
    level: int = 0  # 0 unopened or limped, 1 raised, 2 three-bet, 3 all in
    limpers: int = 0
    callers: int = 0  # called the current raise
    last: int | None = None


def _amount(x: float) -> str:
    return f"{x:g}"


class _Builder:
    def __init__(self, players: int, stack: float, menu: Menu):
        self.n = players
        self.stack = stack
        self.menu = menu
        self.positions = POSITIONS[players]
        self.decisions: list[Decision] = []

    def price(self, s: _State) -> float:
        return max(s.committed)

    def owes(self, s: _State, p: int) -> bool:
        if p in s.folded or s.committed[p] >= self.stack:
            return False
        return p not in s.acted or s.committed[p] < self.price(s)

    def next_player(self, s: _State) -> int | None:
        start = 0 if s.last is None else s.last + 1
        for i in range(self.n):
            p = (start + i) % self.n
            if self.owes(s, p):
                return p
        return None

    def raise_sizes(self, s: _State, p: int) -> list[float]:
        others = [q for q in range(self.n) if q != p and q not in s.folded]
        if s.level >= 3 or all(s.committed[q] >= self.stack for q in others):
            return []
        menu, price = self.menu, self.price(s)
        if s.level == 0:
            sizes = list(menu.opens) if s.limpers == 0 else [menu.iso + s.limpers - 1]
            if menu.open_jam:
                sizes.append(self.stack)
        elif s.level == 1:
            sizes = [k * price + s.callers * price for k in menu.three_bets]
        else:
            sizes = [self.stack]
        # A size at or beyond the stack is an all-in; equal sizes collapse to one.
        return sorted({min(x, self.stack) for x in sizes if x > price})

    def cold(self, s: _State, p: int) -> bool:
        # Facing an all-in anyone may call; only a 3-bet cannot be called cold.
        blind = BLINDS.get(self.positions[p], 0.0)
        return s.level == 2 and s.committed[p] <= blind

    def actions(self, s: _State, p: int) -> list[tuple[str, _State]]:
        price = self.price(s)
        acted = s.acted | {p}
        out = []
        if s.committed[p] < price:
            out.append(("fold", replace(s, folded=s.folded | {p}, acted=acted, last=p)))
            limp = s.level == 0
            if (not limp or self.menu.limps) and (self.menu.cold_calls or not self.cold(s, p)):
                committed = list(s.committed)
                committed[p] = min(price, self.stack)
                after = replace(s, committed=tuple(committed), acted=acted, last=p)
                after = replace(after, limpers=s.limpers + 1) if limp else after
                after = after if limp else replace(after, callers=s.callers + 1)
                out.append(("call", after))
        else:
            out.append(("check", replace(s, acted=acted, last=p)))
        for size in self.raise_sizes(s, p):
            committed = list(s.committed)
            committed[p] = size
            jam = size >= self.stack
            label = "jam" if jam else f"raise {_amount(size)}"
            level = 3 if jam else s.level + 1
            after = replace(
                s, committed=tuple(committed), acted=frozenset({p}), level=level, callers=0, last=p
            )
            out.append((label, after))
        return out

    def build(self, s: _State, path: tuple[str, ...]) -> Node:
        active = tuple(p for p in range(self.n) if p not in s.folded)
        if len(active) == 1:
            return Terminal(s.committed, active, "fold")
        p = self.next_player(s)
        if p is None:
            all_in = any(s.committed[q] >= self.stack for q in active)
            return Terminal(s.committed, active, "showdown" if all_in else "flop")
        node = Decision(len(self.decisions), p, path, s.committed, (), [])
        self.decisions.append(node)
        choices = self.actions(s, p)
        node.actions = tuple(label for label, _ in choices)
        position = self.positions[p]
        node.children = [
            self.build(after, (*path, f"{position}:{label}")) for label, after in choices
        ]
        return node


@dataclass
class Tree:
    players: int
    stack: float
    menu: Menu
    root: Node
    decisions: list[Decision]

    @property
    def positions(self) -> tuple[str, ...]:
        return POSITIONS[self.players]


def build(players: int, stack: float, menu: Menu = STANDARD) -> Tree:
    builder = _Builder(players, stack, menu)
    committed = tuple(BLINDS.get(position, 0.0) for position in POSITIONS[players])
    root = builder.build(_State(committed), ())
    return Tree(players, stack, menu, root, builder.decisions)
