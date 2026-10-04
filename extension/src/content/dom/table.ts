import type { TableView } from "../../shared/protocol";
import type { Box, Layout } from "../ui/placement";
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

/** The local user's seat, or null when they are not seated at this table. */
export function heroSeat(root: ParentNode): Element | null {
  return root.querySelector(".table-player.you-player");
}

function box(element: Element | null): Box | null {
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

function boxes(root: ParentNode, selector: string): Box[] {
  return [...root.querySelectorAll(selector)].map(box).filter((b) => b !== null);
}

function union(parts: Box[]): Box | null {
  if (parts.length === 0) return null;
  const left = Math.min(...parts.map((b) => b.left));
  const top = Math.min(...parts.map((b) => b.top));
  const right = Math.max(...parts.map((b) => b.left + b.width));
  const bottom = Math.max(...parts.map((b) => b.top + b.height));
  return { left, top, width: right - left, height: bottom - top };
}

// VAMOS's own badges hang just under each seat.
const BADGE_HEIGHT = 26;

// Where the pot was last seen, for this window size: between hands it can be empty or gone,
// and the card should not jump around the screen when it is.
let lastCenter: { viewport: string; box: Box } | null = null;

/**
 * What the action card must keep clear of, and what it sits next to. Containers are
 * measured with their descendants: PokerNow positions many parts absolutely (hole cards,
 * the pot's "total" pill, the "Check or Fold" button above the action row), and a
 * container's own box leaves those out.
 */
export function readLayout(root: ParentNode): Layout {
  const seats = boxes(root, ".table-player");
  const decisions = union(boxes(root, "p.action-signal, .game-decisions-ctn, .game-decisions-ctn *"));
  const pot = union(boxes(root, ".table-pot-size, .table-pot-size *"));
  const board = union(boxes(root, ".table-cards, .table-cards *"));
  const viewport = `${window.innerWidth}x${window.innerHeight}`;
  if (pot) lastCenter = { viewport, box: pot };
  const remembered = lastCenter?.viewport === viewport ? lastCenter.box : null;
  return {
    viewport: { width: window.innerWidth, height: window.innerHeight },
    center: pot ?? remembered ?? board,
    decisions,
    obstacles: [
      ...seats,
      ...boxes(root, ".table-player .card-container, .table-player-bet-value, .dealer-button-ctn, .dealer-button-ctn *"),
      ...[pot, board, decisions].filter((b) => b !== null),
    ],
    soft: seats.map((b) => ({ left: b.left, top: b.top + b.height, width: b.width, height: BADGE_HEIGHT })),
  };
}

/** The table at the hero's turn, or null when it is not the hero's turn. */
export function readHeroTurn(root: ParentNode): TableView | null {
  const hero = root.querySelector(".table-player.you-player");
  // PokerNow leaves the action buttons up between turns (as pre-actions), so whose turn it
  // is comes from the seat marked as deciding, and from the action area, which PokerNow
  // dims (class "low") whenever the hero is not the one to act. "Your Turn" is not used:
  // it goes away while the hero has the bet-size panel open, mid-decision.
  if (!hero?.classList.contains("decision-current")) return null;
  if (root.querySelector(".game-decisions-ctn.low")) return null;
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
