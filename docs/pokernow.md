# PokerNow reference

What VAMOS relies on about PokerNow, verified in P0 against a live guest table (2026-10-02) and PokerNow's production bundle, plus open-source trackers where noted.
PokerNow publishes none of this; re-verify when something breaks.

## Navigation

- Game pages are `https://www.pokernow.com/games/<id>`; bare `pokernow.com` and the legacy `pokernow.club` redirect there.
- Creating a game from `/start-game` swaps the URL to `/games/<id>` with `history.replaceState`, without a page load, so Chrome never injects content scripts for it.
  VAMOS injects on every PokerNow page and watches `location.pathname`.
- Endpoints live under the same prefix (`/games/<id>/log`, CSV exports), so the game-page match is anchored: `^/games/([^/?#]+)/?$`.

## Live log endpoint

- `GET /games/<id>/log[?after_at=X][&before_at=Y]`, same origin, using the page's cookies.
- Response: `{"logs": [{"at": ISO time, "created_at": "179092346302103", "msg": "..."}], "infos": {...}}`.
- Rows are newest first, at most 50 per response.
- `created_at` is a string: epoch milliseconds x 100 plus a sequence number; it is the same value as the CSV export's `order` column.
- `after_at` and `before_at` take `created_at` values (not milliseconds) and are both exclusive.
  `after_at` filters to the newest 50 rows above X; it does not page forward, so a burst of more than 50 rows needs backward paging with `before_at`.
- Rate limit: roughly one request per 2 seconds per IP.
  Polling every 1.5s produced a 429 about every 30 seconds; extra requests from another tab on the same IP made most of them fail.
  Back off on 429 and 503 and honor `Retry-After`.

## Log lines

Observed live (two consecutive hands, in log order):

```
The player "Hero @ heroid0001" requested a seat.
The admin approved the player "Hero @ heroid0001" participation with a stack of 1000.
-- starting hand #1 (id: ywhe4rpsfvih)  No Limit Texas Hold'em (dealer: "TestVillain @ FKxeoYccvW") --
The player "TestVillain @ FKxeoYccvW" joined the game with a stack of 1000.
The player "Hero @ heroid0001" joined the game with a stack of 1000.
Player stacks: #1 "TestVillain @ FKxeoYccvW" (1000) | #2 "Hero @ heroid0001" (1000)
Your hand is 3♠, 4♥
"TestVillain @ FKxeoYccvW" posts a small blind of 10
"Hero @ heroid0001" posts a big blind of 20
"TestVillain @ FKxeoYccvW" folds
Uncalled bet of 10 returned to "Hero @ heroid0001"
"Hero @ heroid0001" collected 20 from pot
-- ending hand #1 --
-- starting hand #2 (id: 31q3mb1wzm0w)  No Limit Texas Hold'em (dealer: "Hero @ heroid0001") --
Player stacks: #1 "TestVillain @ FKxeoYccvW" (990) | #2 "Hero @ heroid0001" (1010)
Your hand is 4♠, 10♠
"Hero @ heroid0001" posts a small blind of 10
"TestVillain @ FKxeoYccvW" posts a big blind of 20
"Hero @ heroid0001" calls 20
```

- Amounts are the player's street total: in hand #2 the small blind's `calls 20` adds 10. The full grammar, verified against thousands of real hands, is in `docs/log-grammar.md`.
- Players are always `"Name @ ID"`; the ID survives nickname changes.
- Tens are written `10`, e.g. `10♠`.
- "Your hand is" rows are private: each viewer sees only their own, and the other players' rows are missing from the sequence (e.g. `...103` then `...105`). Gaps are normal, not lost data.
- When the decision timer runs out, the log records an ordinary fold.
- `Player stacks: #N` uses physical seat numbers.

## DOM

- Seats: `.table-player.table-player-<P>`; `<P>` is a visual position rotated so the hero sits at 1, not the physical seat.
  Join DOM seats to log players by the player link `a[href="/players/<id>"]`, never by `<P>`.
- Classes on a seat: `you-player` (the local user), `decision-current` (whoever must act), `fold`, `offline`, `winner`.
- Dealer: `.dealer-button-ctn.dealer-position-<P>`, in the same rotated frame as the seats.
- Pot: `.table-pot-size .main-value` excludes the current street's bets; the `total` add-on includes them.
- Chip values: read `span.chips-value > span.normal-value`; big-blind display mode adds a `bb-value` span.
- Action buttons: `.game-decisions-ctn .action-button.{call,raise,check,fold}`; inapplicable ones are rendered but `disabled`, and `call` only exists when calling is legal.
  "Your Turn" is `p.action-signal`.
- Decision timer: `.decision-current .time-to-talk` holds `.normal-time` and `.time-bank` bars whose inline `width` is the percentage left; it redraws every 100ms.
  Seconds are not in the DOM; derive them from how fast the bar shrinks.

## Chrome and macOS

- Chrome will not start a native messaging host whose executable sits in a privacy-protected folder such as `~/Desktop`; `vamos install` puts the launcher in `~/Library/Application Support/VAMOS/`.
- Content scripts are never injected into tabs that were open before the extension was loaded or reloaded; reload the PokerNow tab after reloading the extension.
- An open native messaging port keeps the service worker alive; content and panel ports do not, so both reconnect when the worker is stopped.
