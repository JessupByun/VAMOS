# VAMOS

VAMOS is a real-time poker assistant for [PokerNow](https://www.pokernow.com) No-Limit Hold'em cash games.
It reads the table from PokerNow's own game log, does the math in code, and asks Claude for the decisions that need judgment, with your opponent profiles in context.
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
| `data/` | VAMOS's own state: saved games (kept 3 days), settings, seat matches, costs and logs. The one thing to edit by hand is `heroNames` in `data/settings.json`: your real name and nicknames, which are replaced with "Hero" before anything reaches Claude. |
| `docs/` | Reference notes: PokerNow's log and page, and how preflop advice works. |
| `scripts/` | Developer tools. |
| `tests/` | The test suite. |

## How it works

- Preflop, heads-up at 10bb or less: a push/fold chart (`Preflop chart` on the card).
- Preflop, clear spots: simple rules (`Preflop rule`). Trash folds when 3 or more players are dealt in and the price is normal or steeper (not blind against blind, not facing an all-in); the strongest hands raise.
- Everything else, before or after the flop: Claude Opus 5.5 at low effort (`VAMOS! AI (Claude Opus 5.5 · low)`), with Ask deeper for a medium-effort second answer.
- The side panel's switches turn AI advice, the card and the badges on or off; with AI off no Claude call is made.
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
