"""
The preflop baseline at a live decision: the solved-tree spot that matches what happened,
the hero's mix there, and one action sampled from it.

Live play is mapped onto the solved trees:
- positions come from the seats (heads-up the button is the small blind);
- a raise goes to the nearest solved size, compared as a ratio (a 4bb open counts as the
  3bb open), and an all-in counts as the tree's all-in; a raise more than twice or under
  half the nearest size (a 50bb open shove where only 2.5 and 3bb opens were solved) is
  off the tree;
- the effective stack picks the two nearest solved depths and the mix is blended between
  them by distance (in log of the stack).
Anything the trees do not cover (five or more players, a straddle, a cold call of a 3-bet),
and hands the solve learned nothing for at a spot (nobody else ever reaches it), get no
advice rather than a guess.
"""

import math
import random
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from vamos.cards import hand_class
from vamos.engine.state import VOLUNTARY, positions
from vamos.log.hands import Hand, Street
from vamos.preflop.matchups import INDEX
from vamos.preflop.solver import Solution
from vamos.preflop.tree import POSITIONS, Decision

MAX_STRETCH = math.log(2)  # how far a live raise may be from the solved size it stands for


@dataclass(frozen=True)
class Spot:
    position: str
    actions: tuple[str, ...]
    strategy: np.ndarray  # (actions, 169)
    # (169,) True for hands the solve learned nothing for here (see solver.settle).
    unlearned: np.ndarray = field(default_factory=lambda: np.zeros(169, dtype=bool))


# A hand that reaches a spot less often than this by its own choices gets no advice there:
# the solve trained it too little (aces could fold facing an all-in after limping).
RARE_FOR_ADVICE = 1e-3


@dataclass(frozen=True)
class Table:
    players: int
    stack: float
    spots: dict[tuple[str, ...], Spot]  # keyed by the path of "position:action" steps
    provenance: str = ""

    @cached_property
    def untrained(self) -> dict[tuple[str, ...], np.ndarray]:
        """
        Per spot, the hands with no trustworthy answer: unlearned, still on the solver's
        starting point (an exact even split), or reaching the spot less often than
        RARE_FOR_ADVICE by their own choices.
        """
        out = {}

        def walk(path, reach):
            spot = self.spots.get(path)
            if spot is None:
                return
            even = np.abs(spot.strategy - 1 / len(spot.actions)).max(axis=0) < 1.5e-3
            out[path] = spot.unlearned | even | (reach[spot.position] < RARE_FOR_ADVICE)
            for a, action in enumerate(spot.actions):
                nxt = dict(reach)
                nxt[spot.position] = reach[spot.position] * spot.strategy[a]
                walk((*path, f"{spot.position}:{action}"), nxt)

        walk((), {p: np.ones(169) for p in POSITIONS[self.players]})
        return out


def table_from(solution: Solution, provenance: str = "") -> Table:
    tree = solution.tree
    spots = {}

    def walk(node, path):
        if not isinstance(node, Decision):
            return
        position = tree.positions[node.player]
        spots[path] = Spot(
            position, node.actions, solution.strategy[node.id], solution.unlearned[node.id]
        )
        for action, child in zip(node.actions, node.children, strict=True):
            walk(child, (*path, f"{position}:{action}"))

    walk(tree.root, ())
    return Table(tree.players, tree.stack, spots, provenance)


@dataclass(frozen=True)
class Move:
    """One live preflop action, in big blinds."""

    position: str
    kind: str  # fold, check, call, bet or raise
    to: float | None  # the street total for bets and raises
    all_in: bool


def size_of(action: str, stack: float) -> float:
    return stack if action == "jam" else float(action.split()[1])


def translate(table: Table, moves: list[Move]) -> tuple[str, ...] | None:
    """The tree path for the live moves, or None where the tree has no matching action."""
    path: tuple[str, ...] = ()
    for move in moves:
        spot = table.spots.get(path)
        if spot is None or spot.position != move.position:
            return None
        action = _match(spot.actions, move, table.stack)
        if action is None:
            return None
        path = (*path, f"{move.position}:{action}")
    return path


def _match(actions: tuple[str, ...], move: Move, stack: float) -> str | None:
    if move.kind in ("fold", "check", "call"):
        return move.kind if move.kind in actions else None
    raises = [a for a in actions if a == "jam" or a.startswith("raise ")]
    if not raises or move.to is None:
        return None
    if move.all_in and "jam" in raises:
        return "jam"
    nearest = min(raises, key=lambda a: abs(math.log(size_of(a, stack) / move.to)))
    return nearest if abs(math.log(size_of(nearest, stack) / move.to)) <= MAX_STRETCH else None


@dataclass(frozen=True)
class Advice:
    mix: tuple[tuple[str, float], ...]  # (action, frequency), most frequent first
    action: str  # sampled from the mix
    depths: tuple[float, ...]  # the solved stack depths used
    # The bet the tree's spot faces, in big blinds (1 when unopened), so raise sizes can be
    # scaled to the live bet they stand in for.
    faced: float


def faced(path: tuple[str, ...], stack: float) -> float:
    for step in reversed(path):
        action = step.split(":", 1)[1]
        if action == "jam" or action.startswith("raise "):
            return size_of(action, stack)
    return 1.0


def live_moves(hand: Hand) -> list[Move] | None:
    """The hand's preflop actions as moves, or None if the tree cannot represent the hand."""
    labels = positions(hand)
    players = len(labels)
    if players not in POSITIONS or any(a.kind == "post_straddle" for a in hand.actions):
        return None
    if players == 2:
        labels = {pid: "SB" if label == "BTN" else label for pid, label in labels.items()}
    big_blind = hand.big_blind
    if not big_blind:
        return None
    moves = []
    for a in hand.actions:
        if a.street != Street.PREFLOP or a.kind not in VOLUNTARY:
            continue
        to = None if a.to is None else a.to / big_blind
        moves.append(Move(labels[a.player], a.kind, to, a.all_in))
    return moves


def _blend(depth: float, stacks: list[float]) -> list[tuple[float, float]]:
    """(stack, weight) of the solved depths around `depth`, nearest first."""
    if depth <= stacks[0]:
        return [(stacks[0], 1.0)]
    if depth >= stacks[-1]:
        return [(stacks[-1], 1.0)]
    hi = bisect_left(stacks, depth)
    lo_stack, hi_stack = stacks[hi - 1], stacks[hi]
    t = math.log(depth / lo_stack) / math.log(hi_stack / lo_stack)
    return [(lo_stack, 1 - t), (hi_stack, t)]


def advise(
    tables: list[Table],
    moves: list[Move],
    hero_position: str,
    cards: tuple[str, str],
    depth: float,
    seed: str,
) -> Advice | None:
    """
    The hero's mix after `moves` (all from `tables`, one player count), blended across the
    two solved depths nearest `depth`; None if no table covers the spot.
    """
    by_stack = {t.stack: t for t in tables}
    found = []
    price = 1.0
    for stack, weight in _blend(depth, sorted(by_stack)):
        table = by_stack[stack]
        path = translate(table, moves)
        spot = None if path is None else table.spots.get(path)
        hand = INDEX[hand_class(*cards)]
        if spot is None or spot.position != hero_position or table.untrained[path][hand]:
            continue
        if not found:
            price = faced(path, table.stack)
        found.append((stack, weight, spot.actions, spot.strategy[:, hand]))
    if not found:
        return None
    # Exactly on a solved depth whose table does not cover the spot, the neighbor that does
    # has zero weight in the blend; it is used alone then, as anywhere else between the two.
    total = sum(weight for _, weight, _, _ in found)
    mix: dict[str, float] = {}
    for _, weight, actions, column in found:
        share = weight / total if total > 0 else 1 / len(found)
        for action, freq in zip(actions, column, strict=True):
            mix[action] = mix.get(action, 0.0) + share * float(freq)
    used = [stack for stack, _, _, _ in found]
    ordered = tuple(sorted(mix.items(), key=lambda m: -m[1]))
    # Sampled once per decision, so the card does not flicker between refreshes.
    roll = random.Random(seed).random()
    action = ordered[-1][0]
    for candidate, freq in ordered:
        roll -= freq
        if roll < 0:
            action = candidate
            break
    return Advice(ordered, action, tuple(used), price)
