import { describe, expect, it } from "vitest";

import { type Box, chooseSpot, type Layout, RESERVED, slotStyle } from "../src/content/ui/placement";

const box = (left: number, top: number, width: number, height: number): Box => ({ left, top, width, height });

// PokerNow's real geometry (from its stylesheet) in a 1600x900 window: the pot pill sits at
// 24% down, 43.4% across the 1600x756 table.
const POT = box(694, 181, 211, 40);
const table = (center: Box | null = POT): Layout => ({ viewport: { width: 1600, height: 900 }, center });

describe("chooseSpot", () => {
  it("always puts the card at the top middle, just above the pot", () => {
    const spot = chooseSpot(table());
    expect(spot.name).toBe("top");
    expect(spot.box.top + spot.box.height).toBe(POT.top - 10);
    expect(spot.box.left + spot.box.width / 2).toBe(POT.left + POT.width / 2);
  });

  it("uses the top middle of the window before any pot is found", () => {
    const spot = chooseSpot(table(null));
    expect(spot.name).toBe("window-top");
    expect(spot.box.left + spot.box.width / 2).toBe(800);
  });

  it("stays on screen beside a pot at the window's edge", () => {
    const spot = chooseSpot(table(box(0, 20, 60, 30)));
    expect(spot.box.left).toBeGreaterThanOrEqual(8);
    expect(spot.box.top).toBeGreaterThanOrEqual(8);
    expect(spot.box.left + RESERVED.width).toBeLessThanOrEqual(1600 - 8);
  });
});

describe("slotStyle", () => {
  it("pins the card's bottom just above the pot, centered on it", () => {
    const layout = table();
    expect(slotStyle(chooseSpot(layout), layout)).toEqual({
      left: "800px",
      bottom: `${900 - (POT.top - 10)}px`,
      transform: "translateX(-50%)",
    });
  });
});
