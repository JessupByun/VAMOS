import { describe, expect, it } from "vitest";

import { GameTabs } from "../src/background/tabs";
import type { ContentToWorker } from "../src/shared/protocol";

const hero = { id: "heroid0001", name: "Hero" };
const seats = (gameId: string, seated: boolean): ContentToWorker => ({
  type: "seats",
  gameId,
  players: [{ id: "villain001", name: "Villain" }],
  hero: seated ? hero : null,
});
const turn = (gameId: string): ContentToWorker => ({ type: "turn", gameId, key: "", table: null });

describe("GameTabs", () => {
  it("lets the first tab to ask poll a game, and no other tab on it", () => {
    const tabs = new GameTabs<string>();
    expect(tabs.claim("A", "g1")).toBe(true);
    expect(tabs.claim("B", "g1")).toBe(false);
    expect(tabs.claim("A", "g1")).toBe(true); // asking again, e.g. after the backend restarted
    expect(tabs.claim("C", "g2")).toBe(true); // another game has its own poller
    expect(tabs.poller("g1")).toBe("A");
    expect(tabs.poller("g2")).toBe("C");
  });

  it("hands polling to another tab on the game when the polling one goes", () => {
    const tabs = new GameTabs<string>();
    tabs.claim("A", "g1");
    tabs.claim("B", "g1");
    tabs.claim("C", "g2");
    expect(tabs.close("B")).toBeNull(); // was not polling
    tabs.claim("D", "g1");
    expect(tabs.close("A")).toEqual({ gameId: "g1", next: "D" });
    expect(tabs.poller("g1")).toBe("D");
    expect(tabs.close("D")).toEqual({ gameId: "g1", next: undefined });
    expect(tabs.poller("g1")).toBeUndefined();
    expect(tabs.claim("E", "g1")).toBe(true);
    expect(tabs.poller("g2")).toBe("C");
  });

  it("knows a tab's game from its reports of the table, not only from asking to poll", () => {
    const tabs = new GameTabs<string>();
    tabs.claim("A", "g1");
    tabs.passes("B", seats("g1", true));
    expect(tabs.close("A")).toEqual({ gameId: "g1", next: "B" });
  });

  it("drops a tab's reports of the table while another tab on the game sees the hero seated", () => {
    const tabs = new GameTabs<string>();
    expect(tabs.passes("A", seats("g1", true))).toBe(true);
    // Still loading, or watching: it would make the backend forget the hero's seat and turn.
    expect(tabs.passes("B", seats("g1", false))).toBe(false);
    expect(tabs.passes("B", turn("g1"))).toBe(false);
    // Other tables, and the rest of what a tab sends, are not affected.
    expect(tabs.passes("C", seats("g2", false))).toBe(true);
    expect(tabs.passes("B", { type: "assign_identity", playerId: "villain001", profile: null })).toBe(true);
    // Once it sees the hero seated too, it reports like the other.
    expect(tabs.passes("B", seats("g1", true))).toBe(true);
    expect(tabs.passes("B", turn("g1"))).toBe(true);
    expect(tabs.passes("A", turn("g1"))).toBe(true);
  });

  it("lets a tab without the hero report once no tab on the game sees the hero seated", () => {
    const tabs = new GameTabs<string>();
    tabs.passes("A", seats("g1", true));
    expect(tabs.passes("B", seats("g1", false))).toBe(false);
    tabs.close("A");
    expect(tabs.passes("B", seats("g1", false))).toBe(true);
    // The hero stood up in the only tab that saw them seated.
    tabs.passes("C", seats("g2", true));
    expect(tabs.passes("C", seats("g2", false))).toBe(true);
  });
});
