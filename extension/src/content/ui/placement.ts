// Where the action card goes. A few spots are tried in order: just above the pot (the top
// of the table, rarely taken in a short-handed game), then on the right above PokerNow's
// "Your Turn" and buttons, then the top of the window. The first that covers nothing wins;
// failing that, the first that covers only VAMOS's own badges; failing that, the one that
// covers the least of PokerNow's own content.
//
// The spot is chosen for a full-size card (RESERVED), whatever the card says right now, so
// the waiting pill, "thinking..." and the answer all appear in the same place. The card is
// then pinned to the spot's edge that faces the table (its bottom above the pot or the
// buttons), so a smaller card never moves toward what the spot keeps clear of.

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Layout {
  viewport: { width: number; height: number };
  /** The pot (with its "total" pill), else the board: the first spot is just above it. */
  center: Box | null;
  /** PokerNow's "Your Turn" signal and buttons, bottom right. */
  decisions: Box | null;
  /** PokerNow's own content: seats, their cards and bets, the pot, the board, the buttons. */
  obstacles: Box[];
  /** VAMOS's own badges under the seats: better covered than anything of PokerNow's. */
  soft: Box[];
}

export type SpotName = "top" | "right" | "window-top";

export interface Spot {
  name: SpotName;
  /** Where a RESERVED-size card would sit. */
  box: Box;
}

/** A full answer card, the largest the card usually gets. */
export const RESERVED = { width: 340, height: 125 };
const MARGIN = 8; // from the window's edges
const GAP = 10; // from what the card sits next to

function overlapArea(a: Box, others: Box[]): number {
  let area = 0;
  for (const b of others) {
    const w = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
    const h = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
    if (w > 0 && h > 0) area += w * h;
  }
  return area;
}

function clamp(box: Box, layout: Layout): Box {
  const maxLeft = Math.max(MARGIN, layout.viewport.width - MARGIN - box.width);
  const maxTop = Math.max(MARGIN, layout.viewport.height - MARGIN - box.height);
  return {
    ...box,
    left: Math.min(Math.max(box.left, MARGIN), maxLeft),
    top: Math.min(Math.max(box.top, MARGIN), maxTop),
  };
}

export function spots(layout: Layout): Spot[] {
  const { width, height } = RESERVED;
  const { viewport, center, decisions } = layout;
  const out: Spot[] = [];
  if (center) {
    const left = center.left + center.width / 2 - width / 2;
    out.push({ name: "top", box: clamp({ left, top: center.top - GAP - height, width, height }, layout) });
  }
  const bottom = decisions ? decisions.top - GAP : viewport.height * 0.6;
  const right = { left: viewport.width - MARGIN - width, top: bottom - height, width, height };
  out.push({ name: "right", box: clamp(right, layout) });
  const windowTop = { left: viewport.width / 2 - width / 2, top: MARGIN, width, height };
  out.push({ name: "window-top", box: clamp(windowTop, layout) });
  return out;
}

export function chooseSpot(layout: Layout): Spot {
  const all = spots(layout);
  const hard = (s: Spot) => overlapArea(s.box, layout.obstacles);
  const soft = (s: Spot) => overlapArea(s.box, layout.soft);
  return (
    all.find((s) => hard(s) === 0 && soft(s) === 0) ??
    all.find((s) => hard(s) === 0) ??
    all.reduce((best, s) => (hard(s) < hard(best) ? s : best))
  );
}

/** CSS for the card's slot: pinned to the spot's edge that faces the table. */
export function slotStyle(spot: Spot, layout: Layout): Record<string, string> {
  const { box } = spot;
  const bottom = `${Math.round(layout.viewport.height - (box.top + box.height))}px`;
  const centerX = `${Math.round(box.left + box.width / 2)}px`;
  if (spot.name === "top") return { left: centerX, bottom, transform: "translateX(-50%)" };
  if (spot.name === "right") {
    return { right: `${Math.round(layout.viewport.width - (box.left + box.width))}px`, bottom };
  }
  return { left: centerX, top: `${Math.round(box.top)}px`, transform: "translateX(-50%)" };
}
