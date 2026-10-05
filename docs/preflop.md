# Preflop

What answers each preflop decision, what each part approximates, and how it was checked.

## At the table

At the hero's preflop turn (`engine/advice.py`), in order:
- Heads-up at 10bb or less effective (the smaller start-of-hand stack): the push/fold chart, labeled `Preflop chart` on the card.
- Otherwise the rules in `engine/preflop_rules.py`, labeled `Preflop rule`: trash folds (or checks when free) and the strongest hands raise at a standard size.
- Everything else goes to Claude (`VAMOS! AI (Claude Opus 5.5 · low)` on the card), or shows only the facts when the AI switch is off.

## Rules

- Trash (47 hand types, 41% of deals) folds when all of these hold, and checks whenever checking is free:
  - 3 or more players were dealt in, and it is not a blind battle (folded to the blinds with no one else putting a chip in);
  - calling costs at least 27% of the pot after the call, the price the list was derived at (the big blind facing a 2.5bb button open);
  - the bet faced was not made all in (facing any all-in, whatever the price, Claude decides: the call turns on raw equity against the shover's range, which the profile informs).
- The open set (AA-TT, AKs, AQs, AJs, KQs, AKo, AQo) raises first in or over limpers, at any table size.
- The 3-bet set (AA-QQ, AK) 3-bets exactly one raise by someone else; facing a single all-in only AA and KK answer (call or re-raise), since a call with QQ or AK depends on the shover.
- Sizes: open 3bb (2.5bb heads-up); over limpers 4bb plus 1bb per extra limper; a 3-bet 3x the raise in position and 4x out of position, plus one raise per caller; never below PokerNow's minimum.
- A raise of 40% or more of what the others can still match goes all in instead (measured against the deepest opponent still in, not the hero's own stack).
- Claude decides facing a 3-bet or more (the hero's own raise counts), with a straddle or dead money posted, and in every spot above that no rule covers.
  Trash still folds facing a 3-bet, a straddle or dead money at a normal price.

Where the lists come from, and their limits (VAMOS's rule: nothing from a source whose terms forbid real-time assistance or reuse may set advice):
- The trash list was frozen from VAMOS's retired 3-handed solves: hands folding 99% or more both opening on the button and in the big blind facing a 2.5bb button open, at every depth from 35 to 100bb.
  Those solves' flop model was tuned on numbers read from a GTO Wizard article, so the list's exact boundary is not from an allowed source.
  Each hand has allowed backing of its own: 46 of the 47 are in Sklansky and Malmuth's group 9, the hands to fold (54o is group 8); facing a raise, our own equity tables decide most of them; and in the saved games only 15-17% of opens at 4 or more handed win the blinds uncontested, so stealing with trash rarely works in this pool.
- Without rake the big blind defends wider against small raises, which is why trash folds only at the source spot's price or steeper.
- The open set is exactly Sklansky and Malmuth's groups 1 and 2 plus AQo.
  The 3-bet set holds at least 63% equity against a top-15% opening range and 67% against a top-30% one (our own tables; QQ 68% and 71%).
- Not used heads-up or blind against blind, where real ranges are much wider: every hand has at least 32% equity against a random hand (32o, the lowest, 32.3%), while completing the small blind costs 25% of the pot and calling a 2.5bb open 30%.

Coverage on the saved games (`uv run python scripts/replay_preflop_routes.py`, 2026-10-05, 7 games, 350 hero decisions):

| Players dealt | Decisions | Claude | Claude with random cards | Claude calls per 100 hands |
|---|---|---|---|---|
| Heads-up | 80 | 89% | 91% | 82 |
| 3-handed | 24 | 46% | 72% | 50 |
| 4-handed | 126 | 64% | 66% | 65 |
| 5+ handed | 120 | 62% | 62% | 68 |
| All | 350 | 68% | 71% | |

The saved games are pruned after 3 days, so each run measures a different window; the script lists the games it read.

## Push/fold chart

- Nash for the heads-up game where the small blind may only jam or fold and the big blind may only call or fold, solved by discounted CFR over the 169 hand classes with exact card removal (`preflop/solver.py`, `preflop/payoffs.py`, `preflop/matchups.py`), one table per whole big blind from 2 to 10bb (`preflop/tables/`).
- It answers only the small blind's first action and the big blind facing a jam.
  A limp, a raise well under half the effective stack (a min-raise at 6bb or deeper) and every later decision go to the rules or Claude; at 5bb or less a 2bb min-raise is read as the jam.
- Approximations:
  - The full game also lets the small blind limp and min-raise; the chart stops at 10bb, the limit HoldemResources recommends for pure push/fold (until 2026-10-05 it ran to 15bb, where it folded 46-54% of small-blind hands that a full strategy would partly limp). Deeper, the rules or Claude decide.
  - It plays Nash and ignores the profiles: against a player who jams or calls far wider or tighter than Nash, a best response would gain more.
  - Between whole depths the mix is blended in log of the stack, so hands at the jam/fold threshold show mixes that are blending artifacts (for example 49% jam at 9.5bb for a hand HoldemResources always jams); the cost is under 2 mbb a hand from 5.5bb and about 8 at 2.5bb.
  - A raise that is not all in, to about half the effective stack or more, counts as the jam (the raiser is committed), so the big blind's answer is then CALL; exactly on a solved depth n the next table down is tried too, which lowers the cutoff to (n-1)/2bb (40% of the stack at 5bb).
  - Below the 2bb table, with nothing to call (a small blind who also posted a missed big blind can check), or with a straddle or dead money posted, the chart steps aside.

## Checks

- A fresh solve matches HoldemResources' published Nash solution at 15 depths from 2 to 25bb: every hand played differently is one both treat as a near coin flip (within 0.001bb), and the check fails if card removal is dropped or equities shift half a point (`tests/test_preflop_solver.py`; run `uv run python scripts/fetch_hrc_pushfold.py` first).
- The committed tables are worth what HoldemResources' strategy is: the small blind's value with both players on each shipped table equals its value on HoldemResources' at every depth from 2 to 10bb, within 0.0001bb (same file).
  Hands played differently from HoldemResources are within about 0.008bb between their two actions; the shipped tables stopped after 100 iterations at 0.004-0.014 mbb of exploitability (0.5 mbb is only the generator's stopping target).
- Every committed table's exploitability is recomputed from its strategies and is under 0.5 mbb a hand, aces never fold, and each file matches the SHA-256 in `manifest.json` (`tests/test_preflop_tables.py`).
- The rules are checked spot by spot in `tests/test_engine_advice.py`, each fix with a test that fails on the code before it.

## Regenerating

```bash
uv run python scripts/gen_matchups.py         # heads-up card removal and equity, about 1 minute
uv run python scripts/gen_preflop_tables.py   # the push/fold tables and manifest.json
```

## History

- Until 2026-10-05 the solver also produced full-depth tables for 2-4 players (opens, 3-bets and limps, with a flop realization model and bucketed multiway equity).
- An audit against real solver output (published 6-max charts, GTO Wizard heads-up 50bb, HoldemResources push/fold) found only heads-up push/fold reliable, so those tables were retired and their code removed; the write-up and code are in git history (commit 00c9426 and earlier).
- An independent review of the rules on 2026-10-05 led to the all-in, price, squeeze and effective-stack fixes described above.
