# Profile guidelines

The rulebook for VAMOS's opponent profiles, one file per player in `profiles/`.
VAMOS owns these files and keeps them current after each session; they are the source of truth about each opponent.
Whoever updates a profile (VAMOS's updater, or Claude working on the repo) follows this file exactly.

## Who reads a profile

- Claude reads the whole file at every decision while that player is at the table, to exploit them. Write for that reader: specific, ranked, actionable.
- jes (the hero, the only user) reads it in the side panel. The panel shows the Summary's first sentence, the Key stats headline lines, and the frontmatter's `hands`, `sessions`, `net` and `last_updated`.
- The next update reads it as the starting point. Never lose information the next update would need.

## Evidence, and how far to trust it

1. **VAMOS's exact counts** from saved games (`data/games/`). Every number in a profile comes from counts, never from estimates or recall.
2. **The hands themselves.** PokerNow's log shows every player's actions in every hand, whether or not jes played it. Hole cards are known only when shown (showdowns and voluntary shows), plus jes's own.
3. **Older sessions from the joined logs** (HU, A, B, C and E, 2026-09-30 to 2026-10-03), where every hole card was known. Their findings stay unless newer evidence contradicts them; when describing hand ranges, say which sessions they come from.
4. **jes's notes** on a player for a session (table talk, mood, tilt). Use them, attributed: "jes noted ...".

Never guess hole cards that were not shown. Never present a read as fact without its evidence.

## File layout

Frontmatter, then the sections below in this order, with these headings.
A section with nothing to say yet stays, with one line saying so.

```
---
name: alex                      # the file name without .md; the only name used for this player
tag: Calling station            # 1 to 3 standard poker words, shown on the seat badge
player_id: Qw3rTy7UiO           # the player's main PokerNow account only
hands: 912                     # every hand counted in this profile, ring and heads-up
sessions: [HU 2026-09-30, A 2026-09-30, ..., N 2026-10-07]
games: {O: pglExampleGameId000000000 through hand 136}   # label -> PokerNow game and the last hand counted
hole_cards_known: HU, A, B, C all (240); later sessions only showdowns and shows
net: -42.60 (HU -12.00, A -18.50, ...)   # total, then each session by label, in dollars
excluded: [Oct 3 by group decision]       # sessions left out and why; omit when none
last_updated: 2026-10-07
---
```

Sections:

1. `## Summary`: two to four sentences. The first is the playstyle in one line (the panel shows only that sentence). Then the single most valuable exploit.
2. `## Key stats`: the Source line, then the headline lines, then any other stats. Keep these headline lines in exactly this wording, since the panel reads them:
   - `- VPIP 44% (264/600), PFR 15% (90/600)`
   - `- 3-bet vs an open 6% (12/210). Facing a 3-bet: fold 25% (5/20)`
   - `- Flop: C-bets 80% (24/30). Faced a bet: fold 45% (63/140)`
   - `- Went to showdown 30% (84/280), won at showdown 50% (42/84)`
   - `- Recent: ...` the same stats over the last two ring sessions.
3. `## Preflop`: opening, limping, calling, 3-betting and 4-betting by position and stack depth, with the hands seen in each (from shown cards).
4. `## Postflop`: `**Flop**`, `**Turn**`, `**River**` and `**Raises and slowplays**`: how often they bet, call, fold and raise, and with what (from shown cards).
5. `## Bet sizing`: what their sizes mean.
6. `## Bluffing`: how often, where, and with which hands.
7. `## Heads-up`: their heads-up game (against jes and against others), counted separately from the ring stats. Omit the section entirely if they have never played heads-up.
8. `## Versus jes`: anything they do differently against jes.
9. `## Tilt and dynamics`: reactions to big losses, being bluffed, running hot, rebuys, mood.
10. `## How to exploit`: concrete adjustments, most valuable first, each with its evidence and when it applies (table size, stack depth, position).
11. `## Notable hands`: the most instructive hands, up to about eight, each one line.
12. `## Changelog`: one line per update, newest first: the date, the sessions added, and what changed in the read.

## Stats

- **Ring vs heads-up.** Key stats and the street sections count ring hands (3 or more players dealt in). Hands with 2 players dealt in count only under Heads-up. If an older total mixed in a heads-up session, the Source line says so.
- **Adding sessions.** A stat's new total is the previous total plus the new counts for that same stat. Never recompute sessions you do not have; never drop older counts.
- **Recent** means the last two ring sessions the player played, and is shown next to the total whenever it differs by about 5 points or more.
- **Every number carries its sample:** `45% (63/140)`. A percentage alone is never allowed.
- **Low confidence:** any pattern resting on fewer than about 10 examples says "low confidence".
- **Definitions** (the counting code uses exactly these):
  - VPIP: put chips in voluntarily before the flop (call, bet or raise; posting a blind does not count).
  - PFR: raised before the flop.
  - Open-raise and open-limp: the first voluntary action in a pot no one has entered.
  - 3-bet vs an open: raised when facing exactly one raise and not yet having put chips in voluntarily. Cold-call and fold to an open use the same opportunities.
  - Facing a 3-bet: made the first raise, then faced a re-raise (fold, call or 4-bet).
  - Iso-raise: raised over one or more limpers (a check or call is the alternative).
  - Limped, then faced a raise: limped, then faced a raise (call, fold or re-raise).
  - Flop c-bet: was the last preflop raiser and bet the flop when first to act or checked to.
  - Lead into the raiser: bet the flop before the preflop raiser acted on it.
  - Bets when checked to (per street): bet when first to act or checked to.
  - Faced a bet (per street): the first response to a bet on that street (fold, call or raise).
  - Went to showdown: of the hands where they saw the flop, the share that reached a showdown (two or more players left, all-in runouts included). Won at showdown: of those, the share where they collected chips.
  - Net: chips collected minus chips put in, in dollars.

## Writing

- Describe the playstyle and tendencies in plain, standard poker terms (VPIP, c-bet, check-raise, overbet, thin value, bluff-catcher). No invented labels.
- Back every read with counts or named hands. A hand is named by session label and hand number: `K112` is hand 112 of session K.
- Say whether a hand range comes from shown cards in later sessions or from the joined logs.
- When behavior changes, keep the old read, add the new one with dates and both numbers, and say which to trust now. Rewrite a read only when the evidence contradicts it, and note the rewrite in the Changelog.
- No length limit: useful detail is worth its tokens. But no repetition and no filler: merge duplicate points, keep each section organized, and cap examples at about eight per bullet (describe the rest as a range).
- No generic poker advice, and nothing that would apply to any player.
- Refer to players only by their profile names (`alex`, `rj`, `sam`, `taylor`) and to the hero as `jes`. Never write a real name, a PokerNow nickname or a display name.
- Amounts: results and net in dollars ("+12.40"); bet sizes in big blinds or as a share of the pot ("3bb", "75% pot").
- No em dashes; use a plain dash. One point per bullet.

## Exclusions

Honor every exclusion: a player's request (sam: session D) or a group decision (Oct 3 for alex).
Never count, cite or describe an excluded session, and list it under `excluded` in the frontmatter.

## Updating after a session

After a session, jes asks Claude in this project to update the profiles: the `update-profiles` skill (`.claude/skills/update-profiles/`) prepares the counts and hand records with `scripts/prepare_profile_update.py` and runs the `update-profiles` workflow, on jes's subscription and never his API credits.
Session labels and excluded games live in `data/profile-sessions.json`.

1. Take the session's exact counts, split ring and heads-up, and add them to the totals.
2. Read every hand the player played in the session (the digest), with every shown hand.
3. Update each section: confirm, sharpen or revise reads; add new patterns with their evidence; update the exploits and their order.
4. Update the frontmatter: `hands`, `sessions`, `games`, `hole_cards_known`, `net` and `last_updated`.
5. Add the Changelog line.
6. A session already listed in `games` is never added twice.
   Each session is listed with the last hand counted, `O: pglExampleGameId000000000 through hand 136`, so a game that goes on after an update (a pause on the same link) adds only the hands after it.

## Size and compaction

Profiles grow with every session, so they are kept dense rather than capped.
Claude reads every seated player's whole profile at each decision; the profiles are cached, so size costs little, but a very long prefix slows answers and buries the strongest reads.
The target is a body of at most about 6,000 words (about 8,000 tokens) per player.
Below it, add freely. Past it, the update compacts before adding, in this order:

1. **Examples:** at most about eight per bullet. Keep the most instructive and the most recent; the rest become counts ("and 14 more like it").
2. **Superseded reads:** once a behavior change is well established (two or more sessions and 10 or more examples), the old read shrinks to one line under the section, starting "Earlier:".
3. **Changelog:** the last 20 entries stay; older ones fold into one short "Earlier history" paragraph at the end.
4. **Per-session detail:** `sessions` and `net` keep the last 10 sessions by label; older ones fold into one era entry, such as `A-T 2026-09-30 to 2026-11-02 -60.10`.
5. **Notable hands:** about eight, replacing weaker ones with more instructive ones.

Never compacted away: the headline stats, the exploits that still hold, and any read with solid evidence that the latest sessions have not contradicted.
Nothing is lost by compacting: every earlier version of each profile is kept in `data/profile-history/<name>/`, and every raw game stays in `data/games/`, so detail can always be recovered and every stat recounted exactly.

## A new profile

Only when jes asks for one, under the name jes chooses.
Build it from every saved game where the chosen accounts played, following this file.
