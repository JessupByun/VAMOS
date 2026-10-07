# VAMOS

VAMOS is a real-time poker assistant for [PokerNow](https://www.pokernow.com) No-Limit Hold'em cash games.
It reads the table from PokerNow's own game log, works out the stats in code, and asks Claude for the decisions that need judgment, with your opponent profiles in context.
VAMOS only reads and displays; it never clicks or types into PokerNow.

## Your folders

| Folder | What you do there |
|---|---|
| `profiles/` | One `<name>.md` per opponent, written and updated by you. Start from `_TEMPLATE.md`; `_PROMPT.md` is the prompt for generating one in a separate AI chat. VAMOS reads these and never changes them. |
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
- The side panel's Current hand has a "Replay an earlier hand" menu: pick one of this session's hands to open the hand replayer, street by street, with what VAMOS advised next to what you did (x closes it).
  In the menu the session's biggest pots are bold with their pot, results are green or red, and hands where you never called, bet or raised are dimmed.
  Hands are kept on this Mac only, in `data/games/` (under 1 MB per session), and never deleted, so every game stays there to analyze later; the replayer makes no AI calls.
- The side panel's switches: AI Advice (with it off no Claude call is made; the preflop rules and chart still answer), VAMOS! HUD (the card on the table) and Per-Player Stats (the badges under each seat).
- The side panel's API costs count the Claude calls VAMOS records (`data/costs.jsonl`).
  The Anthropic Console is exact: it counts anything else that uses your API key, and any charge for calls VAMOS stopped before Claude's first reply (whether those are billed is unverified; VAMOS logs them in `data/advice.jsonl` without a cost).
- Problems are said, never hidden: the side panel counts the log lines VAMOS could not read this session (each one is in `data/logs/host.log`), and if `data/identity.json` (your seat matches) is ever damaged, VAMOS keeps it aside as `identity.damaged-<date>-<time>.json`, starts without it and says so under Players.
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
