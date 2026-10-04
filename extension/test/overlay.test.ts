import { describe, expect, it } from "vitest";

import { forTurn } from "../src/content/ui/overlay";
import type { Facts, SpotFacts } from "../src/shared/protocol";

const spot = { madeHand: "AA" } as SpotFacts;
const reply = (key: string, extra: Partial<Facts>): Facts => ({ type: "facts", gameId: "g", key, facts: null, ...extra });

describe("forTurn", () => {
  it("shows the backend's error for the current turn instead of reading forever", () => {
    expect(forTurn("k1", reply("k1", { error: "VAMOS! hit an error" }))).toEqual({ error: "VAMOS! hit an error" });
  });

  it("never shows an error or facts from an earlier turn", () => {
    expect(forTurn("k2", reply("k1", { error: "VAMOS! hit an error" }))).toBeNull();
    expect(forTurn("k2", reply("k1", { facts: spot }))).toBeNull();
  });

  it("shows the facts once they arrive, and nothing while the log catches up", () => {
    expect(forTurn("k1", reply("k1", { facts: spot }))).toEqual({ facts: spot });
    expect(forTurn("k1", reply("k1", {}))).toBeNull();
    expect(forTurn("k1", null)).toBeNull();
  });
});
