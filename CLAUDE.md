# VAMOS - Claude Code Project Guide

VAMOS is a real-time poker assistant for PokerNow NLH cash games: a Chrome MV3 extension reads the table, a local Python backend (launched by Chrome native messaging) works out the stats, and Claude decides postflop and the close preflop spots with the user's opponent profiles in context.
The approved plan, with every design decision and the phase list, is in `~/.claude/plans/im-starting-a-new-ticklish-yao.md`; where it and this file differ, this file is current.
Current phase: P3 (Claude decides postflop and the close preflop spots; P0 to P2 are done).
`README.md` is the user's map of the folder; keep it current when folders or setup change.

## Commands

```bash
uv sync                      # install
uv run pytest                # tests (corpus checks deselected by default)
uv run python scripts/fetch_corpus.py && uv run pytest -m corpus   # parser vs ~6,600 real hands
uv run python scripts/fetch_hrc_pushfold.py && uv run pytest -m corpus   # also checks push/fold vs HRC
uv run ruff check . && uv run ruff format --check .
uv run vamos install         # register the native host with Chrome (re-run if the repo moves)
uv run vamos install --record   # test tables only: save raw log pages to data/recordings/
uv run vamos verify <game_id> <export.csv>   # live-built hands vs PokerNow's exported log
uv run python scripts/replay_preflop_routes.py   # preflop coverage on the saved games: chart, rule or Claude
uv run python scripts/replay_advice.py --dry-run # logged spots through today's prompt (without --dry-run it calls Claude and costs money, up to --budget, $3 by default)
uv run python scripts/replay_saved_preflop.py --dry-run   # saved games' Claude preflop spots through Claude (costs money without --dry-run, up to --budget, $3 by default)
uv run python scripts/gen_matchups.py && uv run python scripts/gen_preflop_tables.py   # regenerate the push/fold chart

cd extension
npm run typecheck && npm test && npm run build
npx playwright test          # e2e: throwaway Chrome for Testing + real host + fake PokerNow
node tools/test-seats.mjs <game url>   # test tables only: extra seats that play, for recordings
```

After a rebuild, reload VAMOS in `chrome://extensions` and then reload the PokerNow tab.
After a backend change, the same reload restarts the backend (Chrome launches it from this folder).

## Layout

- `src/vamos/` backend package: `cards.py` card and hand-class helpers, `money.py` chip parsing, `config.py` paths, `identity.py` the seat-to-profile map (a damaged `data/identity.json` is set aside and reported, never fatal), `profiles.py` reads `profiles/`, `jsonl.py` appends to the JSON Lines files (a line a crash cut short never swallows the next one), `verify.py` (`vamos verify`), `host/` native messaging host and installer (a message that is not JSON is logged and skipped).
- `extension/` the MV3 extension (`src/background` worker, `src/content` action card, seat badges, DOM readers and log poller, `src/sidepanel`), vitest unit tests in `test/`, the Playwright e2e suite in `e2e/`, and `tools/test-seats.mjs` (extra seats at our own test tables).
- `src/vamos/log/` the log parser: `grammar.py` (line -> event) and `hands.py` (events -> hands with chip accounting).
- `src/vamos/engine/` live decision facts and preflop answers: `state.py` (decision state, turn order, positions), `madehand.py`, `facts.py` (the action card's facts), `advice.py` (the push/fold chart, else `preflop_rules.py`); `equity.py` is the weighted equity behind the preflop matchup tables.
- `src/vamos/brain/` Claude's advice, before and after the flop: `pseudonyms.py` (names never reach the API), `prompt.py` (a short role plus the profiles of the players at the table as the cached prefix, then the hand as JSON; deliberately no strategy guide, Claude's own judgment does the rest), `provider.py` (streaming calls), `advisor.py` (background calls, deadline, stale-node cancel; every call, however it ended, goes to `data/advice.jsonl` and gets one line in `data/logs/host.log`), `answer.py` (schema and legal sizing), `spot.py` (legal actions), `costs.py` (ledger in `data/costs.jsonl`, a row per request whose usage is known).
- `src/vamos/history.py` the side panel's replayer: this session's finished hands, and one hand street by street with what VAMOS showed at each of the hero's decisions (recorded by the server in `data/games/<game>.decisions.jsonl`, with when the card first showed each decision and when its advice changed) before the hero's action. Hands from before that record began fall back on the game's Claude calls in `data/advice.jsonl` (Claude's answers only, names mapped back by starting stack).
- `src/vamos/session.py` one game: the live log rows, the hands rebuilt from them, and this session's read per player (`reads()`), which goes to Claude with each opponent and to the side panel. The rows are appended to `data/games/<game>.jsonl` (every game is kept, for the user to analyze), so a restarted backend picks the session up where it was; on (re)start the content script asks the backend where to resume (`resume_request`, passed on by the service worker only from the one tab per game that polls, `extension/src/background/tabs.ts`) and the poller walks back to that row; when the backend holds nothing of the game, the poller walks the whole log back to the game's start with `before_at`, in turns with the live polls.
- `src/vamos/preflop/` the heads-up push/fold chart: `lookup.py` and `tables.py` load it at the table, with the hand-class constants in `matchups.py` and the positions in `tree.py`; `payoffs.py`, the solving code in `solver.py` and the tree building only regenerate and check it. `docs/preflop.md` says what answers each preflop spot and how each part was checked; read it before changing the chart or the rules.
- `scripts/` developer tools, one purpose each (listed under Commands): corpus fetchers, the push/fold generators, and the three replays.
- `docs/pokernow.md` verified facts about PokerNow's log endpoint, log lines and DOM; `docs/log-grammar.md` the full line grammar with evidence. Read both before touching parsing or DOM code.
- `tests/` pytest suite, one test file per module.
- `profiles/` the opponent profiles, owned by VAMOS and written only under the rulebook `profiles/_GUIDELINES.md` (the one tracked file there); every earlier version is in `data/profile-history/<name>/`.
- `drafts/` the side panel's Save draft output (`<date>-<name>.md`), gitignored except its `README.md`; the user reviews and merges them by hand.
- `data/` VAMOS's own local state, gitignored and never pruned: identity map, advice log, cost ledger, logs, `settings.json` (the dial, the AI switch `aiOn`, the table switches `showCard` and `showBadges`, and the hero's own names to keep out of prompts), `games/` with each game's log rows, per-player notes (`<game>.notes.json`) and the advice shown at each of the hero's decisions (`<game>.decisions.jsonl`), `profile-history/<name>/` with every version of each profile, and when used `recordings/`, `replays/`, `reports/` and `test-seats/`.

## Rules

### Scope
- VAMOS only reads and displays. It never clicks, types into, or otherwise operates PokerNow.
- VAMOS owns the opponent profiles (the user's decision, 2026-10-07): they are the source of truth about each player, kept current after sessions from the saved games and written only under `profiles/_GUIDELINES.md`, with every earlier version kept in `data/profile-history/<name>/`. A profile is created only when the user asks, under the name the user chooses. The profile loader skips files that start with `_`.
- Save draft writes this session's read and notes to `drafts/` only; it never creates or edits a profile.
- This session's read is counted from the current game's log only; it is the one stats count in v1. "This session" API cost runs back to the last 3-hour pause between calls, so restarts do not reset it.
- The extension closes the backend 15 s after the last table tab goes away, so a refresh keeps the same backend.
- The action card always sits at the top middle of the table, just above the pot, even over a seat.
- Learning happens only through the profiles: exact per-player counts from the saved games plus written reads, under the rulebook. No other stats engine or automatic learning. Deferred features (range tables, HRC tables, review mode's adherence stats, training mode) are built only when the user asks; the hand replayer was built on request (2026-10-05).
- Claude runs on Opus 5.5 only, before and after the flop: effort low by default, medium as the deeper option. Sonnet 5.5 and Haiku 4.5 were dropped after blind comparisons on logged spots; never Claude Fable 5.1.
- Ask the user before any non-trivial design choice the plan does not cover.

### Correctness
- Amounts are parsed with `money.parse_chips` into integer cents; never use `float()` on PokerNow values. It returns `None` for non-amounts such as "All In"; handle `None` explicitly.
- Every amount the user sees reads in dollars and big blinds, "$0.60 (3bb)" (`facts.format_money`; sizes in Claude's text get the same through `advisor.with_dollars`), and a call shows what it adds. Claude is sent big blinds only.
- Cards are two-character strings ("As", "Th"). Convert log cards with `cards.parse_log_card`.
- Log amounts are the player's street total, not the increment. Unrecognized lines become `unknown` events and are surfaced (each logged once in `data/logs/host.log`, and counted for the game in the side panel), never guessed; extend the grammar only from real examples.
- The PokerNow game log is the authoritative action stream; the DOM is only for whose turn it is, legal actions, hero cards, seats and the countdown.
- DOM seat classes (`table-player-N`) are hero-relative positions, not physical seats; join DOM to log by player ID (`/players/<id>` link).
- Respect PokerNow's rate limit (about one log request per 2s per IP): back off on every failed poll (honoring `Retry-After` up to 5 minutes) and say why on the card and in the side panel; never add parallel pollers (one tab per game polls).
- Never advise on stale state: advice is stamped with its decision node and only shown while that node is current.
- Every piece of advice shows its source on the card: `Preflop chart` (heads-up push/fold), `Preflop rule` (a clear fold, check, call, raise or all in), `VAMOS! AI (Claude Opus 5.5 · low)` (Claude, before or after the flop, always naming the model and effort), and plain `VAMOS!` while the hand is being read. The card's numbers are "stats" wherever the user reads them (for example "AI off: stats only"); never "facts" or "math". Fallbacks are never silent: a failed AI call says why on the card.
- Colors stay calm: only what an action says is colored, along the rainbow by how much money it puts in (orange-red fold, yellow call, blue bet, purple raise, pink all in; check stays neutral), and only on the action and the option drawn. Where advice came from is told by its label, never a color. Status (connection, warnings, errors, won or lost) keeps its own green, yellow and red; the wordmark uses the action colors as a gradient. The card (`overlay.css`) and the side panel (`panel.css`) define the same tokens.
- "Ask deeper" adds its answer under the first one in the side panel; an earlier answer for the same decision is never replaced, and one still thinking keeps running (both are billed and logged) so the two can be compared. The card shows the deeper answer once it comes. A backend error on a spot shows on the card instead of "reading the hand...".
- Breakages say so: a problem with the API key or credits stays in the side panel's header until a call gets through, a backend Chrome cannot start shows the fix (`uv run vamos install`) on the card and in the side panel, and controls that need the backend are paused with the reason while it is not ready.
- Prompts use pseudonyms; no real name, alias or PokerNow nickname may reach the API.
- Label approximations honestly where they are shown, and back claims with tests or sources.
- Never call eval7's range helpers directly: `py_hand_vs_range_exact` does not deal the turn and river, and both it and `py_hand_vs_range_monte_carlo` silently ignore combo weights. Use `vamos.engine.equity.equity`, which is tested against brute force.

### Style
- Match the surrounding code; comments only for non-obvious why.
- No em dashes anywhere; use a plain dash.
- In the UI and drafts, say "current session" or "this session", never "tonight" (games are not always at night).
- In long Markdown files, one sentence per line.
- Commit messages never include an AI co-author line. Prefer new commits over amending; check `git status` first.
- Never force-push, hard-reset, or delete an unmerged branch without the user's explicit approval. Anything confirmed stale (merged branches, leftover worktrees, dead code, superseded docs, test leftovers) is cleaned up without asking; when unsure, keep it and say so.
- Run `uv run pytest` and ruff before reporting a task complete.
