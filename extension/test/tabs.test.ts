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
    expect(tabs.claim("A", "g1")).toEqual({ gameId: "g1", to: "A" });
    expect(tabs.claim("B", "g1")).toBeNull();
    // Asking again, e.g. after the backend restarted.
    expect(tabs.claim("A", "g1")).toEqual({ gameId: "g1", to: "A" });
    // Another game has its own poller.
    expect(tabs.claim("C", "g2")).toEqual({ gameId: "g2", to: "C" });
    expect(tabs.poller("g1")).toBe("A");
    expect(tabs.poller("g2")).toBe("C");
  });

  it("knows which tabs show the hero's turn, so a closed one's turn can be cleared", () => {
    const tabs = new GameTabs<string>();
    tabs.claim("A", "g1");
    tabs.claim("B", "g1");
    tabs.turn("A", "g1", true);
    expect([tabs.gameOf("A"), tabs.showsTurn("A"), tabs.showsTurn("B")]).toEqual(["g1", true, false]);
    expect(tabs.turnShown("g1")).toBe(true);
    tabs.close("A");
    expect(tabs.turnShown("g1")).toBe(false);
    // The same turn showing in another tab on the game keeps it.
    tabs.turn("B", "g1", true);
    expect(tabs.turnShown("g1")).toBe(true);
    expect(tabs.turnShown("g2")).toBe(false);
  });

  it("hands polling to another tab on the game when the polling one goes", () => {
    const tabs = new GameTabs<string>();
    tabs.claim("A", "g1");
    tabs.claim("B", "g1");
    tabs.claim("C", "g2");
    expect(tabs.close("B")).toBeNull(); // was not polling
    tabs.claim("D", "g1");
    expect(tabs.close("A")).toEqual({ gameId: "g1", to: "D" });
    expect(tabs.poller("g1")).toBe("D");
    expect(tabs.close("D")).toEqual({ gameId: "g1", to: undefined });
    expect(tabs.poller("g1")).toBeUndefined();
    expect(tabs.claim("E", "g1")).toEqual({ gameId: "g1", to: "E" });
    expect(tabs.poller("g2")).toBe("C");
  });

  it("knows a tab's game from its reports of the table, not only from asking to poll", () => {
    const tabs = new GameTabs<string>();
    tabs.claim("A", "g1");
    tabs.passes("B", seats("g1", true));
    expect(tabs.close("A")).toEqual({ gameId: "g1", to: "B" });
  });

  it("hands polling to a tab the user looks at once the polling tab is hidden", () => {
    const tabs = new GameTabs<string>();
    tabs.show("A", "g1", true);
    tabs.claim("A", "g1");
    tabs.show("B", "g1", true);
    expect(tabs.claim("B", "g1")).toBeNull();
    tabs.show("C", "g2", true);
    tabs.claim("C", "g2");
    // The user switches from A to B: Chrome hides A first, or shows B first.
    expect(tabs.show("A", "g1", false)).toEqual({ gameId: "g1", from: "A", to: "B" });
    expect(tabs.show("B", "g1", true)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
    expect(tabs.show("B", "g1", false)).toBeNull(); // A is hidden too
    expect(tabs.show("A", "g1", true)).toEqual({ gameId: "g1", from: "B", to: "A" });
    // A visible poller keeps polling, whatever else is shown.
    expect(tabs.show("B", "g1", true)).toBeNull();
    expect(tabs.poller("g1")).toBe("A");
    // Another game's tabs do not count.
    tabs.show("A", "g1", false);
    tabs.show("B", "g1", false);
    expect(tabs.show("C", "g2", true)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
    expect(tabs.poller("g2")).toBe("C");
  });

  it("lets a tab the user looks at take over from a hidden poller when it asks to poll", () => {
    const tabs = new GameTabs<string>();
    // After the worker restarted, the hidden tab happened to ask first.
    tabs.show("A", "g1", false);
    tabs.show("B", "g1", true);
    expect(tabs.claim("A", "g1")).toEqual({ gameId: "g1", to: "A" });
    expect(tabs.claim("B", "g1")).toEqual({ gameId: "g1", from: "A", to: "B" });
    expect(tabs.claim("A", "g1")).toBeNull();
  });

  it("hands polling from a hidden poller to the tab showing the hero's turn", () => {
    const tabs = new GameTabs<string>();
    tabs.show("A", "g1", false);
    tabs.claim("A", "g1");
    tabs.show("B", "g1", false);
    expect(tabs.turn("A", "g1", false)).toBeNull();
    expect(tabs.turn("B", "g1", true)).toEqual({ gameId: "g1", from: "A", to: "B" });
    // Both show the turn now: it does not bounce back.
    expect(tabs.turn("A", "g1", true)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
    // A visible poller keeps polling: the polling tab is told to poll soon instead.
    tabs.show("B", "g1", true);
    tabs.turn("B", "g1", false);
    expect(tabs.turn("A", "g1", true)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
  });

  it("never passes polling back and forth between hidden tabs as turns begin and end", () => {
    const tabs = new GameTabs<string>();
    tabs.show("A", "g1", false);
    tabs.claim("A", "g1");
    tabs.show("B", "g1", false);
    expect(tabs.turn("B", "g1", true)).toEqual({ gameId: "g1", from: "A", to: "B" });
    expect(tabs.turn("A", "g1", true)).toBeNull();
    // B's turn ends first: polling stays with B.
    expect(tabs.turn("B", "g1", false)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
    expect(tabs.turn("A", "g1", false)).toBeNull();
    expect(tabs.poller("g1")).toBe("B");
  });

  it("hands polling from a closed tab to one the user looks at, else one showing the hero's turn", () => {
    const tabs = new GameTabs<string>();
    for (const tab of ["A", "B", "C", "D"]) tabs.show(tab, "g1", tab === "A" || tab === "D");
    tabs.claim("A", "g1");
    tabs.turn("C", "g1", true);
    expect(tabs.close("A")).toEqual({ gameId: "g1", to: "D" });
    expect(tabs.close("D")).toEqual({ gameId: "g1", to: "C" });
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
