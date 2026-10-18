import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { EXTENSION_ID, GAME_ID, HERO_ID, KNOWN_ID, ORIGIN, STRANGER_ID, launch } from "./harness";

// Not a check: the README's images, taken on a made-up table drawn with our own CSS (none of
// PokerNow's pages, styles or players). Run with VAMOS_README_IMAGES=1 npx playwright test readme
// after npm run build; the images are written to docs/images/.
const OUT = resolve(import.meta.dirname, "../../docs/images");

const H = `"you @ ${HERO_ID}"`;
const A = `"alex @ ${KNOWN_ID}"`;
const N = `"new guy @ ${STRANGER_ID}"`;

// A made-up profile in the rulebook's shape (profiles/_GUIDELINES.md), for the profile viewer.
const PROFILE = `---
name: alex
tag: Calling station
player_id: ${KNOWN_ID}
hands: 412
sessions: [A 2026-09-01, B 2026-09-08, C 2026-09-15]
net: -38.20 (A -20.00, B -7.60, C -10.60)
last_updated: 2026-09-16
---

## Summary
Loose-passive: limps a lot, calls flop bets with any pair or draw, and rarely raises without two pair or better.
Bet bigger for value and stop bluffing him on the river.

## Key stats
Source: sessions A to C, ring hands only.
- VPIP 51% (198/388), PFR 9% (35/388)
- 3-bet vs an open 3% (4/131). Facing a 3-bet: fold 40% (2/5)
- Flop: C-bets 58% (11/19). Faced a bet: fold 28% (31/111)
- Went to showdown 41% (78/190), won at showdown 46% (36/78)
- Recent: VPIP 56% (61/109), faced a flop bet: fold 22% (9/41)

## How to exploit
- Value bet thinner and bigger on every street: he called a 75% pot river with fifth pair in C88.
- Do not bluff the river: he folded to a river bet 3 times in 24 (low confidence on sizes).
- His check-raise means two pair or better: all 6 he showed were (C14, C61, B33).
`;

// Three hands so the badges and the replayer have something to show, then hand #12, played
// out: a preflop rule, then Claude on the flop and the turn against alex, the calling station.
const HANDS = [
  `-- starting hand #9 (id: demo9)  No Limit Texas Hold'em (dealer: ${H}) --`,
  `Player stacks: #1 ${H} (10.00) | #2 ${A} (10.00) | #3 ${N} (10.00)`,
  "Your hand is 7♦, 2♠",
  `${A} posts a small blind of 0.10`,
  `${N} posts a big blind of 0.20`,
  `${H} folds`,
  `${A} calls 0.20`,
  `${N} checks`,
  "Flop:  [J♠, 6♣, 6♦]",
  `${A} checks`,
  `${N} bets 0.20`,
  `${A} calls 0.20`,
  "Turn: J♠, 6♣, 6♦ [A♣]",
  `${A} checks`,
  `${N} checks`,
  "River: J♠, 6♣, 6♦, A♣ [9♦]",
  `${A} checks`,
  `${N} checks`,
  `${A} shows a J♦, 3♥.`,
  `${N} shows a 8♣, 7♣.`,
  `${A} collected 0.80 from pot`,
  "-- ending hand #9 --",
  `-- starting hand #10 (id: demo10)  No Limit Texas Hold'em (dealer: ${A}) --`,
  `Player stacks: #1 ${H} (10.00) | #2 ${A} (10.40) | #3 ${N} (9.60)`,
  "Your hand is 9♥, 8♥",
  `${N} posts a small blind of 0.10`,
  `${H} posts a big blind of 0.20`,
  `${A} calls 0.20`,
  `${N} calls 0.20`,
  `${H} checks`,
  "Flop:  [8♠, 5♦, 2♥]",
  `${N} checks`,
  `${H} bets 0.40`,
  `${A} calls 0.40`,
  `${N} folds`,
  "Turn: 8♠, 5♦, 2♥ [J♣]",
  `${H} checks`,
  `${A} checks`,
  "River: 8♠, 5♦, 2♥, J♣ [3♠]",
  `${H} bets 0.60`,
  `${A} calls 0.60`,
  `${H} shows a 9♥, 8♥.`,
  `${A} shows a 5♣, 4♣.`,
  `${H} collected 2.60 from pot`,
  "-- ending hand #10 --",
  `-- starting hand #11 (id: demo11)  No Limit Texas Hold'em (dealer: ${N}) --`,
  `Player stacks: #1 ${H} (11.40) | #2 ${A} (9.20) | #3 ${N} (9.40)`,
  "Your hand is A♥, K♥",
  `${H} posts a small blind of 0.10`,
  `${A} posts a big blind of 0.20`,
  `${N} folds`,
  `${H} raises to 0.60`,
  `${A} calls 0.60`,
  "Flop:  [Q♥, 10♦, 3♣]",
  `${H} bets 0.60`,
  `${A} calls 0.60`,
  "Turn: Q♥, 10♦, 3♣ [7♠]",
  `${H} checks`,
  `${A} checks`,
  "River: Q♥, 10♦, 3♣, 7♠ [2♦]",
  `${H} checks`,
  `${A} checks`,
  `${A} shows a Q♣, 4♣.`,
  `${H} shows a A♥, K♥.`,
  `${A} collected 2.40 from pot`,
  "-- ending hand #11 --",
  `-- starting hand #12 (id: demo12)  No Limit Texas Hold'em (dealer: ${H}) --`,
  `Player stacks: #1 ${H} (10.20) | #2 ${A} (10.40) | #3 ${N} (9.40)`,
  "Your hand is K♠, Q♠",
  `${A} posts a small blind of 0.10`,
  `${N} posts a big blind of 0.20`,
];
const TO_FLOP = [`${H} raises to 0.60`, `${A} calls 0.60`, `${N} folds`, "Flop:  [K♦, 9♣, 4♥]", `${A} checks`];
const TO_TURN = [`${H} bets 1.00`, `${A} calls 1.00`, "Turn: K♦, 9♣, 4♥ [2♣]", `${A} checks`];
const TO_END = [
  `${H} bets 2.40`,
  `${A} folds`,
  `Uncalled bet of 2.40 returned to ${H}`,
  `${H} collected 3.40 from pot`,
  "-- ending hand #12 --",
];
// Billed like a real call with the profiles cached, so the costs read like a session's.
const USAGE = { input_tokens: 900, cache_read_input_tokens: 9000, output_tokens: 160 };

// A felt table, seats on its rim and card faces, all drawn here.
const TABLE_CSS = `
  body { margin: 0; background: #1b1d21; color: #e8e8e8; font: 15px -apple-system, "Segoe UI", sans-serif; }
  .table { position: relative; width: 1000px; height: 600px; margin: 0 auto; }
  .table::before { content: ""; position: absolute; left: 70px; top: 60px; width: 860px; height: 420px;
    border-radius: 210px; background: radial-gradient(ellipse at center, #2f7d4f 0%, #1d5a37 75%);
    border: 14px solid #3b2b1f; box-shadow: inset 0 0 50px rgba(0,0,0,.45), 0 10px 30px rgba(0,0,0,.5); }
  .table-player { width: 150px !important; height: 74px !important; box-sizing: border-box; padding: 10px 14px;
    background: #24272c; border: 1px solid #3a3e45; border-radius: 12px; }
  .table-player.fold { opacity: .55; }
  .table-player.decision-current { border-color: #e9c46a; box-shadow: 0 0 0 2px rgba(233,196,106,.35); }
  .table-player-1 { left: 425px !important; top: 486px !important; }
  .table-player-2 { left: 18px !important; top: 236px !important; }
  .table-player-3 { left: 832px !important; top: 236px !important; }
  .table-player-name a { color: #fff; text-decoration: none; font-weight: 600; }
  .demo-stack { color: #b9c0c8; font-size: 14px; margin-top: 4px; }
  .table-player-cards { position: absolute; top: -50px; left: 30px; display: flex; gap: 4px; }
  .card-container { width: 40px; height: 56px; border-radius: 6px; background: repeating-linear-gradient(45deg, #2a4f9a 0 6px, #24447f 6px 12px);
    border: 2px solid #f2f2f2; box-sizing: border-box; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 17px; }
  .card-container.flipped { background: #fafafa; color: #1b1b1b; }
  .card-container.card-h, .card-container.card-d { color: #c62828; }
  .card-s-A::before { content: "A"; } .card-s-K::before { content: "K"; } .card-s-Q::before { content: "Q"; }
  .card-s-J::before { content: "J"; } .card-s-T::before { content: "10"; } .card-s-9::before { content: "9"; }
  .card-s-8::before { content: "8"; } .card-s-7::before { content: "7"; } .card-s-6::before { content: "6"; }
  .card-s-5::before { content: "5"; } .card-s-4::before { content: "4"; } .card-s-3::before { content: "3"; }
  .card-s-2::before { content: "2"; }
  .table-player-bet-value { position: absolute; top: -26px; right: -8px; color: #f5f5f5; font-size: 13px; }
  .card-container.card-s::after { content: "\\2660"; } .card-container.card-h::after { content: "\\2665"; }
  .card-container.card-d::after { content: "\\2666"; } .card-container.card-c::after { content: "\\2663"; }
  .table-cards.run-1 { position: absolute; left: 390px; top: 262px; display: flex; gap: 8px; }
  .table-cards .card-container { width: 52px; height: 72px; font-size: 21px; }
  .table-pot-size { position: absolute; left: 440px; top: 224px; width: 120px; text-align: center; color: #f5f5f5; font-weight: 600; }
  .table-pot-size::before { content: "pot "; color: #cfe3d6; font-weight: 400; }
  .dealer-chip { position: absolute; left: 590px; top: 446px; width: 26px; height: 26px; border-radius: 50%;
    background: #fff; color: #222; font-weight: 700; font-size: 13px; display: flex; align-items: center; justify-content: center; }
  .game-decisions-ctn { position: absolute; right: 30px; bottom: -64px; display: flex; gap: 8px; align-items: center; }
  .game-decisions-ctn .action-signal { margin: 0 8px 0 0; color: #e9c46a; font-weight: 600; }
  .action-button { font: inherit; font-weight: 600; padding: 8px 18px; border-radius: 8px; border: 0; background: #3a3e45; color: #fff; }
`;

const FLOP_ANSWER = {
  reason:
    "Player A calls flop bets with any pair or draw and rarely raises without two pair, so top pair with a good kicker bets for value.",
  strategy: [
    { action: "bet", size_pct_pot: 70, freq: 0.85 },
    { action: "check", size_pct_pot: 0, freq: 0.15 },
  ],
  plan: "Bet again on most turns; if Player A check-raises, fold one pair.",
  usage: USAGE,
};
const TURN_ANSWER = {
  reason: "Player A calls a second barrel with any king or nine and almost never bluff-raises, so keep betting.",
  strategy: [{ action: "bet", size_pct_pot: 70, freq: 1 }],
  plan: "Bet the river again unless a straight or flush card comes.",
  usage: USAGE,
};

interface Spot {
  pot: string;
  bets?: Record<string, string>;
  board?: [string, string][];
}

/** The hero's turn as the table draws it: the pot, the bets in front, the board. */
async function heroTurn(page: Page, spot: Spot) {
  await page.evaluate(
    ({ spot, hero }) => {
      const chips = (value: string) => `<span class="chips-value"><span class="normal-value">${value}</span></span>`;
      const seat = (id: string) => document.querySelector(`a[href="/players/${id}"]`)!.closest(".table-player")!;
      for (const [id, value] of Object.entries(spot.bets ?? {})) {
        seat(id).insertAdjacentHTML("beforeend", `<p class="table-player-bet-value">${chips(value)}</p>`);
      }
      document.querySelector(".table-pot-size .main-value")!.innerHTML = chips(spot.pot);
      document.querySelector(".table-cards.run-1")!.innerHTML = (spot.board ?? [])
        .map(([suit, rank]) => `<div class="card-container card-${suit} card-s-${rank} flipped"></div>`)
        .join("");
      seat(hero).classList.add("decision-current");
    },
    { spot, hero: HERO_ID },
  );
}

/** The hero acted: the turn is over and the bets go in. */
async function heroActed(page: Page) {
  await page.evaluate(() => {
    document.querySelector(".you-player")!.classList.remove("decision-current");
    for (const bet of document.querySelectorAll(".table-player-bet-value")) bet.remove();
  });
}

test("README images", async () => {
  test.skip(!process.env.VAMOS_README_IMAGES, "set VAMOS_README_IMAGES=1 to write docs/images/");
  const harness = await launch({ record: false });
  try {
    writeFileSync(join(dirname(harness.dataDir), "profiles", "alex.md"), PROFILE);
    const page = await harness.context.newPage();
    await page.setViewportSize({ width: 1000, height: 680 });
    await page.goto(`${ORIGIN}/games/${GAME_ID}`);
    await expect(page.locator("#vamos-root .status")).toHaveText(/^active/);
    await page.addStyleTag({ content: TABLE_CSS });
    harness.addLogLines(HANDS);
    await page.evaluate(
      ({ hero, known, stranger }) => {
        const card = (suit: string, rank: string) => `<div class="card-container card-${suit} card-s-${rank} flipped"></div>`;
        const seat = (id: string) => document.querySelector(`a[href="/players/${id}"]`)!.closest(".table-player")!;
        for (const { id, name, stack } of [
          { id: hero, name: "you", stack: "9.60" },
          { id: known, name: "alex", stack: "9.80" },
          { id: stranger, name: "new guy", stack: "9.20" },
        ]) {
          seat(id).querySelector("a")!.textContent = name;
          seat(id).querySelector(".table-player-infos-ctn")!.insertAdjacentHTML("beforeend", `<div class="demo-stack">${stack}</div>`);
        }
        seat(hero).insertAdjacentHTML("beforeend", `<div class="table-player-cards">${card("s", "K")}${card("s", "Q")}</div>`);
        seat(known).insertAdjacentHTML(
          "beforeend",
          '<div class="table-player-cards"><div class="card-container"></div><div class="card-container"></div></div>',
        );
        document
          .querySelector(".table")!
          .insertAdjacentHTML(
            "afterbegin",
            '<div class="table-pot-size"><div class="main-value"></div></div><div class="table-cards run-1"></div><div class="dealer-chip">D</div>',
          );
        document.querySelector(".game-decisions-ctn")!.innerHTML =
          '<p class="action-signal">Your Turn</p><button class="action-button fold">Fold</button>' +
          '<button class="action-button check">Check</button><button class="action-button raise">Bet</button>';
      },
      { hero: HERO_ID, known: KNOWN_ID, stranger: STRANGER_ID },
    );
    const card = page.locator("#vamos-root .action-card");

    // Preflop: king-queen suited first in on the button, which a rule answers.
    await heroTurn(page, { pot: "0.00", bets: { [KNOWN_ID]: "0.10", [STRANGER_ID]: "0.20" } });
    await expect(card.locator(".source")).toContainText("Preflop rule");
    await heroActed(page);

    // The flop: Claude decides, against the profiled calling station.
    harness.setClaudeAnswer(FLOP_ANSWER);
    harness.addLogLines(TO_FLOP);
    await page.evaluate((id) => document.querySelector(`a[href="/players/${id}"]`)!.closest(".table-player")!.classList.add("fold"), STRANGER_ID);
    await heroTurn(page, { pot: "1.40", board: [["d", "K"], ["c", "9"], ["h", "4"]] });
    await expect(card.locator(".source")).toContainText("VAMOS! AI");
    await expect(card).toContainText("BET $1.00");

    const panel = await harness.context.newPage();
    await panel.setViewportSize({ width: 400, height: 1000 });
    await panel.emulateMedia({ colorScheme: "dark" });
    await panel.goto(`chrome-extension://${EXTENSION_ID}/src/sidepanel/index.html`);
    await expect(panel.locator("section.advice .answer").first()).toBeVisible();
    // Every earlier hand read, so the badges count all three.
    const picker = panel.getByRole("combobox", { name: "Replay an earlier hand" });
    await expect(picker.locator("option")).toHaveCount(4);
    const alex = panel.locator(".players li.player", { has: panel.locator(".who", { hasText: /^alex$/ }) });
    await alex
      .getByPlaceholder("Notes for this session, e.g. very bluffy")
      .fill("Called the river with fives two hands ago.");
    await page.bringToFront();
    await panel.bringToFront();
    await panel.locator("section.advice").screenshot({ path: `${OUT}/panel-hand.png` });
    await alex.locator("details.file summary").click();
    await alex.screenshot({ path: `${OUT}/panel-player.png` });

    // The turn: Claude again, then alex folds and the hand goes to the replayer.
    await heroActed(page);
    harness.setClaudeAnswer(TURN_ANSWER);
    harness.addLogLines(TO_TURN);
    await heroTurn(page, { pot: "3.40", board: [["d", "K"], ["c", "9"], ["h", "4"], ["c", "2"]] });
    await expect(card).toContainText("BET $2.40");
    await heroActed(page);
    harness.addLogLines(TO_END);
    await expect(picker.locator("option")).toHaveCount(5);
    await picker.selectOption({ index: 1 });
    await expect(panel.locator("section.replay .vamos").first()).toBeVisible();
    await panel.locator("section.replay").screenshot({ path: `${OUT}/panel-replay.png` });

    // The top of the panel: the switches, the model and the costs.
    await panel.evaluate(() => window.scrollTo(0, 0));
    const costs = await panel.locator("section.card", { hasText: "API costs" }).boundingBox();
    if (costs) {
      await panel.screenshot({ path: `${OUT}/panel-top.png`, clip: { x: 0, y: 0, width: 400, height: costs.y + costs.height + 8 } });
    }
  } finally {
    await harness.close();
  }
});
