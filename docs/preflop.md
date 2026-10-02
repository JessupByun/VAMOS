# Preflop baseline

How VAMOS's home-built preflop solver works, what it approximates, and how each part was checked.
It is a stand-in until HRC Pro tables replace it; every number it produces is labeled approximate.

## The game

- Equal stacks, blinds of 0.5 and 1bb, no antes, 2 to 4 players, amounts in big blinds (`preflop/tree.py`).
- Menu agreed with Jessup:
  - opens of 2.5 or 3bb;
  - 3-bets of 3x or 4x the raise, plus one raise size per caller in between (squeeze sizing);
  - every 4-bet is all in;
  - limps allowed, with isolation raises to 4bb plus 1bb per extra limper;
  - facing an all-in, call or fold;
  - no cold calls of a 3-bet: a player who has only posted a blind may fold or go all in, but anyone may call an all-in.
- A size at or beyond the stack becomes an all-in.
- Equal stacks mean no side pots, so a hand ends preflop with one player left, everyone all in, or a flop.

## Solver

- Discounted CFR over the 169 hand classes, alternating updates, vectorized per class (`preflop/solver.py`).
- Heads-up it converges to an equilibrium of the model game; exploitability is reported exactly.
- With 3 or 4 players CFR has no guarantee, so solutions report NashConv and per-hand strategies are expected to vary between near-equal options (for example 3-betting to 9 or to 12).

## Payoffs and their approximations

| Part | What is approximated | Measured effect |
|---|---|---|
| Heads-up card removal | Nothing: exact class-vs-class counts and equity (`preflop/matchups.py`, 400k Monte Carlo samples per pair) | Full-class averages agree with the stored representative within 0.4 points |
| Card removal with 3-4 players | Folded players carry no card removal (our blockers do not change how often others fold) | Chips sum to +0.009bb a hand three-handed, +0.016bb four-handed (exactly 0 heads-up) |
| 3-4 way all-in equity | Opponents grouped into 40 (3-way) or 16 (4-way) buckets of similar hands, weighted by how often each mix is dealt (`preflop/multiway.py`) | Against exact equity on realistic ranges: 0.44 points off on average, 1.5 at worst |
| Pots that see a flop | Realization model: equity discounted by hand type, position and stack-to-pot ratio (`preflop/realization.py`) | See below |

## Realization model

- Each class gets a weight from five features (pair, suited, connectedness, high card, low card), capped so no hand type can tilt a matchup too far.
- Every player but the last to act after the flop has the weight scaled down (0.72).
- The pot is split in proportion to equity times weight, so shares always sum to one, and the split blends back to raw equity as the stack-to-pot ratio falls (`1 - exp(-spr / 3)`, assumed, not fitted).
- Tuned on GTO Wizard's heads-up 50bb solution with a 2.1bb small-blind open (0.12bb ante, AI solver): the big blind's pot shares after calling, and VAMOS's own solve of the same game compared hand by hand.
- Checked once on the same article's 3.5bb open, held back:

| | Raw equity | Model | GTO Wizard |
|---|---|---|---|
| Small blind first in (fold / limp / raise) | 8.6 / 56.0 / 35.4 | 1.8 / 63.9 / 34.2 | 0.2 / 67.6 / 32.2 |
| Big blind vs 3.5bb (fold / call / 3-bet) | 27.4 / 56.1 / 16.5 | 54.6 / 31.2 / 14.2 | 51.4 / 28.3 / 20.3 |
| Same main action as GTO Wizard (small blind / big blind) | 61% / 65% | 74% / 84% | |
| Big blind's pot share after calling, mean error | 6.7% of the pot | 4.9% | |

- Known misses: against the 2.1bb open the big blind folds 26% instead of 17%, and it 3-bets 14% instead of 20% against the 3.5bb open.
- The reference shares the solver, ante and article with the tuning spot, so it cannot catch a bias common to both.
- Only the big blind's out-of-position calls have a reference; in-position play and 3-4 way pots use the same model unchecked.
- The chart numbers are private test data (`tests/corpus/gtowizard/`, gitignored; GTO Wizard's content is not for redistribution).

## Checks

- Heads-up push/fold matches HoldemResources' published Nash solution at 15 depths from 2 to 25bb: every hand played differently is one both treat as a near coin flip (within 0.001bb), and the check fails if card removal is dropped or equities shift half a point (`tests/test_preflop_solver.py`, `uv run python scripts/fetch_hrc_pushfold.py` first).
- Multiway tables conserve the pot (average share 1/n within 0.1 points) and match exact equity within the bounds above (`tests/test_preflop_multiway.py`).
- The realization model keeps pot shares summing to one, returns raw equity at an all-in, and favors the player acting last (`tests/test_preflop_realization.py`).

## Regenerating

```bash
uv run python scripts/gen_matchups.py      # about 1 minute
uv run python scripts/gen_multiway.py      # about 4 minutes
uv run python scripts/fit_realization.py   # about 10 minutes; needs the private chart data
```
