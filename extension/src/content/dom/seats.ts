import type { SeatedPlayer } from "../../shared/protocol";

export interface SeatElement extends SeatedPlayer {
  element: Element;
}

const PLAYER_LINK = /^\/players\/([A-Za-z0-9_-]+)$/;

/**
 * The opponents seated at the table, each with its seat element for placing a badge.
 * PokerNow's `table-player-N` classes are hero-relative positions, so players are
 * identified by the `/players/<id>` link inside each seat (the same ID as the log's
 * "Name @ ID"). The hero (`you-player`) is left out.
 */
export function readSeats(root: ParentNode): SeatElement[] {
  const seats: SeatElement[] = [];
  for (const element of root.querySelectorAll(".table-player")) {
    if (element.classList.contains("you-player")) continue;
    const href = element.querySelector('a[href^="/players/"]')?.getAttribute("href") ?? "";
    const id = href.match(PLAYER_LINK)?.[1];
    if (!id) continue;
    const name = element.querySelector(".table-player-name")?.textContent?.trim() ?? "";
    seats.push({ id, name, element });
  }
  return seats;
}

/** A stable key for "which players sit where", to report only real changes. */
export function seatsKey(seats: SeatedPlayer[]): string {
  return seats.map((s) => `${s.id}:${s.name}`).join("|");
}
