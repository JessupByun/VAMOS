import type { LogPage } from "../../shared/protocol";

export const POLL_MS = 2000;
export const MAX_BACKOFF_MS = 30000;
/** PokerNow's maximum rows per response (per open-source trackers; not yet seen live). */
export const PAGE_SIZE = 50;

interface LogBody {
  logs?: { created_at?: string }[];
}

export interface LogPoller {
  stop: () => void;
  /** Forget the cursor so the next poll re-sends the newest rows (e.g. to a fresh backend). */
  resync: () => void;
}

/** Rows strictly between `floor` and `before` still need fetching; then the cursor moves to `target`. */
interface Backfill {
  floor: string;
  before: string;
  target: string;
}

// PokerNow's undocumented log endpoint (same origin, uses the page's cookies), rate-limited
// to roughly one request per 2s per IP. Rows come newest first; `after_at` and `before_at`
// take `created_at` values (epoch ms x 100 plus a sequence number) and are exclusive.
// `after_at` returns only the newest page above the cursor, so when a full page comes back
// the rows below it are walked back with `before_at` before the cursor may move, one request
// per tick. Pages are forwarded untouched; the backend owns parsing and dedup.
export function startLogPoller(gameId: string, deliver: (page: LogPage) => boolean): LogPoller {
  let delay = POLL_MS;
  let cursor: string | null = null;
  let backfill: Backfill | null = null;
  // Bumped by resync(), so a response to a request made before it cannot move the cursor.
  let generation = 0;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  function query(): string {
    if (backfill) return `?after_at=${backfill.floor}&before_at=${backfill.before}`;
    return cursor ? `?after_at=${cursor}` : "";
  }

  function advance(body: LogBody) {
    const rows = body.logs ?? [];
    const newest = rows[0]?.created_at;
    const oldest = rows.at(-1)?.created_at;
    if (newest === undefined || oldest === undefined) {
      if (backfill) {
        cursor = backfill.target;
        backfill = null;
      }
      return;
    }
    // Not delivered (no backend connected): leave everything as is and ask again next tick.
    if (!deliver({ type: "log_page", gameId, fetchedAt: Date.now(), body })) return;
    const full = rows.length >= PAGE_SIZE;
    if (backfill) {
      if (full) backfill = { ...backfill, before: oldest };
      else {
        cursor = backfill.target;
        backfill = null;
      }
    } else if (cursor !== null && full) {
      backfill = { floor: cursor, before: oldest, target: newest };
    } else {
      cursor = newest;
    }
  }

  async function tick() {
    const requestedIn = generation;
    try {
      const response = await fetch(`/games/${gameId}/log${query()}`, { credentials: "same-origin" });
      if (response.status === 429 || response.status === 503) {
        const retryAfter = Number(response.headers.get("retry-after")) * 1000;
        delay = Math.min(Math.max(delay * 2, retryAfter || 0), MAX_BACKOFF_MS);
      } else {
        delay = POLL_MS;
        if (response.ok) {
          const body: LogBody = await response.json();
          if (!stopped && requestedIn === generation) advance(body);
        }
      }
    } catch {
      delay = Math.min(delay * 2, MAX_BACKOFF_MS);
    }
    if (!stopped) timer = setTimeout(tick, delay);
  }

  tick();
  return {
    stop: () => {
      stopped = true;
      clearTimeout(timer);
    },
    resync: () => {
      generation += 1;
      cursor = null;
      backfill = null;
    },
  };
}
