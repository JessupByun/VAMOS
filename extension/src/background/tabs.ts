import type { ContentToWorker } from "../shared/protocol";

/**
 * The PokerNow tabs open on each game, and which of them speaks for it. One tab per game polls
 * the log, since PokerNow's rate limit is per IP and a second poller doubles the requests: the
 * first to ask, until it closes or reloads, when another tab on the game takes over. And while
 * some tab on a game sees the hero seated, a tab that does not (still loading, or watching)
 * does not report the table, so the backend keeps the hero's seat and turn.
 */
export class GameTabs<Tab> {
  private readonly games = new Map<Tab, string>();
  private readonly seated = new Set<Tab>();
  private readonly pollers = new Map<string, Tab>();

  /** `tab` asks to poll its game's log; whether it is the tab that does. */
  claim(tab: Tab, gameId: string): boolean {
    this.games.set(tab, gameId);
    const poller = this.pollers.get(gameId);
    if (poller !== undefined && poller !== tab) return false;
    this.pollers.set(gameId, tab);
    return true;
  }

  /** The tab polling the game's log, if any. */
  poller(gameId: string): Tab | undefined {
    return this.pollers.get(gameId);
  }

  /** Whether `message` from `tab` goes on to the backend. */
  passes(tab: Tab, message: ContentToWorker): boolean {
    if (message.type !== "seats" && message.type !== "turn" && message.type !== "clock") return true;
    this.games.set(tab, message.gameId);
    if (message.type === "seats") {
      if (message.hero) this.seated.add(tab);
      else this.seated.delete(tab);
    }
    if (this.seated.has(tab)) return true;
    for (const other of this.seated) if (this.games.get(other) === message.gameId) return false;
    return true;
  }

  /**
   * `tab` went away. When it was polling, returns its game and the tab that polls it now:
   * another tab open on the game, if there is one.
   */
  close(tab: Tab): { gameId: string; next: Tab | undefined } | null {
    const gameId = this.games.get(tab);
    this.games.delete(tab);
    this.seated.delete(tab);
    if (gameId === undefined || this.pollers.get(gameId) !== tab) return null;
    this.pollers.delete(gameId);
    const next = [...this.games].find(([, game]) => game === gameId)?.[0];
    if (next !== undefined) this.pollers.set(gameId, next);
    return { gameId, next };
  }
}
