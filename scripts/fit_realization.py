"""
Fit the realization weights in vamos.preflop.realization to GTO Wizard's heads-up 50bb
solution with a 2.1bb small-blind open, then check them once on the held-back 3.5bb open.
Raw equity is reported alongside as the baseline to beat.

Data: tests/corpus/gtowizard/hu50.json (gitignored; private, non-commercial use only).
Run: uv run python scripts/fit_realization.py

Two kinds of reference, both from the 2.1bb spot only:
- Shares. GTO Wizard's EQR at the big blind's decision is the value of calling, net of the
  call, over equity times the pot (folding is 0). For a hand that always calls, the call
  closes the action, so its share of the flop pot is
      share = (EQR * equity * pot_now + call) / pot_on_flop,
  and a hand that mixes calling and folding is indifferent, so its share is call / pot.
  Equity is against the small blind's measured raising range, from VAMOS's own tables.
- Strategies. VAMOS's solver plays the same heads-up game (0.12bb ante each, the one open
  size, 3-bets to 4x, isolation raises to 6bb), and its hand-by-hand strategy at the small
  blind's first action and the big blind's answer to the open is compared with GTO
  Wizard's (measured from the article's charts), plus the big blind's raise rate over a
  limp. Fitting shares alone made both players fold far too much (the small blind 13.6%
  instead of 4.2%), so the solved strategies are part of the objective.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from vamos.preflop import matchups, realization
from vamos.preflop.matchups import COMBO_COUNTS, INDEX
from vamos.preflop.payoffs import ChipPayoffs
from vamos.preflop.realization import Realization
from vamos.preflop.solver import Solution, Solver
from vamos.preflop.tree import Menu, build

DATA = Path(__file__).resolve().parents[1] / "tests" / "corpus" / "gtowizard" / "hu50.json"
PURE = 0.97  # call frequency for a hand to count as always calling
TRAIN, TEST = "2.1", "3.5"
ITERATIONS = 600  # about 0.1 mbb/hand from equilibrium on this tree
STRATEGY_WEIGHT = 0.05  # per-hand strategy error against squared share error
LIMP_RAISE = 0.336  # the big blind's raise rate over a limp, 2.1bb spot
PRIOR = COMBO_COUNTS / COMBO_COUNTS.sum()


@dataclass
class Spot:
    name: str
    raw: dict
    hands: np.ndarray  # class indices with a share target
    target: np.ndarray  # flop pot share of the big blind
    kind: list[str]  # "call" or "even"
    weight: np.ndarray  # (169, 169) P(j | i) times the small blind's raise frequency
    spr: float


def load_spot(name: str, raw: dict) -> Spot:
    m = matchups.load()
    o, ante, stack = raw["open"], raw["ante_each"], raw["stack"]
    pot_now, call, pot_flop = o + 1 + 2 * ante, o - 1, 2 * o + 2 * ante
    spr = (stack - o - ante) / pot_flop
    raises = np.zeros(169)
    for hand, freq in raw["sb"].items():
        raises[INDEX[hand]] = freq["raise"]
    weight = m.given * raises[None, :]
    equity = (weight * m.equity).sum(axis=1) / weight.sum(axis=1)
    hands, target, kind = [], [], []
    for hand, freq in raw["bb"].items():
        i = INDEX[hand]
        if freq["call"] >= PURE:
            hands.append(i)
            target.append((raw["eqr"][hand] / 100 * equity[i] * pot_now + call) / pot_flop)
            kind.append("call")
        elif (
            0.05 <= freq["call"] <= 0.95
            and freq["fold"] >= 0.05
            and freq["raise"] + freq["jam"] <= 0.02
        ):
            hands.append(i)
            target.append(call / pot_flop)
            kind.append("even")
    return Spot(name, raw, np.array(hands), np.array(target), kind, weight, spr)


@dataclass(frozen=True)
class Variant:
    """Which features are fitted, and whether the tilt cap is."""

    name: str
    features: tuple[bool, ...]
    capped: bool

    def unpack(self, x: np.ndarray) -> tuple[np.ndarray, float, float | None]:
        k = sum(self.features)
        theta = np.zeros(len(self.features))
        theta[np.array(self.features)] = x[:k]
        cap = float(np.exp(x[k + 1])) if self.capped else None
        return theta, float(np.exp(x[k])), cap

    def start(self) -> np.ndarray:
        x = [*realization.THETA[np.array(self.features)], np.log(realization.OUT_OF_POSITION)]
        return np.array(x + ([np.log(2.0)] if self.capped else []))


FOUR = (True, True, True, True, False)
VARIANTS = (
    Variant("four features", FOUR, False),
    Variant("five features (low card)", (True,) * 5, False),
    Variant("four features, capped", FOUR, True),
    Variant("five features, capped", (True,) * 5, True),
)


def predict(
    spot: Spot, theta: np.ndarray, out_of_position: float, cap: float | None = None
) -> np.ndarray:
    m = matchups.load()
    w = realization.weights(theta, cap)
    share = realization.heads_up_share(m.equity, w * out_of_position, w, spot.spr)
    model = (spot.weight * share).sum(axis=1) / spot.weight.sum(axis=1)
    return model[spot.hands]


def raw_equity(spot: Spot) -> np.ndarray:
    m = matchups.load()
    return ((spot.weight * m.equity).sum(axis=1) / spot.weight.sum(axis=1))[spot.hands]


def solve(
    spot: Spot, theta: np.ndarray | None, out_of_position: float, cap: float | None = None
) -> Solution:
    o = spot.raw["open"]
    tree = build(2, spot.raw["stack"], Menu(opens=(o,), three_bets=(4.0,), iso=6.0), ante=0.12)
    flop = (
        None
        if theta is None
        else Realization(50, theta=theta, out_of_position=out_of_position, cap=cap)
    )
    solver = Solver(tree, ChipPayoffs(flop=flop))
    solver.run(ITERATIONS)
    return solver.solution()


def reference(freqs: dict, actions: tuple[str, ...]) -> np.ndarray:
    """GTO Wizard's per-hand frequencies in the order of VAMOS's actions."""
    out = np.zeros((len(actions), 169))
    for hand, f in freqs.items():
        for a, action in enumerate(actions):
            if action == "fold":
                out[a, INDEX[hand]] = f["fold"]
            elif action in ("call", "check"):
                out[a, INDEX[hand]] = f["call"]
            else:  # the open, the 3-bet and any all-in count as raising
                out[a, INDEX[hand]] = f["raise"] + f["jam"]
    return out / np.maximum(out.sum(axis=0, keepdims=True), 1e-9)


def nodes(spot: Spot, solution: Solution) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """(VAMOS, GTO Wizard) per-hand strategies at the two decisions both charts show."""
    o = f"{spot.raw['open']:g}"
    root, first = solution.at(())
    answer, second = solution.at((f"SB:raise {o}",))
    return {
        "small blind first in": (first, reference(spot.raw["sb"], root.actions)),
        f"big blind vs {o}bb": (second, reference(spot.raw["bb"], answer.actions)),
    }


def limp_raise(solution: Solution) -> float:
    """The big blind's raise rate over a limp, across all its hands."""
    _, answer = solution.at(("SB:call",))
    return float((PRIOR * answer[1]).sum())


def strategy_error(spot: Spot, solution: Solution) -> float:
    total = 0.0
    for ours, theirs in nodes(spot, solution).values():
        total += float((PRIOR * ((ours - theirs) ** 2).sum(axis=0)).sum())
    return total


def loss(spot: Spot, variant: Variant, x: np.ndarray) -> float:
    theta, oop, cap = variant.unpack(x)
    shares = ((predict(spot, theta, oop, cap) - spot.target) ** 2).mean()
    solution = solve(spot, theta, oop, cap)
    strategies = strategy_error(spot, solution) + (limp_raise(solution) - LIMP_RAISE) ** 2
    return float(shares + STRATEGY_WEIGHT * strategies)


def nelder_mead(f, x0: np.ndarray, step: float = 0.3, iterations: int = 400) -> np.ndarray:
    points = [x0] + [x0 + step * e for e in np.eye(len(x0))]
    values = [f(p) for p in points]
    for _ in range(iterations):
        order = np.argsort(values)
        points = [points[k] for k in order]
        values = [values[k] for k in order]
        centroid = np.mean(points[:-1], axis=0)
        reflected = centroid + (centroid - points[-1])
        fr = f(reflected)
        if fr < values[0]:
            expanded = centroid + 2 * (centroid - points[-1])
            fe = f(expanded)
            points[-1], values[-1] = (expanded, fe) if fe < fr else (reflected, fr)
        elif fr < values[-2]:
            points[-1], values[-1] = reflected, fr
        else:
            contracted = centroid + 0.5 * (points[-1] - centroid)
            fc = f(contracted)
            if fc < values[-1]:
                points[-1], values[-1] = contracted, fc
            else:
                points = [points[0] + 0.5 * (p - points[0]) for p in points]
                values = [f(p) for p in points]
        if max(values) - min(values) < 1e-7:
            break
    return points[int(np.argmin(values))]


def describe(
    spot: Spot, label: str, theta: np.ndarray | None, oop: float, cap: float | None = None
) -> None:
    solution = solve(spot, theta, oop, cap)
    shares = raw_equity(spot) if theta is None else predict(spot, theta, oop, cap)
    error = np.abs(shares - spot.target)
    print(f"  {label}: share error {error.mean():.4f} of the pot")
    for name, (ours, theirs) in nodes(spot, solution).items():
        reach = PRIOR
        mine = (ours * reach).sum(axis=1) / reach.sum()
        ref = (theirs * reach).sum(axis=1) / reach.sum()
        agree = (PRIOR * (ours.argmax(axis=0) == theirs.argmax(axis=0))).sum()
        off = (PRIOR * np.abs(ours - theirs).sum(axis=0) / 2).sum()
        print(
            f"    {name:<22} VAMOS {np.round(100 * mine, 1).tolist()}  "
            f"GTO Wizard {np.round(100 * ref, 1).tolist()}  "
            f"same main action {100 * agree:.0f}% of hands, frequencies off {100 * off:.1f}%"
        )


def main() -> None:
    raw = json.loads(DATA.read_text())["spots"]
    train, test = load_spot(TRAIN, raw[TRAIN]), load_spot(TEST, raw[TEST])
    # Variants are compared on the tuning spot only; the held-back spot is looked at once,
    # for the chosen one.
    fitted = []
    for variant in VARIANTS:
        best = nelder_mead(lambda x, v=variant: loss(train, v, x), variant.start())
        value = loss(train, variant, best)
        fitted.append((value, variant, best))
        theta, oop, cap = variant.unpack(best)
        print(f"{variant.name}: tuning loss {value:.5f}")
        describe(train, "  model", theta, oop, cap)
    value, variant, best = min(fitted, key=lambda f: f[0])
    theta, oop, cap = variant.unpack(best)
    print(f"\nchosen: {variant.name}")
    print(f"THETA = {np.round(theta, 4).tolist()}  OUT_OF_POSITION = {oop:.4f}  CAP = {cap}")
    for spot, role in ((train, "tuning"), (test, "held-back test")):
        print(f"{spot.name}bb open ({role}), actions fold / call / raise:")
        describe(spot, "raw equity", None, 1.0)
        describe(spot, "model", theta, oop, cap)


if __name__ == "__main__":
    main()
