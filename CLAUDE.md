# VAMOS - Claude Code Project Guide

VAMOS (PAIv2) is a real-time poker assistant for PokerNow NLH cash games: a Chrome MV3 extension reads the table, a local Python backend (launched by Chrome native messaging) does the math, and Claude gives postflop advice with the user's opponent profiles in context.
The approved plan, with every design decision and the phase list, is in `~/.claude/plans/im-starting-a-new-ticklish-yao.md`.
Current phase: P0 (bootstrap + spikes).

## Commands

```bash
uv sync                      # install
uv run pytest                # tests (slow and integration tests deselected by default)
uv run pytest -m slow        # long reference checks
uv run python scripts/fetch_corpus.py && uv run pytest -m corpus   # parser vs ~6,600 real hands
uv run ruff check . && uv run ruff format --check .
uv run vamos install         # register the native host with Chrome (re-run if the repo moves)
uv run vamos install --record   # test tables only: save raw log pages to data/recordings/
uv run vamos verify <game_id> <export.csv>   # live-built hands vs PokerNow's exported log

cd extension
npm run typecheck && npm run build
npx playwright test          # e2e: throwaway Chrome for Testing + real host + fake PokerNow
```

After a rebuild, reload VAMOS in `chrome://extensions` and then reload the PokerNow tab.

## Layout

- `src/vamos/` backend package (`cards.py` card + hand-class helpers, `money.py` chip parsing, `host/` native messaging host and installer).
- `extension/` the MV3 extension (`src/background` worker, `src/content` pill + log poller, `src/sidepanel`) and its Playwright e2e suite in `e2e/`.
- `src/vamos/log/` the log parser: `grammar.py` (line -> event) and `hands.py` (events -> hands with chip accounting).
- `docs/pokernow.md` verified facts about PokerNow's log endpoint, log lines and DOM; `docs/log-grammar.md` the full line grammar with evidence. Read both before touching parsing or DOM code.
- `tests/` pytest suite, one test file per module.
- `profiles/` the user's opponent profiles, gitignored except `_TEMPLATE.md` and `_PROMPT.md`.
- `data/` VAMOS's own local state (identity map, advice log, cost ledger, logs), gitignored.

## Rules

### Scope
- VAMOS only reads and displays. It never clicks, types into, or otherwise operates PokerNow.
- Profiles belong to the user. VAMOS reads `profiles/*.md` (skipping files that start with `_`) and never writes them.
- No log ingestion, stats engine, or automatic learning in v1. Deferred features (AI preflop, range tables, HRC tables, review mode, training mode) are built only when the user asks.
- Postflop models are Claude Haiku 4.5, Sonnet 5.5 (default) and Opus 5.5 only. Never Claude Fable 5.1.
- Ask the user before any non-trivial design choice the plan does not cover.

### Correctness
- Amounts are parsed with `money.parse_chips` into integer cents; never use `float()` on PokerNow values. It returns `None` for non-amounts such as "All In"; handle `None` explicitly.
- Cards are two-character strings ("As", "Th"). Convert log cards with `cards.parse_log_card`.
- Log amounts are the player's street total, not the increment. Unrecognized lines become `unknown` events and are surfaced, never guessed; extend the grammar only from real examples.
- The PokerNow game log is the authoritative action stream; the DOM is only for whose turn it is, legal actions, hero cards, seats and the countdown.
- DOM seat classes (`table-player-N`) are hero-relative positions, not physical seats; join DOM to log by player ID (`/players/<id>` link).
- Respect PokerNow's rate limit (about one log request per 2s per IP): back off on 429/503, never add parallel pollers.
- Never advise on stale state: advice is stamped with its decision node and only shown while that node is current.
- Every piece of advice shows its source (`VAMOS · Sonnet`, `VAMOS · Opus`, `INSTANT · math only`); fallbacks are never silent.
- Prompts use pseudonyms; no real name, alias or PokerNow nickname may reach the API.
- Label approximations honestly ("approximate", "vs standard range"), and back claims with tests or sources.
- Never call eval7's range helpers directly: `py_hand_vs_range_exact` does not deal the turn and river, and both it and `py_hand_vs_range_monte_carlo` silently ignore combo weights. Use `vamos.engine.equity.equity`, which is tested against brute force.

### Style
- Match the surrounding code; comments only for non-obvious why.
- No em dashes anywhere; use a plain dash.
- In long Markdown files, one sentence per line.
- Commit messages never include an AI co-author line. Prefer new commits over amending; check `git status` first.
- Never force-push, hard-reset, or delete branches without the user's explicit approval.
- Run `uv run pytest` and ruff before reporting a task complete.
