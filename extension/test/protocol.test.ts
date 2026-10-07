import { describe, expect, it } from "vitest";

import { troubleText } from "../src/shared/protocol";

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
