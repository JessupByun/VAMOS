import type { LogPage } from "../../shared/protocol";

const POLL_MS = 1500;
const MAX_BACKOFF_MS = 15000;

// PokerNow's undocumented log endpoint (same origin, uses the page's cookies). Pages are
// forwarded untouched; the backend owns parsing, paging and dedup. The shape is verified
// against live responses in the P0 spike before anything depends on it.
export function startLogPoller(gameId: string, onPage: (page: LogPage) => void): () => void {
  let delay = POLL_MS;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  async function tick() {
    try {
      const response = await fetch(`/games/${gameId}/log`, { credentials: "same-origin" });
      if (response.status === 429) {
        delay = Math.min(delay * 2, MAX_BACKOFF_MS);
      } else {
        delay = POLL_MS;
        if (response.ok && !stopped) {
          onPage({ type: "log_page", gameId, fetchedAt: Date.now(), body: await response.json() });
        }
      }
    } catch {
      delay = Math.min(delay * 2, MAX_BACKOFF_MS);
    }
    if (!stopped) timer = setTimeout(tick, delay);
  }

  tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
