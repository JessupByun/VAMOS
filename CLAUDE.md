# VAMOS - Claude Code Project Guide

VAMOS (PAIv2) is a real-time poker assistant for PokerNow NLH cash games: a Chrome MV3 extension reads the table, a local Python backend (launched by Chrome native messaging) does the math, and Claude gives postflop advice with the user's opponent profiles in context.
The approved plan, with every design decision and the phase list, is in `~/.claude/plans/im-starting-a-new-ticklish-yao.md`.
Current phase: P3 (the postflop brain; P0 to P2 are done, and preflop is frozen as good enough).

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
uv run python scripts/fetch_hrc_pushfold.py && uv run pytest -m corpus   # also checks push/fold vs HRC

cd extension
npm run typecheck && npm run build
npx playwright test          # e2e: throwaway Chrome for Testing + real host + fake PokerNow
```

After a rebuild, reload VAMOS in `chrome://extensions` and then reload the PokerNow tab.

## Layout

- `src/vamos/` backend package (`cards.py` card + hand-class helpers, `money.py` chip parsing, `host/` native messaging host and installer).
- `extension/` the MV3 extension (`src/background` worker, `src/content` pill + log poller, `src/sidepanel`) and its Playwright e2e suite in `e2e/`.
- `src/vamos/log/` the log parser: `grammar.py` (line -> event) and `hands.py` (events -> hands with chip accounting).
- `src/vamos/engine/` live decision facts: equity, decision state and turn order, made-hand text, the action card's facts.
- `src/vamos/brain/` Claude's postflop advice: `pseudonyms.py` (names never reach the API), `prompt.py` (a short role plus the profiles as the cached prefix, then the hand as JSON; deliberately no strategy guide, Claude's own judgment does the rest), `provider.py` (streaming calls), `advisor.py` (background calls, deadline, stale-node cancel), `answer.py` (schema and legal sizing), `spot.py` (legal actions), `costs.py` (ledger in `data/costs.jsonl`).
- `src/vamos/session.py` one game: the live log rows, the hands rebuilt from them, and tonight's read per player (`reads()`), which goes to Claude with each opponent and to the side panel. The rows are appended to `data/games/<game>.jsonl` (pruned after 3 days), so a restarted backend picks the night up where it was; on (re)start the content script asks the backend where to resume (`resume_request`) and the poller walks back to that row; when the backend holds nothing of the game, the poller walks the whole log back to the game's start with `before_at`, in turns with the live polls.
- `src/vamos/preflop/` the home-built preflop solver and its generated tables in `data/` (committed); `docs/preflop.md` lists every approximation with its measured size. Read it before changing the solver.
- `docs/pokernow.md` verified facts about PokerNow's log endpoint, log lines and DOM; `docs/log-grammar.md` the full line grammar with evidence. Read both before touching parsing or DOM code.
- `tests/` pytest suite, one test file per module.
- `profiles/` the user's opponent profiles, gitignored except `_TEMPLATE.md` and `_PROMPT.md`.
- `data/` VAMOS's own local state (identity map, advice log, cost ledger, logs, `settings.json` with the dial and the hero's own names to keep out of prompts, `drafts/` from the side panel's Save draft, `games/` with each game's log rows), gitignored. `settings.json` also holds the AI switch (`aiOn`: off makes no Claude calls) and the table switches (`showCard`, `showBadges`). Per-player notes are saved per game in `games/<game>.notes.json`.

## Rules

### Scope
- VAMOS only reads and displays. It never clicks, types into, or otherwise operates PokerNow.
- Profiles belong to the user. VAMOS reads `profiles/*.md` (skipping files that start with `_`) and never writes them.
- Save draft writes tonight's read and notes to `data/drafts/` only; it never creates or edits a profile, and nothing merges drafts automatically. Updating profiles from inside VAMOS stays off: the user merges by hand from full logs.
- Tonight's read is counted from the current game's log only; it is the one stats count in v1. "Tonight" API cost runs back to the last 3-hour pause between calls, so restarts do not reset it.
- The extension closes the backend 15 s after the last table tab goes away, so a refresh keeps the same backend.
- The action card always sits at the top middle of the table, just above the pot, even over a seat.
- No log ingestion, stats engine, or automatic learning in v1. Deferred features (AI preflop, range tables, HRC tables, review mode, training mode) are built only when the user asks.
- Postflop runs on Claude Opus 5.5 only: effort low by default, medium as the deeper option. Sonnet 5.5 and Haiku 4.5 were dropped after blind comparisons on logged spots; never Claude Fable 5.1.
- Ask the user before any non-trivial design choice the plan does not cover.

### Correctness
- Amounts are parsed with `money.parse_chips` into integer cents; never use `float()` on PokerNow values. It returns `None` for non-amounts such as "All In"; handle `None` explicitly.
- Cards are two-character strings ("As", "Th"). Convert log cards with `cards.parse_log_card`.
- Log amounts are the player's street total, not the increment. Unrecognized lines become `unknown` events and are surfaced, never guessed; extend the grammar only from real examples.
- The PokerNow game log is the authoritative action stream; the DOM is only for whose turn it is, legal actions, hero cards, seats and the countdown.
- DOM seat classes (`table-player-N`) are hero-relative positions, not physical seats; join DOM to log by player ID (`/players/<id>` link).
- Respect PokerNow's rate limit (about one log request per 2s per IP): back off on 429/503, never add parallel pollers.
- Never advise on stale state: advice is stamped with its decision node and only shown while that node is current.
- Every piece of advice shows its source on the card: `Preflop chart` (rule-based), `VAMOS! AI (Claude Opus 5.5 · low)` (postflop, always naming the model and effort), and plain `VAMOS!` while the hand is being read; the word "Math" is not used. Fallbacks are never silent: a failed AI call says why on the card.
- Colors stay calm: only what an action says is colored, along the rainbow by how much money it puts in (orange-red fold, yellow call, blue bet, purple raise, pink all in; check stays neutral), and only on the action and the option drawn. Where advice came from is told by its label, never a color. Status (connection, warnings, errors, won or lost) keeps its own green, yellow and red; orange is only the wordmark. The card (`overlay.css`) and the side panel (`panel.css`) define the same tokens.
- "Ask deeper" adds its answer under the first one in the side panel; an earlier answer for the same decision is never replaced. A backend error on a spot shows on the card instead of "reading the hand...".
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
