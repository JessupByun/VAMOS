# Preflop baseline

How VAMOS's home-built preflop solver works, what it approximates, and how each part was checked.
Only its heads-up push/fold tables are still used at the table (see At the table).

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
| Card removal with 3-4 players | Folded players carry no card removal (our blockers do not change how often others fold); every seat then sees the same odds for a fold-out or a two-way pot, so chips balance exactly there | Against exact three-player card removal at a solved 3-handed button open, the blinds both fold 37.5% of the time for every hand instead of 41.5% behind AA and 35.5% behind 22: the steal's value is off by 0.016bb a hand on average, 0.06bb at worst (AA) |
| 3-4 way all-in equity | Opponents grouped into 40 buckets three-way (6 of pairs, 17 suited, 17 offsuit) or 16 four-way (3, 6, 7) of similar hands; within a bucket hands count by combos, not by the opponent's range; each mix of buckets is weighted by how often it is really dealt (`preflop/multiway.py`) | On solved 3- and 4-way spots our equity comes out 0.7 points high on average, 1.7 at worst (mixed buckets were 2.0 and 3.8); on test ranges, including ones that split buckets, 0.5 points off on average, 1.9 at worst |
| Pots that see a flop | Realization model: equity discounted by hand type, position and stack-to-pot ratio (`preflop/realization.py`) | See below |

## Realization model

- Each class gets a weight from four features (pair, suited, connectedness, high card).
- Every player but the last to act after the flop has the weight scaled down (0.77).
- The pot is split in proportion to equity times weight, so heads-up shares always sum to one, and the split blends back to raw equity as the stack-to-pot ratio falls (`1 - exp(-spr / 3)`, assumed, not fitted).
- In 3-4 way pots each seat sees its own exact hand against the others' buckets, each bucket weighted by the hands that opponent actually holds there; the seats' shares then sum to about 0.96-0.97 of a limped pot with random hands, and chips balance to +0.0055bb a hand at a solved 3-handed strategy (weighting buckets as dealt instead created +0.04bb three-handed and +0.11bb four-handed).
- Tuned on GTO Wizard's heads-up 50bb solution with a 2.1bb small-blind open (0.12bb ante, AI solver): the big blind's pot shares after calling, and VAMOS's own solve of the same game compared hand by hand.
  Four variants (with or without a low-card feature and a cap on how far a hand type can tilt a matchup) are fitted from four fixed starts each, and the lowest tuning loss wins.
- On the tuning spot the big blind folds / calls / 3-bets 18.0 / 61.2 / 20.9% against GTO Wizard's 16.9 / 61.2 / 21.9.
- Checked on the same article's 3.5bb open, which was not used for fitting or choosing (but was looked at while the procedure was developed, so it is a check rather than an untouched holdout):

| | Raw equity | Model | GTO Wizard |
|---|---|---|---|
| Small blind first in (fold / limp / raise) | 8.6 / 56.0 / 35.4 | 1.8 / 58.4 / 39.8 | 0.2 / 67.6 / 32.2 |
| Big blind vs 3.5bb (fold / call / 3-bet) | 27.4 / 56.1 / 16.5 | 46.7 / 37.0 / 16.3 | 51.4 / 28.3 / 20.3 |
| Same main action as GTO Wizard (small blind / big blind) | 61% / 65% | 72% / 86% | |
| Big blind's pot share after calling, mean error | 6.7% of the pot | 4.45% | |

- Known misses: on the 3.5bb spot the small blind raises about 8 points more and limps 9 points less than GTO Wizard, and the big blind calls 9 points more.
- The reference shares the solver, ante and article with the tuning spot, so it cannot catch a bias common to both.
- Only the big blind's out-of-position calls have a reference; in-position play and 3-4 way pots use the same model unchecked.
- The chart numbers are private test data (`tests/corpus/gtowizard/`, gitignored; GTO Wizard's content is not for redistribution).

## At the table

- Retired on 2026-10-05: an audit against real solver output (published 6-max charts, GTO Wizard heads-up 50bb, HoldemResources push/fold) found only heads-up push/fold reliable.
  Deeper heads-up play, blind decisions, 3-bets and 4-bets were too far off to show as advice, so the full trees and the 3-4 player push/fold tables were deleted (they remain in git history).
- What remains at the hero's preflop turn (`engine/advice.py`):
  - heads-up at 15bb or less effective: the push/fold chart (`pushfold-2p-*`, blended between depths, one action drawn per decision);
  - otherwise the rules in `engine/preflop_rules.py`: trash folds (or checks when free), the strongest hands raise at a standard size;
  - everything else, and every 3-bet or more, goes to Claude (`Claude` on the card), or shows only the facts when the AI switch is off.
- The trash list was frozen from the retired 3-handed solves: hands folding 99% or more both opening on the button and in the big blind facing a 2.5bb button open, at every depth from 35 to 100bb (47 hands, 41% of deals).
  Those solves played both spots looser than published charts, so their folds are folds there too; near break-even big blind defends without rake are the known exception.
  The rule is not used heads-up or blind against blind.

## Checks

- Heads-up push/fold matches HoldemResources' published Nash solution at 15 depths from 2 to 25bb: every hand played differently is one both treat as a near coin flip (within 0.001bb), and the check fails if card removal is dropped or equities shift half a point (`tests/test_preflop_solver.py`, `uv run python scripts/fetch_hrc_pushfold.py` first).
- Multiway tables conserve the pot (average share 1/n within 0.1 points) and match exact equity within the bounds above (`tests/test_preflop_multiway.py`).
- The realization model keeps pot shares summing to one, returns raw equity at an all-in, and favors the player acting last (`tests/test_preflop_realization.py`).

## Regenerating

```bash
uv run python scripts/gen_matchups.py      # about 1 minute
uv run python scripts/gen_multiway.py      # about 4 minutes
uv run python scripts/fit_realization.py   # about an hour; needs the private chart data
```
