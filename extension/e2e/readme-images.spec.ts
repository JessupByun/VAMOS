import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { EXTENSION_ID, GAME_ID, HERO_ID, KNOWN_ID, ORIGIN, STRANGER_ID, launch } from "./harness";

// Not a check: the README's images, taken on a made-up table drawn with our own CSS (none of
// PokerNow's pages, styles or players). Run with VAMOS_README_IMAGES=1 npx playwright test readme
// after npm run build; the images are written to docs/images/.
const OUT = resolve(import.meta.dirname, "../../docs/images");

const H = `"you @ ${HERO_ID}"`;
const A = `"alex @ ${KNOWN_ID}"`;
const N = `"new guy @ ${STRANGER_ID}"`;

// Three hands so the badges and the replayer have something to show, then the hero's turn on
// the flop of hand #12: top pair against alex, the profiled calling station.
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
  `${H} raises to 0.60`,
  `${A} calls 0.60`,
  `${N} folds`,
  "Flop:  [K♦, 9♣, 4♥]",
  `${A} checks`,
];

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
  .card-s-K::before { content: "K"; } .card-s-Q::before { content: "Q"; } .card-s-9::before { content: "9"; } .card-s-4::before { content: "4"; }
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

test("README images", async () => {
  test.skip(!process.env.VAMOS_README_IMAGES, "set VAMOS_README_IMAGES=1 to write docs/images/");
  const harness = await launch({ record: false });
  try {
    harness.setClaudeAnswer({
      reason:
        "Player A calls flop bets with any pair or draw and rarely raises without two pair, so top pair with a good kicker bets for value.",
      strategy: [
        { action: "bet", size_pct_pot: 70, freq: 0.85 },
        { action: "check", size_pct_pot: 0, freq: 0.15 },
      ],
      plan: "Bet again on most turns; if Player A check-raises, fold one pair.",
    });
    const page = await harness.context.newPage();
    await page.setViewportSize({ width: 1000, height: 680 });
    await page.goto(`${ORIGIN}/games/${GAME_ID}`);
    await expect(page.locator("#vamos-root .status")).toHaveText(/^active/);
    await page.addStyleTag({ content: TABLE_CSS });
    harness.addLogLines(HANDS);
    await page.evaluate(
      ({ hero, known, stranger }) => {
        const chips = (value: string) => `<span class="chips-value"><span class="normal-value">${value}</span></span>`;
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
        seat(hero).classList.add("decision-current");
        seat(hero).insertAdjacentHTML("beforeend", `<div class="table-player-cards">${card("s", "K")}${card("s", "Q")}</div>`);
        seat(known).insertAdjacentHTML(
          "beforeend",
          '<div class="table-player-cards"><div class="card-container"></div><div class="card-container"></div></div>',
        );
        seat(stranger).classList.add("fold");
        document
          .querySelector(".table")!
          .insertAdjacentHTML(
            "afterbegin",
            `<div class="table-pot-size"><div class="main-value">${chips("1.40")}</div></div>` +
              `<div class="table-cards run-1">${card("d", "K")}${card("c", "9")}${card("h", "4")}</div><div class="dealer-chip">D</div>`,
          );
        const buttons = document.querySelector(".game-decisions-ctn")!;
        buttons.innerHTML =
          '<p class="action-signal">Your Turn</p><button class="action-button fold">Fold</button>' +
          '<button class="action-button check">Check</button><button class="action-button raise">Bet</button>';
      },
      { hero: HERO_ID, known: KNOWN_ID, stranger: STRANGER_ID },
    );
    const card = page.locator("#vamos-root .action-card");
    await expect(card.locator(".source")).toContainText("VAMOS! AI");
    await expect(card).toContainText("BET");

    const panel = await harness.context.newPage();
    await panel.setViewportSize({ width: 400, height: 1000 });
    await panel.emulateMedia({ colorScheme: "dark" });
    await panel.goto(`chrome-extension://${EXTENSION_ID}/src/sidepanel/index.html`);
    await expect(panel.locator("section.advice .answer").first()).toBeVisible();
    // Every earlier hand read, so the badges count all three.
    const earlier = panel.getByRole("combobox", { name: "Replay an earlier hand" }).locator("option");
    await expect(earlier).toHaveCount(4);
    await page.bringToFront();
    await page.screenshot({ path: `${OUT}/table.png` });
    await panel.locator("section.advice").screenshot({ path: `${OUT}/panel-hand.png` });
  } finally {
    await harness.close();
  }
});
