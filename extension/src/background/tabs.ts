import type { ContentToWorker } from "../shared/protocol";

/** Polling a game's log starts in a tab, or moves to it from another. */
export interface Handover<Tab> {
  gameId: string;
  /** The tab that polled until now, when it is still open: it stops. */
  from?: Tab;
  /** The tab that polls from now on; none once the game's last tab closed. */
  to?: Tab;
}

/**
 * The PokerNow tabs open on each game, and which of them speaks for it. One tab per game polls
 * the log, since PokerNow's rate limit is per IP and a second poller doubles the requests: the
 * first to ask, until it closes or reloads, when another tab on the game takes over. Chrome
 * may slow a background tab's timers and can freeze background tabs to save energy, so a hidden
 * polling tab also hands over: to a tab on the game the user looks at, or else to one where
 * the hero's turn begins. And while some tab on a game sees the hero
 * seated, a tab that does not (still loading, or watching) does not report the table, so the
 * backend keeps the hero's seat and turn.
 */
export class GameTabs<Tab> {
  private readonly games = new Map<Tab, string>();
  private readonly seated = new Set<Tab>();
  private readonly hidden = new Set<Tab>();
  private readonly turns = new Set<Tab>();
  private readonly pollers = new Map<string, Tab>();

  /**
   * `tab` asks to poll its game's log. It does when no other tab polls, or in place of a hidden
   * one; null when another tab goes on polling.
   */
  claim(tab: Tab, gameId: string): Handover<Tab> | null {
    this.games.set(tab, gameId);
    const poller = this.pollers.get(gameId);
    if (poller === undefined || poller === tab) {
      this.pollers.set(gameId, tab);
      return { gameId, to: tab };
    }
    return this.outranks(tab, poller) ? this.move(gameId, tab) : null;
  }

  /** The tab polling the game's log, if any. */
  poller(gameId: string): Tab | undefined {
    return this.pollers.get(gameId);
  }

  /** `tab` was shown or hidden: when polling moves because of it, where. */
  show(tab: Tab, gameId: string, visible: boolean): Handover<Tab> | null {
    this.games.set(tab, gameId);
    if (visible) this.hidden.delete(tab);
    else this.hidden.add(tab);
    return this.follow(tab, gameId);
  }

  /**
   * The hero's turn began (`showing`) or ended in `tab`: when polling moves because of it, where.
   * Only a turn beginning moves it; one ending never does, so two hidden tabs on the game never
   * pass polling back and forth.
   */
  turn(tab: Tab, gameId: string, showing: boolean): Handover<Tab> | null {
    this.games.set(tab, gameId);
    if (!showing) {
      this.turns.delete(tab);
      return null;
    }
    this.turns.add(tab);
    return this.follow(tab, gameId);
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
   * the best other tab open on the game, if there is one.
   */
  close(tab: Tab): Handover<Tab> | null {
    const gameId = this.games.get(tab);
    this.games.delete(tab);
    this.seated.delete(tab);
    this.hidden.delete(tab);
    this.turns.delete(tab);
    if (gameId === undefined || this.pollers.get(gameId) !== tab) return null;
    this.pollers.delete(gameId);
    const next = this.best(gameId);
    if (next !== undefined) this.pollers.set(gameId, next);
    return { gameId, to: next };
  }

  // After every change the poller is one no other tab outranks, so only the tab that changed
  // can come to outrank it, unless it is the poller itself.
  private follow(changed: Tab, gameId: string): Handover<Tab> | null {
    const poller = this.pollers.get(gameId);
    if (poller === undefined) return null;
    const candidate = changed === poller ? this.best(gameId) : changed;
    return candidate !== undefined && this.outranks(candidate, poller) ? this.move(gameId, candidate) : null;
  }

  /** Shown before hidden, then showing the hero's turn. */
  private rank(tab: Tab): number {
    return (this.hidden.has(tab) ? 0 : 2) + (this.turns.has(tab) ? 1 : 0);
  }

  /** Whether `tab` polls in place of `poller`: only a hidden poller hands over. */
  private outranks(tab: Tab, poller: Tab): boolean {
    return this.hidden.has(poller) && this.rank(tab) > this.rank(poller);
  }

  private best(gameId: string): Tab | undefined {
    let best: Tab | undefined;
    for (const [tab, game] of this.games) {
      if (game === gameId && (best === undefined || this.rank(tab) > this.rank(best))) best = tab;
    }
    return best;
  }

  private move(gameId: string, to: Tab): Handover<Tab> {
    const from = this.pollers.get(gameId);
    this.pollers.set(gameId, to);
    return { gameId, from, to };
  }
}
