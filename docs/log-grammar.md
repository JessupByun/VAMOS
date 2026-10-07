# PokerNow log grammar

This is the reconciled grammar of PokerNow game-log lines, merged from the tracker, lmc4s, converters and fixtures inventories.
The CSV export (`entry,at,order`) and the live `/games/<id>/log` endpoint (`msg`) carry the same text, so one grammar serves both.
Only No-Limit Hold'em cash hands are parsed; every other variant is recognized at its start line and skipped.

## Sources and confidence

- `verified` means the line occurs in a real export, in the VAMOS live recording or in the rows VAMOS saved from real games.
- `parser-only` means only parser code, a synthetic fixture or an unrecorded claim supports it.
- J is Jaaaayden/pokernow-tracker `pnt/logs`: 25 real CSV exports from 2026-07-23 to 2026-09-12, 128,065 rows, 6,631 hands, all NLH cash, MIT licensed.
- JT is the same repo's `tests/fixtures`: 6 unredacted copies of J games that differ from J only by extra `Your hand is` rows.
- R is the VAMOS live recording from the P0 guest table: 23 unique rows from 2026-10-02.
- S is the rows VAMOS saved live from the user's own games in `data/games/` (gitignored, never in the repository): 15 games and 21,643 rows from 2026-10-04 to 2026-10-07, every one of which fullmatches a pattern in this file.
- L is 12 legacy CSV exports attached to GitHub issues (pn2ps, PokerNowKit, evo converter) from 2020-05 to 2023-10: 12,117 rows, including 88 PLO Hi and 48 PLO Hi/Lo hands.
- J and JT are fetched into the gitignored `tests/corpus/` by `scripts/fetch_corpus.py`, and `tests/test_log_corpus.py` (`uv run pytest -m corpus`) re-checks the key invariants on every run: every row parses, every finished hand balances, and every all-in player puts in exactly their stack.
- L was examined during research only and is not part of the repository.
- Every J row (128,065) fullmatches one of the `^` patterns in this file after `rstrip()`, and the only L rows that do not are the 57 pre-July-2020 rows listed under Legacy rows.

## Row model

- Both the CSV export and `/log` are newest first, so sort ascending by `order` (CSV) or `created_at` (JSON) and dedupe on that key.
- `order` and `created_at` are both `epoch_ms * 100 + seq`, and `created_at` arrives as a JSON string, so compare it as an integer.
- Never sort by `at`, because many rows share one millisecond (sequence numbers up to 13 were seen).
- Gaps in `seq` are other players' private `Your hand is` rows and are not lost data.
- One entry can contain embedded newlines (`Game Config Changes`), so read the CSV with a real CSV parser and treat each JSON `msg` as one entry.
- Strip trailing whitespace from each entry before matching, because the two real all-in blind posts end in a space.
- No real row has leading whitespace.

## Tokens

```
P      = "(?P<name>.*?) @ (?P<id>[A-Za-z0-9_-]+)"     quoted player token; P2 is a second token on the same line
N      = \d+(?:\.\d+)?                                integer, or exactly 2 decimals in cents mode
CARD   = (?:10|[2-9JQKA])[♠♥♦♣]
CARDS  = CARD(?:, CARD)*
ALLIN  = (?P<allin> and go all in)?                    matched after rstrip()
```

- Every pattern below is anchored with `^` and `$` and is meant for `re.fullmatch` on the stripped entry.
- The lazy name plus the anchored `@ ID"` splits the token on the last ` @ `, which matters because a real name is `spa @ ces` (charlestudor/PokerNowLogConverter `Input_Log_002.csv`).
- Every observed ID is exactly 10 characters of `[A-Za-z0-9_-]` and may start with a dash (`-47FT3Kv-N`).
- The ID is the identity key, because display names change within and across games while the ID stays the same.
- Ten is written `10`, and real rows always use the suit glyphs `♠♥♦♣`.
- No log row in J or L contains a thousands separator, unlike DOM values.
- The all-in suffix is ` and go all in` (never `goes`), and it can follow any post, call, bet or raise.

## Hand state model

- Before the first start line, and between hands, rows are table-level (seating, admin, config, stakes).
- `-- starting hand` opens a hand; if a hand is still open, close the open one as incomplete first and keep it out of money figures.
- `-- ending hand #N --` closes betting but leaves hand N as the attachment target for later shows, `Undealt cards:` rows and 7-2 bounty rows until the next start line.
- When the start line names any variant other than `No Limit Texas Hold'em`, enter skip mode and drop every hand row, including post-hand rows, until the next `-- starting hand`.
- Table-level rows (seating, admin, config, blind changes, ID changes) are still applied in skip mode, because the next NLH hand depends on them.
- Track `committed[player]` per street and reset it only at a main-run `Flop:`, `Turn:` or `River:` row.
- Small blind, big blind, straddle, missed big blind, call, bet and raise all carry the player's new street total, so set `committed = max(committed, N)` and take the increment as the difference.
- A missing small blind and an ante are dead money that enter the pot without touching `committed`.
- `Uncalled bet of N` is subtracted from the bettor's contribution.
- For every complete hand, the sum of street totals plus dead money minus uncalled returns equals the sum of `collected ... from pot` rows (J 6,629 of 6,629; L 475 of 481, where the 6 failures are pre-July-2020 verbs).
- `Player stacks` values are stacks before any post, because for 1,006 of 1,006 all-in players in J the sum of their street totals plus dead posts equals that value.
- The preflop price to call is the larger of the highest commitment and the nominal big blind, because a short all-in big blind posts less than the nominal amount.
- Showdown is decided by counting players who never folded, never by the presence of `shows` rows.

## Line types

### Hand framing

#### hand_start (verified)

```
^-- starting hand #(?P<n>\d+)(?: \(id: (?P<hand_id>[a-z0-9]+)\))?(?: +(?P<variant>.+?))? +\((?:dealer: P|dead button)\) --$
```

```
-- starting hand #1 (id: ywhe4rpsfvih)  No Limit Texas Hold'em (dealer: "TestVillain @ FKxeoYccvW") --
-- starting hand #26 (id: ndddmwtmyhzo)  No Limit Texas Hold'em (dead button) --
-- starting hand #69  (Pot Limit Omaha Hi) (dealer: "LLATFFEJ @ cp8PZiuFnj") --
```

- It opens a hand and resets all per-hand state.
- `n` rises by 1 per hand within a game (0 gaps in J), and `hand_id` is 12 lowercase alphanumerics.
- The dealer is a player token whose seat comes from the following `Player stacks` row (6,567 of 6,567 J dealers are listed there).
- `(dead button)` replaces the dealer when the button seat is empty (J 64 hands), so positions must be anchored on the big blind.
- The 2026 format puts exactly two spaces between `(id: ...)` and an unparenthesized variant (J 6,631, R 2).
- Older formats are `(id: x)  (No Limit Texas Hold'em)` (2022-2023), `#N  (No Limit Texas Hold'em)` with no id (2021), `#N  (Texas Hold'em)` (2020-06) and `#N (dealer: ...)` with no variant (2020-05).
- Only `No Limit Texas Hold'em` occurs in 2026 data; L has `(Pot Limit Omaha Hi)` (88 hands) and `(Pot Limit Omaha Hi/Lo 8 or Better)` (48 hands).
- Current PokerNow also offers `Pot Limit Omaha Hi/Lo (8 or Better)`, `Pot Limit Omaha 5 Hi` and `Pot Limit Omaha 5 Hi/Lo (8 or Better)`, which contain inner parentheses that a greedy `\((.*)\)` would mis-split.
- VAMOS strips one pair of outer parentheses from `variant` and accepts the hand only when it equals `No Limit Texas Hold'em` exactly.
- A `Game Config Changes` row can share the start row's millisecond and sit just before it.
- J counts: 6,631 (6,567 with a dealer, 64 dead button); L counts: 482.

#### hand_end (verified)

```
^-- ending hand #(?P<n>\d+) --$
```

```
-- ending hand #1 --
-- ending hand #153 --
```

- It closes betting for hand `n`, but rows after it can still belong to hand `n` (see Post-hand rows).
- An export or a live window can stop mid-hand, so a hand may never get this row (J: `pgl1-6EfdPrbVUbhLt_fsj8IS` #261 and `pglkWn5b4Y8whHqWY3tVmrtW1` #35; R: hand #2).
- J counts: 6,629 ends for 6,631 starts.

### Roster and hero

#### player_stacks (verified)

```
^Player stacks: #(\d+) P \((N)\)(?: \| #(\d+) P \((N)\))*$
extract each entry with: #(?P<seat>\d+) "(?P<name>.*?) @ (?P<id>[A-Za-z0-9_-]+)" \((?P<stack>N)\)
```

```
Player stacks: #1 "TestVillain @ FKxeoYccvW" (1000) | #2 "Hero @ heroid0001" (1000)
Player stacks: #1 "hsj @ PEMYRVPxOS" (1000) | #10 "wooooo @ MBFczOlpuA" (4625)
Player stacks: #1 "bick and dalls @ gpP9uUffpu" (1386) | #2 "LingWae @ FQN9hzhzP_" (3109) | #5 "fish @ oMsP5L3lJa" (2678) | #10 "fishidishi @ L0mhqQt-8H" (2315)
```

- It is the only authoritative dealt-in roster, listing every player who acts in the hand.
- Seats are physical seats 1 to 10, sorted ascending, often non-contiguous, and never rotated to the dealer.
- Stacks are before any post and already include the previous hand's bounty payments, admin stack edits and rebuys.
- Check the extracted entry count against the pipe count plus one and treat a mismatch as unknown rather than guessing.
- Stacks are integers in 2026 data and carry 2 decimals in cents mode (`(181.62)` in L).
- `joined the game` rows sit between the start row and this row in 148 J hands, so do not build the roster from joins.
- J counts: 6,631; L counts: 475 plus 1 legacy row without seats.

#### hero_hole_cards (verified)

```
^Your hand is (?P<cards>CARDS)$
```

```
Your hand is 4♠, 10♠
Your hand is 3♠, 4♥
Your hand is 4♦, 10♣, 6♦, 8♣
```

- It holds the viewer's own hole cards and names no player.
- It sits between `Player stacks` and the first blind post or `Dead Small Blind`.
- Each dealt player has a private row, so the hero's row lands `k` sequence steps after `Player stacks`, where `k` is the hero's 1-based index in that roster (JT offsets 1 to 4, R offsets 2 and 2).
- VAMOS takes hero identity from the DOM (`you-player`), not from this row.
- NLH rows have 2 cards and PLO rows have 4 (L: 83 PLO Hi, 48 PLO Hi/Lo).
- The row can be absent: J is redacted to shown hands only, spectator exports have none, and `/log` omits it unless both `npt` and `apt` cookies are sent.
- J counts: 1,855; R: 2; L: 337.

### Forced posts

#### post_small_blind (verified)

```
^P posts a small blind of (?P<amt>N)ALLIN$
```

```
"TestVillain @ FKxeoYccvW" posts a small blind of 10
"hsj @ PEMYRVPxOS" posts a small blind of 5
"LingWae @ FQN9hzhzP_" posts a small blind of 4 and go all in 
```

- It is a live blind that sets the poster's preflop commitment.
- Heads-up, the dealer posts the small blind (R hands #1 and #2).
- The all-in form ends with a trailing space in the real CSV (J `pglBiOB-v1yjtGEt1PQg_UgT4.csv`).
- J counts: 6,594 plus 1 all-in; L: 478 (decimal in cents mode, e.g. `posts a small blind of 0.25`).

#### post_big_blind (verified)

```
^P posts a big blind of (?P<amt>N)ALLIN$
```

```
"Hero @ heroid0001" posts a big blind of 20
"hsj @ PEMYRVPxOS" posts a big blind of 20
"LingWae @ FQN9hzhzP_" posts a big blind of 1 and go all in 
```

- It is a live blind that sets the poster's preflop commitment.
- A short all-in big blind does not lower the price: in J `pglBiOB` hand #86 the BB posts 1 all-in and the next three players each `calls 10`.
- The hand's nominal big blind therefore comes from the latest `The game's big blind was changed` row or the previous hand, not from the largest or first post.
- The all-in form has the same trailing space as the small blind.
- J counts: 6,631 (1 all-in); L: 481.

#### post_straddle (verified)

```
^P posts a straddle of (?P<amt>N)ALLIN$
```

```
"pookienow @ gpP9uUffpu" posts a straddle of 10
"FiggleSticks @ ILQFEWJ0SW" posts a straddle of 10
"Rum Targaryen @ hEwkk2Lyvz" posts a straddle of 1.00
```

- It is live and cumulative: `N` is the poster's preflop total, so a straddle by the small blind poster replaces the small blind rather than adding to it.
- Treating it as cumulative balances 34 of 34 J straddle hands, while adding it balances only 28.
- 17 of the 34 J straddles were posted by the small blind poster, e.g. heads-up SB 2, BB 5, SB straddles 10, BB folds, `Uncalled bet of 5 returned`.
- The straddler gets the last preflop option and logs `checks` if nobody raises.
- Straddles appear only after `Allow Straddle: off » on`, and the all-in suffix is accepted but unobserved.
- J counts: 34 (all in `pgldBYgodxANW2_YvaxBEJh-3.csv`); L: 3.

#### post_missing_small_blind (verified)

```
^P posts a missing small blind of (?P<amt>N)ALLIN$
```

```
"fish @ oMsP5L3lJa" posts a missing small blind of 5
"DK @ -47FT3Kv-N" posts a missing small blind of 5
"Tasi @ gKf3XcCGy2" posts a missing small blind of 10
```

- It is dead money that enters the pot without counting toward the poster's street commitment.
- Treating it as dead balances 27 of 27 J hands, and treating it as live balances 0.
- The wording is `missing`, unlike `missed big blind`, and it usually comes after the regular blinds and before the same player's missed big blind.
- J counts: 27; L: 10.

#### post_missed_big_blind (verified)

```
^P posts a missed big blind of (?P<amt>N)ALLIN$
```

```
"fish @ oMsP5L3lJa" posts a missed big blind of 10
"chugnuts @ 7BGoX5yBgi" posts a missed big blind of 5
"pebus @ ILQFEWJ0SW" posts a missed big blind of 10
```

- It is live and cumulative, so it sets the poster's preflop commitment and absorbs a small blind the same player posted.
- Cumulative balances 20 of 20 J hands, additive balances 14 and dead balances 3.
- In 7 J hands the same player posted the small blind and then a missed big blind, e.g. `pglSdQtyFGypDbrqD5IhXXlYz` #171 (`chugnuts` SB 2, then missed BB 5).
- Its poster can later `checks` when limped to, which never happens to a player facing a bet (0 such checks in J).
- J counts: 20; L: 4.

#### post_ante (verified, legacy only)

```
^P posts an ante of (?P<amt>N)ALLIN$
```

```
"JMan @ Z_JcOkRrQ0" posts an ante of 0.10
"JS @ WZHH2swU87" posts an ante of 0.10
```

- It is dead money posted by every dealt player before the blinds.
- Treating it as dead balances 136 of 136 L `evo18` hands, and treating it as live balances 0.
- The real wording is `posts an ante of`, and the tracker grammar's `posts a ante of` has no real example.
- The 2026 wording is unverified, because no J game had an ante above zero (`The game's ante was changed from 0 to 0.`).
- L counts: 882 (`evo18_log_120422.csv`, 2022-12, cents mode); J: 0.

#### dead_small_blind (verified)

```
^Dead Small Blind$
```

```
Dead Small Blind
```

- No small blind is posted this hand because the small blind seat is empty, and only the big blind posts.
- The row names no player and no amount, and it sits where the small blind post would be.
- The big blind then sits one slot from the button, so anchor positions on the big blind poster (2 slots from the button, or 1 heads-up).
- J counts: 37; L: 3.

### Betting actions

#### fold (verified)

```
^P folds$
```

```
"TestVillain @ FKxeoYccvW" folds
"hsj @ PEMYRVPxOS" folds
```

- The player leaves the hand, and a decision-timer timeout is logged as an ordinary fold (docs/pokernow.md).
- J counts: 8,844; R: 1; L: 1,798.

#### check (verified)

```
^P checks$
```

```
"hsj @ PEMYRVPxOS" checks
"wooooo @ MBFczOlpuA" checks
```

- No chips move, and no J row checks while facing a bet once missed big blinds are treated as live.
- A pre-selected check can share the millisecond of the street row.
- J counts: 15,466; L: 1,484.

#### call (verified)

```
^P calls (?P<amt>N)ALLIN$
```

```
"Hero @ heroid0001" calls 20
"Getting even @ a8Gu84mPIA" calls 953 and go all in
"Harry @ Q4OMcL0rqd" calls 2445 and go all in
```

- `amt` is the caller's total commitment on this street, and the chips added are `amt - committed`.
- In R hand #2 the small blind posts 10 and then `calls 20`, adding 10.
- No J call has a zero increment, so a caller always adds chips.
- With the all-in suffix `amt` can be below the current bet (144 partial calls in J).
- J counts: 15,274 plus 161 all-in; R: 1; L: 1,529 (decimals such as `calls 5.00` in cents mode).

#### bet (verified)

```
^P bets (?P<amt>N)ALLIN$
```

```
"wooooo @ MBFczOlpuA" bets 20
"hsj @ PEMYRVPxOS" bets 815 and go all in
"rk @ PIUrT-JXRC" bets 16.00
```

- It opens betting on a postflop street, and `amt` is the street total, which equals the increment.
- No J bet occurs preflop or while facing a bet.
- J counts: 7,448 plus 312 all-in; L: 612.

#### raise (verified)

```
^P raises to (?P<amt>N)ALLIN$
```

```
"wooooo @ MBFczOlpuA" raises to 60
"hsj @ PEMYRVPxOS" raises to 905 and go all in
"6767 @ 3eHJYCMqYM" raises to 110 and go all in
```

- `amt` is the raiser's new street total including blinds and earlier bets on the street, and the increment is `amt - committed`.
- A preflop open is logged as a raise, and no J raise occurs on an unopened postflop street.
- An all-in raise can be incomplete (32 in J), e.g. facing 105 after a 60 raise, `raises to 110 and go all in` adds only 5 over the bet and must not reopen action.
- J counts: 6,618 plus 531 all-in; L: 550.

### Board

#### board_flop (verified)

```
^Flop(?: \((?P<extra>second run|second board)\))?:  \[(?P<cards>CARD, CARD, CARD)\]$
```

```
Flop:  [10♣, 6♣, 3♥]
Flop (second run):  [K♠, A♥, J♥]
Flop (second board):  [2♥, 9♣, 2♣]
```

- A plain `Flop:` row starts the flop and resets every street commitment to 0.
- There are always two spaces after the colon.
- J counts: 5,168 plain, 102 second run, 2 second board; L: 409 (2020 rows use lowercase `flop:`).

#### board_turn (verified)

```
^Turn(?: \((?P<extra>second run|second board)\))?: (?P<board>CARDS) \[(?P<new>CARD)\]$
```

```
Turn: 10♣, 6♣, 3♥ [6♥]
Turn (second run): K♠, A♥, J♥ [6♣]
Turn (second board): 2♥, 9♣, 2♣ [3♥]
```

- A plain `Turn:` row starts the turn and resets commitments, and the prefix restates the board with only the new card bracketed.
- J counts: 4,224 plain, 158 second run, 2 second board; L: 319.

#### board_river (verified)

```
^River(?: \((?P<extra>second run|second board)\))?: (?P<board>CARDS) \[(?P<new>CARD)\]$
```

```
River: 10♣, 6♣, 3♥, 6♥ [Q♣]
River (second run): K♠, A♥, J♥, 6♣ [K♣]
River (second board): 2♥, 9♣, 2♣, 3♥ [6♥]
```

- A plain `River:` row starts the river and resets commitments.
- J counts: 3,503 plain, 218 second run, 2 second board; L: 258.

#### board_second_run (verified)

- `(second run)` rows are the run-it-twice second runout, dealt after all action and after the whole first runout.
- They must not advance the street or reset commitments.
- Second-run rows exist only for streets dealt after the all-in, so an all-in on the flop yields `Turn (second run)` and `River (second run)` that restate the shared flop.
- The first runout uses plain `Flop:`, `Turn:` and `River:` labels.

#### board_second_board (verified)

- `(second board)` rows are the Double Board option and sit right after the first board's row for the same street, with that street's betting after both rows.
- They must not reset commitments a second time.
- Each board gets its own `collected` row, the second ending in ` on the second board`.
- They appear only after `Double Board: off » always` (J `pglZajS5mhezuh8YfoFI0q0KO.csv` hands #152 and #153).

### Showdown and payout

#### uncalled_bet (verified)

```
^Uncalled bet of (?P<amt>N) returned to P$
```

```
Uncalled bet of 10 returned to "Hero @ heroid0001"
Uncalled bet of 1092 returned to "Harry = Fish @ kCN0-Qy2Mz"
Uncalled bet of 16.00 returned to "rk @ PIUrT-JXRC"
```

- It refunds the unmatched part of the last bet or raise, so subtract it from that player's contribution.
- It precedes that player's `collected` row, which is already net of the refund.
- It can come after run-it-twice prompts and shows, not only right after the last fold.
- The player token is at the end of the line.
- J counts: 4,170; R: 1; L: 275.

#### collect (verified)

```
^P collected (?P<amt>N) from pot$
```

```
"Hero @ heroid0001" collected 20 from pot
"hsj @ PEMYRVPxOS" collected 120 from pot
"JMan @ Z_JcOkRrQ0" collected 0.80 from pot
```

- It awards a pot or side pot without a shown hand, and there is no rake row.
- The pot regex must require `from pot`, because bounty summaries also start with `collected N from`.
- J counts: 4,152; R: 1; L: 273.

#### collect_with_hand (verified)

```
^P collected (?P<amt>N) from pot with (?P<desc>.+?)(?P<run> on the second (?:run|board) )? \(combination: (?P<combo>CARDS)\)$
```

```
"Chris @ 5NARaPRkSp" collected 1620 from pot with Straight, A High (combination: 10♠, J♣, Q♥, K♣, A♦)
"wooooo @ MBFczOlpuA" collected 905 from pot with Two Pair, A's & K's on the second run  (combination: J♥, K♠, K♣, A♣, A♥)
"LingWae @ FQN9hzhzP_" collected 72 from pot with Two Pair, K's & 2's on the second board  (combination: 9♣, 2♥, 2♣, K♥, K♠)
```

- It is a showdown award, and a hand can have several (side pots, one per run, one per board, split pots).
- Sum every collect row per player, and the total across rows equals the pot.
- There are two spaces before `(combination:` after ` on the second run` or ` on the second board` (238 rows) and one space otherwise (2,851 rows).
- 2026 descriptors are `A High`, `Pair, J's`, `Two Pair, 10's & 6's`, `Three of a Kind, 7's`, `Straight, 8 High`, `Flush, A High`, `Full House, 8's over 3's`, `Four of a Kind, 7's` and `Straight Flush, Q High`.
- `Royal Flush` never occurs in J, so do not rely on it.
- J counts: 2,850 (233 second run, 2 second board); L: 239.

#### show (verified)

```
^P shows a (?P<cards>CARDS)\.$
```

```
"wooooo @ MBFczOlpuA" shows a 2♦, 7♠.
"gurt is backyo @ gpP9uUffpu" shows a A♥.
"gurt is backyo @ gpP9uUffpu" shows a K♣.
```

- It always reads `shows a` with a trailing period, for one or two cards (four in legacy PLO).
- Inside a hand it is a showdown or all-in reveal, and all-in reveals come before the remaining board rows.
- After `-- ending hand` it is a voluntary show that belongs to the hand that just ended.
- A player can reveal one card per row, even inside a hand at an all-in (J `pglkWn5b4` hand #30: `shows a A♥.` then `shows a K♣.`), so union cards per player per hand.
- J counts: 3,881 two-card and 22 one-card inside hands, 1,894 two-card and 2,016 one-card after the hand ended; L: 426.

### Run it twice

#### rit_prompt (verified)

```
^Remaining players decide whether to run it twice\.$
```

- It is logged at the millisecond of the all-in call that closes action when the table is set to ask players.
- It is always followed by player choices and exactly one result row (333 prompts, 218 agreed plus 115 declined).

#### rit_choice (verified)

```
^P chooses to  run it twice\.$
^P chooses to not run it twice\.$
```

```
"wooooo @ MBFczOlpuA" chooses to  run it twice.
"LingWae @ FQN9hzhzP_" chooses to not run it twice.
```

- It is one player's vote and moves no chips.
- The yes form has two spaces (`chooses to  run`) and the no form has one (522 and 124 rows in J plus L, with no other spacing seen).
- One `not` cancels the run, and remaining players may then go unlogged.

#### rit_result (verified)

```
^All players in hand choose to run it twice\.$
^Some players choose to not run it twice\.$
```

- The agreed form means `(second run)` boards and a second set of collect rows follow.
- J counts: 218 agreed and 115 declined; L: 16.

### Post-hand rows

#### rabbit_hunt (verified)

```
^Undealt cards: (?:(?P<board>CARDS))? \[(?P<undealt>CARDS)\]$
```

```
Undealt cards: 8♥, 10♥, 3♥ [5♣, A♣]
Undealt cards:  [A♥, 4♥, 4♦, K♦, 9♣]
Undealt cards: A♦, 10♠, 9♥, K♦ [2♣]
```

- It is a rabbit hunt that always comes after `-- ending hand`, and it must never extend the board or advance a street.
- A preflop end has an empty prefix and two spaces before `[` (1,315 rows), and a flop or turn end has a prefix (1,569 rows).
- It is viewer-specific, and a cookie-less `/log` walk omits it.
- J counts: 2,884; L: 0.

#### bounty_paid (verified)

```
^P paid (?P<amt>N) for the (?P<kind>.+?) bounty to P2$
```

```
"gurt @ gpP9uUffpu" paid 30 for the 7-2 bounty to "9/2 @ d-4X_F_SSU"
"LingWae @ FQN9hzhzP_" paid 30 for the 7-2 bounty to "9/2 @ d-4X_F_SSU"
```

- It is a stack-to-stack side bet settled after the hand ends, outside any pot, and it belongs to the hand that just ended.
- It counts toward a player's net result and never toward pot arithmetic.
- `kind` has only been `7-2`, after `7-2 bounty: off » N`.
- J counts: 76 in 8 games.

#### bounty_collected (verified)

```
^P collected (?P<amt>N) from the (?P<kind>.+?) bounty$
```

```
"9/2 @ d-4X_F_SSU" collected 90 from the 7-2 bounty
"gurt @ gpP9uUffpu" collected 30 from the 7-2 bounty
```

- It is the sum of the `paid` rows just before it, so counting both credits the winner twice.
- J counts: 40.

### Seating and admin rows

All rows in this group are verified, move no chips within the current hand, and are often logged mid-hand (578 seating or admin rows inside J hands).

#### seat_requested and seat_request_canceled (verified)

```
^The player P requested a seat\.$
^The player P canceled the seat request\.$
```

```
The player "Hero @ heroid0001" requested a seat.
The player "DK @ kCN0-Qy2Mz" canceled the seat request.
```

- A request can carry a new nickname for an existing ID (`aa @ GS_aS71-UM` later requested as `a a @ GS_aS71-UM` in L).
- J counts: 264 requested, 4 canceled; R: 3; L: 30.

#### admin_approved_seat and admin_rejected_seat (verified)

```
^The admin approved the player P participation with a stack of (?P<amt>N)\.$
^The admin P rejected the seat request from the player P2\.$
```

```
The admin approved the player "Hero @ heroid0001" participation with a stack of 1000.
The admin "hsj @ PEMYRVPxOS" rejected the seat request from the player "lick it back @ EuhV1r7dIz".
```

- An approved player is dealt in from a later hand, announced by a `joined` row.
- J counts: 258 approved, 1 rejected; R: 3; L: 34.

#### joined (verified)

```
^The player P joined the game with a stack of (?P<amt>N)\.$
```

```
The player "TestVillain @ FKxeoYccvW" joined the game with a stack of 1000.
The player "wooooo @ MBFczOlpuA" joined the game with a stack of 4625.
```

- The player is seated, and in 148 J hands (and R hand #1) the row sits between `-- starting hand` and `Player stacks` of the hand they join.
- J counts: 347; R: 2; L: 43.

#### quit, stand_up and sit_back (verified)

```
^The player P quits the game with a stack of (?P<amt>N)\.$
^The player P stand up with the stack of (?P<amt>N)\.$
^The player P sit back with the stack of (?P<amt>N)\.$
```

```
The player "hsj @ PEMYRVPxOS" quits the game with a stack of 0.
The player "bring it back @ ILQFEWJ0SW" stand up with the stack of 1701.
The player "bring it back @ ILQFEWJ0SW" sit back with the stack of 1701.
```

- The wording is ungrammatical as logged: `stand up` and `sit back` take `with the stack of`, while join and quit take `with a stack of`.
- Quit and stand up are logged between hands, and sit back can be logged mid-hand (24 of 44) without joining the current hand.
- Every real row starts with `The player`.
- J counts: 218 quit, 57 stand up, 44 sit back; L: 23, 22 and 15.

#### ownership_passed (verified)

```
^The player P passed the room ownership to P2\.$
```

```
The player "hsj @ PEMYRVPxOS" passed the room ownership to "wooooo @ MBFczOlpuA".
```

- The admin role moves to `P2`, with no chip effect.
- J counts: 11; L: 3.

#### admin_stack_update (verified)

```
^The admin updated the player P stack from (?P<old>N) to (?P<new>N)\.$
```

```
The admin updated the player "bick and dalls @ gpP9uUffpu" stack from 1000 to 500.
The admin updated the player "JS @ WZHH2swU87" stack from 42.65 to 142.65.
```

- It is an admin chip adjustment applied between hands (86 of 87), not a hand result.
- The next `Player stacks` row already reflects it.
- J counts: 87; L: 12.

#### admin_queued_stack_change (verified)

```
^WARNING: the admin queued the stack change for the player P (?:(?P<dir>adding|removing)|(?P<reset>reseting to)) (?P<amt>N) chips in the next hand\.$
```

```
WARNING: the admin queued the stack change for the player "LingWae @ FQN9hzhzP_" adding 500 chips in the next hand.
WARNING: the admin queued the stack change for the player "bick and dalls @ gpP9uUffpu" removing 500 chips in the next hand.
WARNING: the admin queued the stack change for the player "Hero @ heroid0001" reseting to 50.00 chips in the next hand.
```

- It is an announcement, usually mid-hand (68 of 90), and must not touch the current hand.
- `adding` and `removing` carry the change, while `reseting to` (PokerNow's spelling) carries the new stack; VAMOS keeps the reset as `detail: "reset"`.
- The reset example is from S with the player replaced; its 6 rows (3 games) all came between hands, and the next `Player stacks` row showed the reset stack each time (the later one when two were queued before the same hand).
- J counts: 90 (37 adding, 53 removing); L: 13; S: 6 resets, 2 removing.

#### admin_force_away, admin_remove_player and admin_game_stop (verified)

```
^The admin P forced the player P2 to away mode in the next hand\.$
^The admin P enqueued the removal of the player P2\.$
^The admin P (?P<op>enqueued|canceled) the game stop on next hand\.$
```

```
The admin "gurt @ gpP9uUffpu" forced the player "DK @ -47FT3Kv-N" to away mode in the next hand.
The admin "gurtyo @ gpP9uUffpu" enqueued the removal of the player "The Great Wall @ D6rY2hGO4r".
The admin "gurt @ gpP9uUffpu" enqueued the game stop on next hand.
```

- Their effects are deferred to the next hand, whose `Player stacks` row is the roster.
- The first token is the admin and the second token is the target.
- J counts: 7 away, 1 removal, 17 stop enqueued, 3 stop canceled.

### Stakes and config

#### blind_change (verified)

```
^The game's (?P<which>small blind|big blind|ante) was changed from (?P<old>N) to (?P<new>N)\.$
```

```
The game's big blind was changed from 20 to 10.
The game's small blind was changed from 0.10 to 0.05.
The game's ante was changed from 0 to 0.
```

- It changes stakes for later hands and is logged between hands, usually beside a `Blind Schedule` config row.
- The ante row is emitted even when unchanged.
- It is the best log-side source for the nominal big blind that a short all-in big blind does not reveal.
- J counts: 90 (33 small blind, 33 big blind, 24 ante, decimals in `pglkWn5b4`); L: 7.

#### game_config_changes (verified)

```
^Game Config Changes(?:\n\* (?P<key>[^:\n]+): (?P<old>.*?) » (?P<new>.*))+$
```

```
Game Config Changes
* Double Board: off » always
```

```
Game Config Changes
* Blind Schedule: L1: 10/20 » L1: 5/10
* Restart Blind Schedule at Level: none » 1
```

- It is one multi-line entry, one `* Key: old » new` line per setting.
- Keys seen: Blind Schedule, Restart Blind Schedule at Level, Allow Rebuy, Nit Minimum Players, Nit Game, Nit Prize (BB), Rabbit Hunt, Allow Run it Twice, 7-2 bounty, Allow Straddle, Double Board, Bomb Pot, Bomb Pot Probability, Bomb Pot BB Quantity, Game Type Cyling (sic), Simulated Fee, Simulated Fee Percentage, Simulated Fee Max Value, Spectator Mode, Cents Mode, Decision Limit Time.
- Double Board, Allow Run it Twice, Allow Straddle, 7-2 bounty, Bomb Pot, Cents Mode and Game Type Cyling change how later hands are logged; VAMOS keeps them as table rows only (second runs and boards are read from the board rows themselves, and table modes are not tracked).
- J counts: 69 (68 between hands).

### Rebuy flow

#### rebuy rows (verified)

```
^Asking to busted players the rebuy decision\.$
^Waiting for the game owner to approve or reject pending rebuy requests\.$
^The player P requested a rebuy of (?P<amt>N)\.$
^The player P rebought\. New stack (?P<amt>N)\.$
```

```
Asking to busted players the rebuy decision.
The player "Harry @ Q4OMcL0rqd" requested a rebuy of 1000.
The player "Harry @ Q4OMcL0rqd" rebought. New stack 1000.
```

- These are between-hand rows, and `rebought` gives the new stack, not the delta.
- J counts: 29 prompt, 21 waiting, 25 requested, 25 rebought, only with `Allow Rebuy` on.

### Legacy rows (verified in L, recognize and skip)

- `Players stacks: "jasondragon @ KMgRtZHdge" (21656) | ...` is the pre-July-2020 roster without seat numbers.
- `"Áron @ KqPrDJjSfQ" calls with 80`, `"Stev0 @ -rMjATuApd" raises with 80` and `"Áron @ KqPrDJjSfQ" raises and all in with 1190` are 2020 betting verbs.
- `"bobo @ Vf1v-slbuU" wins 7400 with Pair, K's (hand: Q♠, K♦)` and `"Áron @ KqPrDJjSfQ" gained 960` are 2020 award rows.
- `The player "bobo @ O7q--fndwt" created the game with a stack of 1000.` is a 2020 seating row.
- Lowercase `flop:`, `turn:` and `river:` are 2020 board rows.
- These 2020 forms do not appear in any 2026 row, so VAMOS leaves them as unknown events rather than parse them.
- Hand descriptors without ranks (`Two Pair`), with suit letters (`Flush, Kh High`), PLO hi/lo text (`hi hand with ...`) and 6-card combinations occur in 2021-2022 rows.
- The collect rule accepts these as `collect`, keeping the descriptor as raw text and the combination cards as given; they never appear in 2026 rows.

### Parser-only rows (no real example)

#### rit_forced (parser-only)

```
^The board will run it twice due(?: to)? the game settings\.$
```

- The tracker's test says it was seen live on 2026-09-17 on a table set to always run it twice, without `to`, but no export row exists.

#### id_changed (parser-only)

```
^The player P changed the ID from (?P<old>[A-Za-z0-9_-]+) to (?P<new>[A-Za-z0-9_-]+) because (?P<reason>.+)\.$
```

- The tracker's test says `The player "Henry @ FQN9hzhzP_" changed the ID from wgnNFHC2ZI to FQN9hzhzP_ because authenticated login.` was seen live on 2026-09-13.
- In that example the token already carries the new ID, while the LMC4S synthetic fixture puts the old ID in the token.
- VAMOS takes both IDs from the `from X to Y` clause and aliases the old ID to the new one.

#### bomb_pot_post (parser-only)

```
^P (?:posts a bet of|calls) (?P<amt>N)ALLIN \(bomb pot bet\)$
```

- Only the synthetic LMC4S fixture has these rows, although `Bomb Pot` exists as a real config key.

#### Other parser-only forms

- `P mucks ...`, `P does not show ...`, `P chooses not to show ...` and `P decided to not show ...` come only from an LMC4S regex.
- `P goes all in [with N]` comes only from an LMC4S regex.
- `posts a missed small blind`, `posts a missing big blind`, `posts a dead small blind`, `posts a dead big blind` and `posts a big blind ante` come only from an LMC4S regex.
- `posts a ante of N` comes only from the tracker grammar and a synthesized test, and real legacy data says `posts an ante of N`.
- `Undealt cards (<variant>): ...`, a `third` run, `collected N from the pot`, a negative `Player stacks` value, and seating rows without the `The player` prefix come only from LMC4S.
- `stands up` and `sits back` come only from an LMC4S regex.

## Conflicts and resolutions

- Calls, bets and raises: every inventory reads `N` as the street total, and J balances 6,629 of 6,629 hands as totals versus 1,428 as increments; the live recording R agrees (hand #2's small blind `calls 20` adds 10).
- Raise amounts include the raiser's earlier chips on the street, blinds included, as shown by J `pglkWn5b4` hand #30, where totals of 270 preflop and 740 on the flop per player give the collected 2,020.
- Straddle: LMC4S adds it to the small blind, PokerNowKit treats it as `raises to`, and evo assumes 2xBB, but J balances 34 of 34 hands as cumulative and 28 of 34 as additive, so it is cumulative.
- Missed big blind: LMC4S adds it, while the tracker and fixtures treat it as live and cumulative, and J balances 20 of 20 cumulative, 14 additive and 3 dead, so it is live and cumulative.
- Missing small blind: all sources call it dead, and J confirms it (27 of 27 dead, 0 live).
- Ante wording: the tracker grammar expects `posts a ante`, but the only real rows say `posts an ante` (L 882), so `an` wins with 2026 still unverified.
- Ante liveness: LMC4S counts it toward the street total, but L `evo18` balances 136 of 136 hands as dead and 0 as live, so it is dead.
- Hand big blind: the tracker takes the largest big-blind post and LMC4S the first, but J `pglBiOB` #86 shows a 1-chip all-in big blind followed by `calls 10`, so the nominal big blind must come from stakes rows or the previous hand.
- Start-line format: the tracker saw only the unparenthesized 2026 form, while LMC4S, the converters and L show parenthesized and id-less forms from 2020-2023, so all exist by era and only the 2026 form matters live.
- PLO Hi/Lo name: legacy rows say `Pot Limit Omaha Hi/Lo 8 or Better`, current PokerNow says `Pot Limit Omaha Hi/Lo (8 or Better)`, and evo says `Pot Limit Omaha Hi/Lo`, so VAMOS whitelists NLH instead of listing names to skip.
- Run-it-twice vote spacing: the tracker's findings.md says both forms have two spaces, but J and L have two spaces only in the yes form (522) and one in the no form (124).
- Trailing space: the tracker's test claims only live rows end in a space, but the J CSV has it on both all-in blind posts, and no other real row has trailing whitespace.
- Combination spacing: LMC4S assumes one space, but real rows have two after ` on the second run|board` (238) and one otherwise (2,851).
- Card notation: the LMC4S fixture uses ASCII cards (`As`, `Th`), but every real source uses glyphs and `10`.
- Decimal amounts: the tracker and LMC4S accept decimals only in blind-change rows, but L has 2,691 decimal betting rows that balance, so every amount must accept `\d+(?:\.\d+)?`.
- ID change token: the tracker's live example puts the new ID in the token and the LMC4S fixture puts the old one there, which stays open until VAMOS records one.
- In-hand single-card shows: Jaaaayden keeps only two-card in-hand shows, while LMC4S unions cards, and J has 22 in-hand single-card rows, so union per player.
- Shows after the hand ends: all sources agree they belong to the hand that just ended, and J adds rabbit hunts and bounty rows to that group.

## Parsing traps

- Amounts are street totals, so reading `calls N` as an increment breaks almost every pot while still looking plausible.
- Reset street commitments only on main-run `Flop:`, `Turn:` and `River:` rows, never on `(second run)` or `(second board)` rows.
- Small blind, big blind, straddle and missed big blind are live, while missing small blind and ante are dead.
- A short all-in big blind does not lower the price to call, so keep the nominal big blind.
- An incomplete all-in raise (32 in J) does not reopen action, and an all-in call can be below the current bet (144 in J).
- Strip trailing whitespace before anchored matching, because `posts a big blind of 1 and go all in ` ends in a space.
- Double spaces are part of the format in `(id: x)  No Limit`, `Flop:  [`, `Undealt cards:  [`, `chooses to  run it twice.` and `on the second run  (combination:`.
- Anchor every rule on the quoted player token plus a fully anchored suffix, because names such as `all in`, `500`, `9/2` and `Harry = Fish` defeat substring searches.
- Split a player token on its last ` @ `, and use lazy captures for two-token rows so the first token cannot swallow the second.
- Never split a file on newlines, because `Game Config Changes` entries contain them.
- Sort by `order` or `created_at` as integers, never by `at`, and dedupe overlapping `/log` pages on `created_at`.
- `/log` pages backwards only: `after_at` returns the newest 50 rows above the cursor, so walk older rows with `before_at` and back off on HTTP 429.
- Rows after `-- ending hand #N --` (2,016 one-card and 1,894 two-card shows, 2,884 rabbit hunts, 116 bounty rows) belong to hand N.
- Seating and admin rows interleave mid-hand and must neither end nor alter the current hand.
- Build the roster only from `Player stacks`, since `joined` rows can precede it inside the hand.
- Positions come from physical seats over dealt-in players with the dealer token as anchor, and fall back to the big blind poster on `(dead button)` or after `Dead Small Blind`.
- Heads-up with `(dead button)`, VAMOS makes the player who is not the big blind the button, first to act before the flop and last after it, as in any heads-up hand; no such hand is in J, R or S, so this order is unverified.
- Showdown comes from counting players who never folded, because all-in shows arrive before the remaining board rows.
- A hand can have many `collected` rows (side pots, runs, boards, splits), so never assume one winner.
- A pot regex must require `from pot`, because `collected N from the 7-2 bounty` shares the prefix.
- Bounty rows move chips outside the pot and the bounty summary repeats the payments, so count only the `paid` rows.
- `Undealt cards:` is a rabbit hunt and must never extend the board.
- A new start row while a hand is open means the earlier hand is incomplete, and exports can also begin with seating or config rows before hand 1.
- `Your hand is` can be missing (redacted exports, spectators, a missing cookie, the hero sitting out), so never require it.
- Mojibake such as `Aâ¦` means the file was decoded as latin-1, which must be fixed when the file is opened.
- Skip non-NLH hands as whole blocks, because their action rows look normal and their hole-card rows have 4 cards.
- Pre-July-2020 verbs (`calls with`, `raises with`, `gained`, `wins ... (hand: ...)`) and lowercase streets do not match 2026 patterns and must be logged as unsupported.
- Key players on the ID, because one ID appears under several names in one game and nicknames change between hands.

## Gaps to record from VAMOS guest tables

- A 2026 start row for every non-NLH variant (PLO, PLO Hi/Lo, PLO5, PLO5 Hi/Lo), including a `Game Type Cyling` session that switches variants between hands.
- 2026 ante rows, to settle `posts an ante of` versus `posts a ante of`, plus an all-in ante.
- Bomb pot hands, to capture the forced-bet wording and confirm the hand skips blinds.
- A login mid-session, to record the ID-change row and learn which ID the token carries.
- A heads-up hand with `(dead button)` (the player due the button leaves a 3-handed table), to confirm who acts first after the flop.
- A table set to always run it twice, to record the forced run-it-twice row in an export.
- Cents-mode betting rows in the 2026 format.
- A table with `Simulated Fee` on, to learn whether a fee row appears and whether pots still balance.
- A decision-timer timeout when checking is free, to learn whether it logs `checks` or `folds`.
- Muck or `does not show` rows, if PokerNow logs them at all.
- A straddle or missed big blind posted all-in, to confirm the suffix and its trailing space.
- Run it twice combined with Double Board, and run it twice with side pots, to fix the order of collect rows.
- `Undealt cards:` after a run-it-twice or Double Board hand.
- An executed admin removal and an away-mode player, to learn whether any row follows the enqueue.
- Nit game rows, if the `Nit Game` setting logs anything.
