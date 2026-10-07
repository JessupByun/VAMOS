import type { LogPage, LogTrouble } from "../../shared/protocol";

export const POLL_MS = 2000;
export const MAX_BACKOFF_MS = 30000;
/** The longest wait a Retry-After from PokerNow is honored for. */
export const MAX_RETRY_AFTER_MS = 5 * 60 * 1000;
/** The closest two requests may be when one is pulled forward with `soon()`. */
export const MIN_GAP_MS = 1000;
/**
 * PokerNow's most rows per response (docs/pokernow.md): a live page this long may have been
 * cut short, so the rows below it are walked back. A walk ends on an empty page, not a short one.
 */
export const PAGE_SIZE = 50;
// One slow-down from PokerNow is routine (docs/pokernow.md) and over by the next poll, so a
// wait this short is not reported; a second in a row, or a longer Retry-After, is.
const QUIET_WAIT_MS = 2 * POLL_MS;

interface LogBody {
  logs: { created_at: string }[];
}

export interface LogPoller {
  stop: () => void;
  /**
   * Start polling, or start over: from `after` (the newest row the backend already holds), or
   * from the newest rows when it holds none; rows above it are walked back to it, so none are
   * skipped.
   */
  resume: (after: string | null) => void;
  /** Poll nothing until the next resume(), e.g. while the backend is away. */
  pause: () => void;
  /** Poll as soon as the minimum gap allows, e.g. when the hero's turn starts. */
  soon: () => void;
}

/** Rows strictly between `floor` and `before` still need fetching; then the cursor moves to `target`. */
interface Backfill {
  floor: string;
  before: string;
  target: string;
}

type Outcome = { body: LogBody } | { problem: string; busy: boolean; retryAfter: number };

/** Retry-After in milliseconds: seconds, or an HTTP date; 0 when absent or unreadable. */
function retryAfterMs(header: string | null): number {
  if (!header) return 0;
  const seconds = Number(header);
  const ms = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(header) - Date.now();
  return Number.isFinite(ms) ? Math.max(0, ms) : 0;
}

function isLogBody(body: unknown): body is LogBody {
  const logs = (body as { logs?: unknown } | null)?.logs;
  return (
    Array.isArray(logs) &&
    logs.every((row) => /^\d+$/.test(String((row as { created_at?: unknown } | null)?.created_at)))
  );
}

/** One request to the log: its rows, or why there are none to use, in plain words. */
async function request(url: string): Promise<Outcome> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: "same-origin" });
  } catch {
    return { problem: "PokerNow's log could not be reached (network error)", busy: false, retryAfter: 0 };
  }
  if (!response.ok) {
    const busy = response.status === 429 || response.status === 503;
    return {
      problem: `PokerNow's log is ${busy ? "busy" : "not answering"} (HTTP ${response.status})`,
      busy,
      retryAfter: retryAfterMs(response.headers.get("retry-after")),
    };
  }
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { problem: "PokerNow's log sent a reply VAMOS cannot read (not JSON)", busy: false, retryAfter: 0 };
  }
  if (!isLogBody(body)) {
    return { problem: "PokerNow's log sent a reply VAMOS cannot read (no log rows)", busy: false, retryAfter: 0 };
  }
  return { body };
}

// PokerNow's undocumented log endpoint (same origin, uses the page's cookies), rate-limited
// to roughly one request per 2s per IP. Rows come newest first; `after_at` and `before_at`
// take `created_at` values (epoch ms x 100 plus a sequence number) and are exclusive.
// `after_at` returns only the newest page above the cursor, so when a full page comes back
// the rows below it are walked back with `before_at` before the cursor may move, one request
// per tick. Pages are forwarded untouched; the backend owns parsing and dedup. Failures back
// off and are reported through `report` until a page comes back again.
export function startLogPoller(
  gameId: string,
  deliver: (page: LogPage) => boolean,
  report: (trouble: LogTrouble | null) => void = () => {},
): LogPoller {
  let delay = POLL_MS;
  let cursor: string | null = null;
  let backfill: Backfill | null = null;
  // The game's older rows, walked back to its start from the oldest row fetched so far: done
  // when the backend holds nothing of this game (VAMOS opened mid-game), in turns with the
  // live polls, so this session's numbers fill in while the hero's turns stay current.
  let history: string | null = null;
  let wantHistory = false;
  let historyNext = false;
  // Bumped by resume() and pause(), so a response to a request made before either cannot move
  // the cursor.
  let generation = 0;
  let running = false;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight = false;
  let lastStart = -Infinity;
  let trouble: LogTrouble | null = null;

  function setTrouble(next: LogTrouble | null) {
    if (next?.problem === trouble?.problem && next?.retryAt === trouble?.retryAt) return;
    trouble = next;
    report(next);
  }

  function query(): string {
    if (historyNext && history) return `?before_at=${history}`;
    if (backfill) return `?after_at=${backfill.floor}&before_at=${backfill.before}`;
    return cursor ? `?after_at=${cursor}` : "";
  }

  function advance(body: LogBody) {
    const rows = body.logs;
    const newest = rows[0]?.created_at;
    const oldest = rows.at(-1)?.created_at;
    if (newest === undefined || oldest === undefined) {
      if (backfill) {
        cursor = backfill.target;
        backfill = null;
      }
      return;
    }
    // A page that went no further back: the walk is over rather than repeated forever.
    if (backfill && BigInt(oldest) >= BigInt(backfill.before)) {
      const { floor, before } = backfill;
      console.warn(`[VAMOS] PokerNow's log did not page back below ${before}; rows after ${floor} may be missing`);
      cursor = backfill.target;
      backfill = null;
      return;
    }
    const fresh = cursor === null && backfill === null;
    // Not delivered (no backend connected): leave everything as is and ask again next tick.
    if (!deliver({ type: "log_page", gameId, fetchedAt: Date.now(), fresh, body })) return;
    if (fresh && wantHistory) {
      wantHistory = false;
      history = oldest;
    }
    if (backfill) backfill = { ...backfill, before: oldest };
    else if (cursor !== null && rows.length >= PAGE_SIZE) {
      backfill = { floor: cursor, before: oldest, target: newest };
    } else cursor = newest;
  }

  function advanceHistory(body: LogBody) {
    const oldest = body.logs.at(-1)?.created_at;
    // The start of the game, or a page that went no further back: the walk is over.
    if (oldest === undefined || history === null || BigInt(oldest) >= BigInt(history)) {
      if (oldest !== undefined) console.warn(`[VAMOS] PokerNow's log did not page back below ${history}`);
      history = null;
      return;
    }
    if (!deliver({ type: "log_page", gameId, fetchedAt: Date.now(), fresh: false, body })) return;
    history = oldest;
  }

  function schedule(wait: number) {
    clearTimeout(timer);
    timer = setTimeout(tick, wait);
  }

  async function tick() {
    const requestedIn = generation;
    const walking = historyNext && history !== null;
    inFlight = true;
    lastStart = Date.now();
    const outcome = await request(`/games/${gameId}/log${query()}`);
    inFlight = false;
    if ("body" in outcome) {
      delay = POLL_MS;
      if (running && requestedIn === generation) {
        if (walking) advanceHistory(outcome.body);
        else advance(outcome.body);
      }
    } else {
      // Doubling stops at MAX_BACKOFF_MS; PokerNow's own Retry-After may ask for longer.
      const doubled = Math.min(delay * 2, MAX_BACKOFF_MS);
      delay = Math.min(Math.max(doubled, outcome.retryAfter), MAX_RETRY_AFTER_MS);
    }
    if (!running) return;
    if ("body" in outcome) setTrouble(null);
    else if (!outcome.busy || delay > QUIET_WAIT_MS) {
      setTrouble({ problem: outcome.problem, retryAt: Date.now() + delay });
    }
    // Live and history requests take turns while there is history left to walk.
    historyNext = history !== null && !walking;
    schedule(delay);
  }

  return {
    stop: () => {
      stopped = true;
      running = false;
      clearTimeout(timer);
    },
    resume: (after) => {
      if (stopped) return;
      generation += 1;
      cursor = after;
      backfill = null;
      history = null;
      historyNext = false;
      // Holding nothing of this game, the backend gets its whole log, oldest rows last.
      wantHistory = after === null;
      if (running) return;
      running = true;
      // A request still on its way schedules the next one; otherwise keep PokerNow's gap.
      if (!inFlight) schedule(Math.max(0, lastStart + delay - Date.now()));
    },
    pause: () => {
      if (!running) return;
      running = false;
      generation += 1;
      clearTimeout(timer);
      setTrouble(null);
    },
    soon: () => {
      // A request already on its way brings the rows; a backoff after a failure stands.
      if (!running || inFlight || delay !== POLL_MS) return;
      historyNext = false;
      schedule(Math.max(0, lastStart + MIN_GAP_MS - Date.now()));
    },
  };
}
