import type { TableView } from "../../shared/protocol";
import { playerId } from "./seats";

// A face-up card is rendered as `card-container card-<suit> card-s-<rank>`, with the rank
// as "2".."9", "T", "J", "Q", "K" or "A" (only the visible text spells ten as "10").
// Face-down cards carry neither class.
function readCard(element: Element): string | null {
  let rank: string | undefined;
  let suit: string | undefined;
  for (const name of element.classList) {
    rank ??= name.match(/^card-s-([2-9TJQKA])$/)?.[1];
    suit ??= name.match(/^card-([shdc])$/)?.[1];
  }
  return rank && suit ? rank + suit : null;
}

function readCards(elements: Iterable<Element>): string[] {
  return [...elements].map(readCard).filter((card) => card !== null);
}

function chipsText(element: Element | null): string {
  return element?.querySelector(".chips-value .normal-value")?.textContent?.trim() ?? "";
}

/** Whether the local user has a seat at this table. */
export function heroSeated(root: ParentNode): boolean {
  return root.querySelector(".table-player.you-player") !== null;
}

/** The table at the hero's turn, or null when it is not the hero's turn. */
export function readHeroTurn(root: ParentNode): TableView | null {
  const hero = root.querySelector(".table-player.you-player");
  // PokerNow leaves the action buttons up between turns (as pre-actions), so whose turn it
  // is comes only from the seat marked as deciding.
  if (!hero?.classList.contains("decision-current")) return null;
  const heroId = playerId(hero);
  if (!heroId) return null;
  return {
    heroId,
    heroCards: readCards(hero.querySelectorAll(".table-player-cards .card-container")),
    board: readCards(root.querySelectorAll(".table-cards.run-1 .card-container")),
    pot: chipsText(root.querySelector(".table-pot-size .main-value")),
    bets: [...root.querySelectorAll(".table-player .table-player-bet-value")].map(chipsText),
  };
}
