# VAMOS

VAMOS is a real-time poker assistant for [PokerNow](https://www.pokernow.com) No-Limit Hold'em cash games.
It reads the table from PokerNow's own game log, does the math in code, and asks Claude for postflop decisions with your opponent profiles in context.
It is the successor to PAIv1.

Status: early development (P0 bootstrap). Nothing is playable yet.

## How it will work

- A Chrome extension in your everyday Chrome reads the PokerNow table and shows an action card, seat badges and a side panel.
- Opening a PokerNow table launches the local Python backend through Chrome native messaging; nothing else to start.
- Preflop advice comes from precomputed baseline tables, instantly and for free.
- Postflop advice comes from Claude (Sonnet by default, Opus on demand), with every piece of advice labeled by its source.
- VAMOS only reads and displays; it never clicks or types into PokerNow.

## Profiles

Opponent profiles are Markdown files you write and maintain yourself in `profiles/`, one per person.
They are gitignored; only `profiles/_TEMPLATE.md` and `profiles/_PROMPT.md` (a prompt for generating profiles in a separate AI chat) are tracked.
VAMOS reads profiles but never edits them, and never learns from anything on its own.

## Development

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js and Google Chrome.

```bash
uv sync
uv run pytest
uv run ruff check .
```
