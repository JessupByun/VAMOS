import { describe, expect, it } from "vitest";

import { type Costs, estimateNote, pausedWhy, troubleText } from "../src/shared/protocol";

describe("troubleText", () => {
  const problem = "PokerNow's log is not answering (HTTP 404)";

  it("counts down to the next try in seconds, then minutes for long waits", () => {
    expect(troubleText({ problem, retryAt: 8000 }, 0)).toBe(`${problem}, retrying in 8 s`);
    expect(troubleText({ problem, retryAt: 8000 }, 7100)).toBe(`${problem}, retrying in 1 s`);
    expect(troubleText({ problem, retryAt: 119_000 }, 0)).toBe(`${problem}, retrying in 119 s`);
    expect(troubleText({ problem, retryAt: 300_000 }, 0)).toBe(`${problem}, retrying in 5 min`);
  });

  it("says it is retrying now once the wait is over", () => {
    expect(troubleText({ problem, retryAt: 8000 }, 8000)).toBe(`${problem}, retrying now`);
    expect(troubleText({ problem, retryAt: 8000 }, 9000)).toBe(`${problem}, retrying now`);
  });
});

describe("pausedWhy", () => {
  it("says why the controls that need the backend wait, until it is ready", () => {
    expect(pausedWhy({ type: "backend", state: "ready", version: "0.1.0" })).toBeNull();
    expect(pausedWhy({ type: "backend", state: "connecting" })).toBe("VAMOS's backend is starting");
    expect(pausedWhy({ type: "backend", state: "down", detail: "backend exited" })).toBe(
      "VAMOS's backend is not running",
    );
  });
});

describe("estimateNote", () => {
  const none = { dollars: 0, calls: 0 };
  const costs = (
    estimated: [number, number][] = [],
    unknown: [number, number, number] = [0, 0, 0],
  ): Costs => {
    const [session = none, month = none, allTime = none] = estimated.map(([dollars, calls]) => ({ dollars, calls }));
    return {
      type: "costs",
      session: 1,
      month: 1,
      allTime: 1,
      overCap: false,
      softCap: 5,
      estimated: { session, month, allTime },
      unknown: { session: unknown[0], month: unknown[1], allTime: unknown[2] },
    };
  };

  it("says nothing when every call was counted from Claude's own token counts", () => {
    expect(estimateNote(costs())).toBeNull();
  });

  it("says once how much is estimated when every figure holds the same calls", () => {
    expect(estimateNote(costs([[0.12, 9], [0.12, 9], [0.12, 9]]))).toBe(
      "Includes about $0.12 estimated for 9 calls stopped before Claude answered.",
    );
    expect(estimateNote(costs([[0.004, 1], [0.004, 1], [0.004, 1]]))).toBe(
      "Includes under $0.01 estimated for 1 call stopped before Claude answered.",
    );
  });

  it("names the figures when they hold different calls", () => {
    expect(estimateNote(costs([[0.12, 9], [0.3, 20], [0.4, 25]]))).toBe(
      "Includes about $0.12 estimated for 9 calls stopped before Claude answered this session, " +
        "about $0.30 for 20 this month, and about $0.40 for 25 all time.",
    );
    expect(estimateNote(costs([[0.12, 9], [0.12, 9], [0.4, 25]]))).toBe(
      "Includes about $0.12 estimated for 9 calls stopped before Claude answered this session and this month, " +
        "and about $0.40 for 25 all time.",
    );
    // None this session: only the figures that include some are named.
    expect(estimateNote(costs([[0, 0], [0.3, 20], [0.3, 20]]))).toBe(
      "Includes about $0.30 estimated for 20 calls stopped before Claude answered this month and all time.",
    );
  });

  it("says when a stopped call had nothing comparable to estimate it from", () => {
    expect(estimateNote(costs([], [2, 2, 2]))).toBe(
      "Leaves out 2 calls stopped before Claude answered: nothing comparable to estimate from.",
    );
    expect(estimateNote(costs([[0.12, 9], [0.12, 9], [0.12, 9]], [1, 1, 3]))).toBe(
      "Includes about $0.12 estimated for 9 calls stopped before Claude answered. " +
        "Leaves out 1 call stopped before Claude answered this session and this month, and 3 all time: " +
        "nothing comparable to estimate from.",
    );
  });
});
