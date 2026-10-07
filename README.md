# VAMOS

VAMOS is a real-time poker assistant for [PokerNow](https://www.pokernow.com) No-Limit Hold'em cash games.
It reads the table from PokerNow's own game log, works out the stats in code, and asks Claude for the decisions that need judgment, with your opponent profiles in context.
VAMOS only reads and displays; it never clicks or types into PokerNow.

## Your folders

| Folder | What you do there |
|---|---|
| `profiles/` | One `<name>.md` per opponent. VAMOS owns these and keeps them up to date from your games, following the rulebook in `_GUIDELINES.md`: exact stats, written reads on each player, and the exploits that work against them. Every earlier version is kept in `data/profile-history/<name>/`. |
| `drafts/` | What the side panel's Save draft writes: one file per player and game, such as `2026-10-05-alex.md`. Saving again in the same game replaces it, so make your edits in `profiles/`: review the draft, merge what is useful into that player's profile, then delete it. |
| `.env` | Your `ANTHROPIC_API_KEY` (see `.env.example`). Never shared or committed. |

Everything else is maintained by Claude:

| Folder | What it is |
|---|---|
| `src/` | The backend that Chrome starts when a PokerNow table opens. |
| `extension/` | The Chrome extension: the card and badges on the table, and the side panel. |
| `data/` | VAMOS's own state: saved games (all kept, for analyzing later), settings, seat matches, a record of every Claude call (`advice.jsonl`), costs and logs. The one thing to edit by hand is `heroNames` in `data/settings.json`: your real name and nicknames, which are replaced with "Hero" before anything reaches Claude. |
| `docs/` | Reference notes: PokerNow's log and page, and how preflop advice works. |
| `scripts/` | Developer tools. |
| `tests/` | The test suite. |

## How it works

- Preflop, heads-up at 10bb or less: a push/fold chart (`Preflop chart` on the card).
- Preflop, clear spots: simple rules (`Preflop rule`). Trash folds when 3 or more players are dealt in and the price is normal or steeper (not blind against blind, not facing an all-in); the strongest hands raise.
- Everything else, before or after the flop: Claude Opus 5.5 at low effort (`VAMOS! AI (Claude Opus 5.5 · low)`), with Ask deeper for a medium-effort second answer.
  Both answers run to the end and show one under the other in the side panel, to compare; the card shows the deeper one once it comes.
  Asked before the first answer has begun to arrive, Ask deeper waits for that moment (usually a second or two), so it reads your opponents' profiles from Claude's prompt cache instead of paying to cache them a second time.
  With less than about 10 seconds left for an answer, Ask deeper still runs but says the deeper answer may not arrive in time.
- The side panel's Current hand has a "Replay an earlier hand" menu: pick one of this session's hands to open the hand replayer, street by street, with what VAMOS advised next to what you did (x closes it).
  In the menu the session's biggest pots are bold with their pot, results are green or red, and hands where you never called, bet or raised are dimmed.
  The filter beside the menu lists all hands, the ones you played (a call, bet or raise of your own), big pots, AI decisions (VAMOS's AI answered at least once) or showdowns (anyone showed cards); the menu's count follows it, and it stays while the side panel is open.
  The replayer's previous and next buttons step to the older and newer hand in the menu.
  Hands are kept on this Mac only, in `data/games/` (under 1 MB per session), and never deleted, so every game stays there to analyze later; the replayer makes no AI calls.
- The side panel's switches: AI Advice (with it off no Claude call is made; the preflop rules and chart still answer), VAMOS! HUD (the card on the table) and Per-Player Stats (the badges under each seat).
  While the backend is not running, the switches, the model, notes, profile matches and Save draft are paused and say why.
- Notes on a player keep up to 2,000 characters: the field counts them near the limit and says when text was cut.
- The side panel's API costs (This session, This month, All time) count VAMOS's Claude calls at the table (`data/costs.jsonl`), read again every minute and when a setting changes.
  A call stopped before Claude's first reply (you acted first, or its time ran out) never gets Claude's token counts, so it is estimated from the latest call with the same opponents' profiles (its input and cached tokens, without output), and a note under the figures says how much of them is estimated.
  With no such call to go by, a stopped call counts $0 and the note says so.
  The developer's test replays (`scripts/replay_advice.py`, `scripts/replay_saved_preflop.py`) stay in `data/costs.jsonl` but are left out of all three figures.
  The Anthropic Console is exact: it counts anything else that uses your API key, test replays included.
- Problems are said, never hidden: when PokerNow's log stops answering, the card and the side panel say why and when VAMOS tries again (for example "PokerNow's log is not answering (HTTP 404), retrying in 8 s"); the side panel counts the log lines VAMOS could not read this session (each one is in `data/logs/host.log`), and if `data/identity.json` (your seat matches) is ever damaged, VAMOS keeps it aside as `identity.damaged-<date>-<time>.json`, starts without it and says so under Players.
  A missing or rejected API key, or running out of API credits, shows as a yellow line at the top of the side panel until a call gets through again.
  When Chrome cannot start the backend, the card and the side panel say to run `uv run vamos install` in this folder and then reload VAMOS.
  A Save draft or a hand replay that fails says why, or that the backend did not answer.
- A game open in two tabs is read from one of them, so PokerNow is asked no more often than with one tab; the other takes over when that tab closes or reloads, and when you switch to the other tab (Chrome slows down tabs you are not looking at) or your turn shows there while the reading tab is out of sight.
- Amounts show in dollars and big blinds, e.g. `RAISE TO $0.60 (3bb)`.
- Claude sees opponents only under pseudonyms (Player A, Player B, ...); no real name or nickname is sent.

## Setup (once)

```bash
uv sync
uv run vamos install
cd extension && npm ci && npm run build
```

Then load `extension/dist` as an unpacked extension in `chrome://extensions`, put your API key in `.env`, and open a PokerNow table.

## After an update

Reload VAMOS in `chrome://extensions`, then reload the PokerNow tab.
That also restarts the backend, which Chrome runs from this folder.
