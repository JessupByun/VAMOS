import { describe, expect, it } from "vitest";

import { ClockEstimate } from "../src/content/dom/clock";

describe("ClockEstimate", () => {
  it("waits for the bar to move for about a second, then extrapolates", () => {
    const clock = new ClockEstimate();
    // A 20-second clock: 5% per second.
    expect(clock.sample(0, 90)).toBeNull();
    expect(clock.sample(500, 87.5)).toBeNull();
    expect(clock.sample(1000, 85)).toBeCloseTo(17);
  });

  it("starts over when the bar is refilled", () => {
    const clock = new ClockEstimate();
    clock.sample(0, 40);
    expect(clock.sample(1000, 100)).toBeNull();
    expect(clock.sample(2000, 95)).toBeCloseTo(19);
  });

  it("gives nothing without a running bar", () => {
    const clock = new ClockEstimate();
    expect(clock.sample(0, null)).toBeNull();
    expect(clock.sample(1000, 0)).toBeNull();
    clock.sample(2000, 50);
    expect(clock.sample(3000, 50)).toBeNull();
  });
});
