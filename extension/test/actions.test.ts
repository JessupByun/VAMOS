import { describe, expect, it } from "vitest";

import { actionClass, actionKind } from "../src/shared/actions";

describe("actionKind", () => {
  it("reads the card's actions and the mix chips alike", () => {
    expect(actionKind("FOLD")).toBe("fold");
    expect(actionKind("CHECK")).toBe("check");
    expect(actionKind("CALL $0.60 (3bb)")).toBe("call");
    expect(actionKind("BET $1.20 (6bb)")).toBe("bet");
    expect(actionKind("RAISE TO $2.40 (12bb)")).toBe("raise");
    expect(actionKind("ALL IN $10.00 (50bb)")).toBe("allin");
    expect(actionKind("raise $0.60 (3bb) 60%")).toBe("raise");
    expect(actionKind("all in 20%")).toBe("allin");
    expect(actionKind("call 40%")).toBe("call");
  });

  it("leaves anything else uncolored", () => {
    expect(actionKind("AA")).toBeNull();
    expect(actionKind(null)).toBeNull();
    expect(actionClass("Top pair")).toBe("");
    expect(actionClass("FOLD")).toBe("act-fold");
  });
});
