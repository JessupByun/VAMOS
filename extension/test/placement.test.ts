import { describe, expect, it } from "vitest";

import { type Box, chooseSpot, type Layout, RESERVED, slotStyle, spots } from "../src/content/ui/placement";

const box = (left: number, top: number, width: number, height: number): Box => ({ left, top, width, height });
const badge = (seat: Box): Box => box(seat.left, seat.top + seat.height, seat.width, 26);

// PokerNow's real geometry (from its stylesheet) in a 1600x900 window: a 16:9 canvas with
// rem = 0.032 x 900 = 28.8px; the table is 1600x756 at the top; seats are 288x102 (18% x
// 13.5% of the table); the pot pill sits at 24% down, 43.4% across; the "Your Turn" signal
// and buttons take the bottom right.
const SEAT = { 6: box(464, 15, 288, 102), 7: box(848, 15, 288, 102), 10: box(1200, 499, 288, 102) };
const POT = box(694, 181, 211, 40);
const DECISIONS = box(1150, 742, 440, 144);

function table(seats: Box[]): Layout {
  return {
    viewport: { width: 1600, height: 900 },
    center: POT,
    decisions: DECISIONS,
    obstacles: [POT, DECISIONS, ...seats],
    soft: seats.map(badge),
  };
}

describe("chooseSpot", () => {
  it("puts the card at the top of the table, just above the pot, when nobody sits there", () => {
    const spot = chooseSpot(table([SEAT[10]]));
    expect(spot.name).toBe("top");
    expect(spot.box.top + spot.box.height).toBe(POT.top - 10);
    expect(spot.box.left + spot.box.width / 2).toBe(POT.left + POT.width / 2);
  });

  it("moves to the right when players sit at the top, even if it touches a VAMOS badge there", () => {
    // The top spot covers seats 6 and 7; the right spot clears seat 10 and only touches its badge.
    expect(chooseSpot(table([SEAT[6], SEAT[7], SEAT[10]])).name).toBe("right");
  });

  it("prefers a spot touching nothing over one touching only a badge", () => {
    const low = box(1250, 560, 300, 60); // a seat whose badge reaches into the right spot
    const layout = { ...table([SEAT[6]]), soft: [badge(low)] };
    expect(chooseSpot(layout).name).toBe("right"); // top is blocked by seat 6
    const both = { ...table([]), soft: [badge(low), box(700, 140, 50, 30)] };
    expect(chooseSpot(both).name).toBe("window-top");
  });

  it("when every spot covers something, picks the one covering the least", () => {
    const layout = table([SEAT[6], SEAT[7], box(1200, 560, 390, 160)]);
    // Right is buried under the big seat; top and window-top each clip seats 6 and 7.
    expect(chooseSpot(layout).name).not.toBe("right");
  });

  it("keeps every spot on screen, even beside a pot at the window's edge", () => {
    const layout = { ...table([]), center: box(0, 20, 60, 30) };
    for (const spot of spots(layout)) {
      expect(spot.box.left).toBeGreaterThanOrEqual(8);
      expect(spot.box.top).toBeGreaterThanOrEqual(8);
      expect(spot.box.left + RESERVED.width).toBeLessThanOrEqual(1600 - 8);
      expect(spot.box.top + RESERVED.height).toBeLessThanOrEqual(900 - 8);
    }
  });
});

describe("slotStyle", () => {
  it("pins the card's bottom just above the pot, centered on it", () => {
    const layout = table([]);
    const style = slotStyle(chooseSpot(layout), layout);
    expect(style).toEqual({ left: "800px", bottom: `${900 - (POT.top - 10)}px`, transform: "translateX(-50%)" });
  });

  it("pins the card's bottom right corner above the buttons on the right", () => {
    const layout = table([SEAT[6], SEAT[7]]);
    expect(slotStyle(chooseSpot(layout), layout)).toEqual({ right: "8px", bottom: `${900 - (742 - 10)}px` });
  });
});
