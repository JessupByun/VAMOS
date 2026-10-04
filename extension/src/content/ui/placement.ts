// Where the action card goes: always the top middle of the table, just above the pot, even
// when a player sits there (the card takes no clicks). Before the pot is found, the top
// middle of the window.
//
// The spot is sized for a full card (RESERVED), whatever the card says right now, and the
// card is pinned by its bottom edge, so the waiting pill, "thinking..." and the answer all
// sit at the same height above the pot.

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface Layout {
  viewport: { width: number; height: number };
  /** The pot (with its "total" pill), else the board: the card sits just above it. */
  center: Box | null;
}

export type SpotName = "top" | "window-top";

export interface Spot {
  name: SpotName;
  /** Where a RESERVED-size card would sit. */
  box: Box;
}

/** A full answer card, the largest the card usually gets. */
export const RESERVED = { width: 340, height: 125 };
const MARGIN = 8; // from the window's edges
const GAP = 10; // from the pot

function clamp(box: Box, layout: Layout): Box {
  const maxLeft = Math.max(MARGIN, layout.viewport.width - MARGIN - box.width);
  const maxTop = Math.max(MARGIN, layout.viewport.height - MARGIN - box.height);
  return {
    ...box,
    left: Math.min(Math.max(box.left, MARGIN), maxLeft),
    top: Math.min(Math.max(box.top, MARGIN), maxTop),
  };
}

export function chooseSpot(layout: Layout): Spot {
  const { width, height } = RESERVED;
  const { viewport, center } = layout;
  if (center) {
    const left = center.left + center.width / 2 - width / 2;
    return { name: "top", box: clamp({ left, top: center.top - GAP - height, width, height }, layout) };
  }
  return { name: "window-top", box: clamp({ left: viewport.width / 2 - width / 2, top: MARGIN, width, height }, layout) };
}

/** CSS for the card's slot: centered on the spot, pinned by the edge that faces the table. */
export function slotStyle(spot: Spot, layout: Layout): Record<string, string> {
  const { box } = spot;
  const centerX = `${Math.round(box.left + box.width / 2)}px`;
  if (spot.name === "top") {
    const bottom = `${Math.round(layout.viewport.height - (box.top + box.height))}px`;
    return { left: centerX, bottom, transform: "translateX(-50%)" };
  }
  return { left: centerX, top: `${Math.round(box.top)}px`, transform: "translateX(-50%)" };
}
