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
    const id = playerId(element);
    if (!id) continue;
    const name = element.querySelector(".table-player-name")?.textContent?.trim() ?? "";
    seats.push({ id, name, element });
  }
  return seats;
}

/** The hero's own seat, or null when the hero is not seated. */
export function readHero(root: ParentNode): SeatedPlayer | null {
  const element = root.querySelector(".table-player.you-player");
  const id = element ? playerId(element) : null;
  if (!element || !id) return null;
  return { id, name: element.querySelector(".table-player-name")?.textContent?.trim() ?? "" };
}

/** The PokerNow ID of the player in a `.table-player` seat. */
export function playerId(seat: Element): string | null {
  const href = seat.querySelector('a[href^="/players/"]')?.getAttribute("href") ?? "";
  return href.match(PLAYER_LINK)?.[1] ?? null;
}

/** A stable key for "which players sit where", to report only real changes. */
export function seatsKey(seats: SeatedPlayer[]): string {
  return seats.map((s) => `${s.id}:${s.name}`).join("|");
}
