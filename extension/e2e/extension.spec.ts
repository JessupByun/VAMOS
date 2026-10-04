import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import {
  EXTENSION_ID,
  GAME_ID,
  type Harness,
  HERO_ID,
  KNOWN_ID,
  launch,
  ORIGIN,
  STRANGER_ID,
} from "./harness";

const POLL_MS = 2000;
const PANEL_URL = `chrome-extension://${EXTENSION_ID}/src/sidepanel/index.html`;

let harness: Harness;

test.beforeEach(async () => {
  harness = await launch();
});

test.afterEach(async () => {
  await harness.context.close();
});

const pillStatus = (page: Page) => page.locator("#vamos-root .status");
const pageCount = () => harness.recordedPages().length;

async function openTable() {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/games/${GAME_ID}`);
  await expect(pillStatus(page)).toHaveText(/^active \(v\d+\.\d+\.\d+\)/);
  return page;
}

/** A live panel answers a snapshot request through panel -> worker -> content -> host. */
async function expectPanelRoundTrip(panel: Page) {
  const acks = panel.locator("li", { hasText: "dom_snapshot" });
  const before = await acks.count();
  await panel.getByRole("button", { name: "Save DOM snapshot" }).click();
  await expect(acks).toHaveCount(before + 1);
}

test("game page loaded directly shows the pill, launches the host and records the log", async () => {
  await openTable();
  await expect.poll(() => harness.hostLog()).toContain("host 0.1.0 started");
  await expect.poll(pageCount).toBe(1);
  expect(harness.recordedPages()[0]?.body.logs).toEqual(harness.logRows());
});

test("game page reached by replaceState from start-game still gets the pill", async () => {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/start-game`);
  await page.click("#create");

  await expect(page).toHaveURL(`${ORIGIN}/games/${GAME_ID}`);
  await expect(pillStatus(page)).toHaveText(/^active/);
});

test("the poller asks only for new rows and delivers each new row once", async () => {
  await openTable();
  await expect.poll(pageCount).toBe(1);
  const newest = harness.logRows()[0]?.created_at;
  await expect.poll(() => harness.logRequests().at(-1)).toBe(`?after_at=${newest}`);

  // Nothing new: later polls deliver nothing.
  await new Promise((r) => setTimeout(r, 2 * POLL_MS + 500));
  expect(pageCount()).toBe(1);

  harness.addLogRows(1);
  await expect.poll(pageCount).toBe(2);
  expect(harness.recordedPages()[1]?.body.logs).toEqual([harness.logRows()[0]]);
});

test("side panel shows the backend status of an open table", async () => {
  await openTable();
  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  await expect(panel.locator(".state")).toHaveText(/active/);
  await expectPanelRoundTrip(panel);
});

test("host exits once the last table tab closes", async () => {
  const page = await openTable();
  await page.close();
  await expect.poll(() => harness.hostLog(), { timeout: 10_000 }).toContain("host exiting");
});

test("a backend that crashes mid-session is relaunched and re-sent the newest rows", async () => {
  await openTable();
  await expect.poll(pageCount).toBe(1);

  const pid = harness.latestHostPid();
  expect(pid).not.toBeNull();
  const killedAt = Date.now();
  process.kill(pid as number, "SIGKILL");

  await expect.poll(() => harness.hostStarts(), { timeout: 15_000 }).toBe(2);
  const page = harness.context.pages().find((p) => p.url().includes(GAME_ID));
  await expect(pillStatus(page as Page)).toHaveText(/^active/);
  // No rows arrived after the first page, so only the post-crash resync can explain this.
  await expect
    .poll(() => harness.recordedPages().some((p) => p.fetchedAt > killedAt && p.body.logs.length === 2))
    .toBe(true);
});

test("pill and side panel recover when Chrome stops the service worker", async () => {
  const game = await openTable();
  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  await expect(panel.locator(".state")).toHaveText(/active/);

  const cdp = await harness.context.newCDPSession(panel);
  await cdp.send("ServiceWorker.enable");
  await cdp.send("ServiceWorker.stopAllWorkers");

  await expect.poll(() => harness.hostStarts(), { timeout: 15_000 }).toBe(2);
  await expect(pillStatus(game)).toHaveText(/^active/);
  // Stale "ready" text would pass a text check; only a live port completes the round trip.
  await expectPanelRoundTrip(panel);
});

test("a side panel open with no table survives an idle worker stop", async () => {
  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  await expect(panel.locator(".state")).toHaveText(/no PokerNow table open/);

  const cdp = await harness.context.newCDPSession(panel);
  await cdp.send("ServiceWorker.enable");
  await cdp.send("ServiceWorker.stopAllWorkers");

  // A panel that never reconnected would keep showing "no PokerNow table open".
  await openTable();
  await expect(panel.locator(".state")).toHaveText(/active/);
});

test("endpoints under a game URL are not treated as a table page", async () => {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/games/${GAME_ID}/log`);
  // The content script runs on every PokerNow page; give it time to (wrongly) mount.
  await page.waitForTimeout(1500);
  await expect(page.locator("#vamos-root")).toHaveCount(0);
  expect(harness.hostStarts()).toBe(0);
});

const badge = (page: Page, id: string) => page.locator(`#vamos-root .badge[data-player="${id}"]`);

test("seat badges show profiles, ask about strangers, and never badge the hero", async () => {
  const page = await openTable();
  await expect(badge(page, KNOWN_ID)).toHaveText("alex");
  await expect(badge(page, STRANGER_ID).locator("select")).toBeVisible();
  await expect(badge(page, HERO_ID)).toHaveCount(0);
});

test("answering who-is-this updates the badge and is remembered", async () => {
  const page = await openTable();
  await badge(page, STRANGER_ID).locator("select").selectOption({ label: "No profile" });
  await expect(badge(page, STRANGER_ID)).toHaveText("new guy");
  const answers = JSON.parse(readFileSync(join(harness.dataDir, "identity.json"), "utf8"));
  expect(answers).toEqual({ [STRANGER_ID]: null });
});

test("tonight's VPIP appears once hands are played", async () => {
  const page = await openTable();
  await expect(badge(page, KNOWN_ID)).toHaveText("alex");
  const [hero, alex, guy] = [
    `"TestHero @ ${HERO_ID}"`,
    `"alex @ ${KNOWN_ID}"`,
    `"new guy @ ${STRANGER_ID}"`,
  ];
  harness.addLogLines([
    `-- starting hand #1 (id: e2eh1)  No Limit Texas Hold'em (dealer: ${hero}) --`,
    `Player stacks: #1 ${hero} (1000) | #2 ${alex} (1000) | #3 ${guy} (1000)`,
    `${alex} posts a small blind of 10`,
    `${guy} posts a big blind of 20`,
    `${hero} folds`,
    `${alex} raises to 60`,
    `${guy} folds`,
    `Uncalled bet of 40 returned to ${alex}`,
    `${alex} collected 40 from pot`,
    "-- ending hand #1 --",
  ]);
  await expect(badge(page, KNOWN_ID)).toHaveText(/^alex · VPIP 100% \(1\/1\) · PFR \d+%$/);
  await expect(badge(page, STRANGER_ID)).toContainText("new guy");
});

test("an answer can be changed or cleared from the side panel", async () => {
  const page = await openTable();
  await badge(page, STRANGER_ID).locator("select").selectOption({ label: "No profile" });
  await expect(badge(page, STRANGER_ID)).toHaveText("new guy");

  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  const change = panel.getByRole("combobox", { name: "Change who new guy is" });
  await change.selectOption({ label: "alex" });
  await expect(badge(page, STRANGER_ID)).toHaveText("alex");

  await change.selectOption({ label: "Forget answer" });
  await expect(badge(page, STRANGER_ID).locator("select")).toBeVisible();
  // Recognized through a profile's player_id, so not changeable in VAMOS.
  await expect(panel.getByRole("combobox", { name: "Change who alex is" })).toHaveCount(0);
});

test("leaving the table and coming back with the back button restores VAMOS", async () => {
  const page = await openTable();
  await page.goto(`${ORIGIN}/start-game`);
  await page.goBack();
  const restored = await page.evaluate(
    () => (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming).type,
  );
  expect(restored).toBe("back_forward");
  await expect(pillStatus(page)).toHaveText(/^active/);
  await expect(badge(page, KNOWN_ID)).toHaveText(/^alex/);
});

const HAND_START = [
  `-- starting hand #1 (id: e2eh1)  No Limit Texas Hold'em (dealer: "new guy @ ${STRANGER_ID}") --`,
  `Player stacks: #1 "TestHero @ ${HERO_ID}" (10.00) | #2 "alex @ ${KNOWN_ID}" (10.00) | #3 "new guy @ ${STRANGER_ID}" (10.00)`,
  "Your hand is A♠, 10♦",
  `"TestHero @ ${HERO_ID}" posts a small blind of 0.10`,
  `"alex @ ${KNOWN_ID}" posts a big blind of 0.20`,
];

/** Puts the fake table where PokerNow would be after "new guy" folds: the hero's turn in the small blind. */
async function heroTurn(page: Page) {
  await page.evaluate(
    ({ hero, known, stranger }) => {
      const chips = (value: string) =>
        `<span class="chips-value"><span class="normal-value">${value}</span></span>`;
      const seat = (id: string) =>
        document.querySelector(`a[href="/players/${id}"]`)?.closest(".table-player") as Element;
      const heroSeat = seat(hero);
      heroSeat.classList.add("decision-current");
      // PokerNow's "Your Turn", above the action buttons.
      document
        .querySelector(".game-decisions-ctn")
        ?.insertAdjacentHTML("afterbegin", '<p class="action-signal">Your Turn</p>');
      heroSeat.insertAdjacentHTML(
        "beforeend",
        `<p class="table-player-bet-value">${chips("0.10")}</p>` +
          '<div class="table-player-cards"><div class="card-container card-s card-s-A flipped"></div>' +
          '<div class="card-container card-d card-s-T flipped"></div></div>',
      );
      seat(known).insertAdjacentHTML(
        "beforeend",
        `<p class="table-player-bet-value">${chips("0.20")}</p>` +
          '<div class="table-player-cards"><div class="card-container"></div><div class="card-container"></div></div>',
      );
      seat(stranger).classList.add("fold");
      document
        .querySelector(".table")
        ?.insertAdjacentHTML(
          "afterbegin",
          `<div class="table-pot-size" style="position:absolute;left:300px;top:200px;width:120px;height:40px"><div class="main-value">${chips("0.00")}</div></div><div class="table-cards run-1"></div>`,
        );
    },
    { hero: HERO_ID, known: KNOWN_ID, stranger: STRANGER_ID },
  );
}

test("the action card shows facts on the hero's turn only once the log has caught up", async () => {
  const page = await openTable();
  const card = page.locator("#vamos-root .action-card");
  await expect(card).toContainText("waiting for your turn");

  harness.addLogLines(HAND_START);
  await heroTurn(page);
  // The fold that made it the hero's turn is not in the log yet.
  await expect(card).toContainText("reading the hand");

  harness.addLogLines([`"new guy @ ${STRANGER_ID}" folds`]);
  // Preflop the chart decides; one facts line follows.
  await expect(card.locator(".source")).toHaveText("Preflop chart");
  await expect(card.locator(".action")).toHaveText(
    /^(FOLD|CALL 0\.10 \(0\.5bb\)|RAISE TO \d+\.\d\d \(\d+(\.\d)?bb\)|ALL IN .*)$/,
  );
  await expect(card.locator(".facts")).toHaveText(/^SB · pot 1\.5bb · need 25% · ATo$/);
  // The card sits at the top of the table, centered just above the pot, clear of every seat.
  const pot = await page.locator(".table-pot-size").boundingBox();
  let box = await card.boundingBox();
  expect(pot && box && Math.abs(box.y + box.height - (pot.y - 10))).toBeLessThan(2);
  expect(pot && box && Math.abs(box.x + box.width / 2 - (pot.x + pot.width / 2))).toBeLessThan(2);
  for (const seat of await page.locator(".table-player").all()) {
    const s = await seat.boundingBox();
    expect(s && box && (s.y >= box.y + box.height || s.x >= box.x + box.width || s.x + s.width <= box.x)).toBe(true);
  }
  // A player sitting at the top sends it to the right, above PokerNow's buttons.
  await page.evaluate(() =>
    document
      .querySelector(".table")
      ?.insertAdjacentHTML(
        "beforeend",
        '<div class="table-player table-player-6" style="position:absolute;left:200px;top:20px;width:700px;height:130px"></div>',
      ),
  );
  const buttons = await page.locator(".game-decisions-ctn").boundingBox();
  await expect
    .poll(async () => {
      box = await card.boundingBox();
      return Boolean(box && buttons && box.y + box.height <= buttons.y && box.x > 640);
    })
    .toBe(true);
  await page.evaluate(() => document.querySelector(".table-player-6")?.remove());
  // Every option is shown, the one drawn marked.
  await expect(card.locator(".chip.picked")).toHaveCount(1);

  // Opening the bet-size panel removes "Your Turn" mid-decision: the card stays.
  await page.evaluate(() => {
    document.querySelector("p.action-signal")?.remove();
    document
      .querySelector(".game-decisions-ctn")
      ?.insertAdjacentHTML("beforeend", '<form class="raise-controller-form"></form>');
  });
  await page.waitForTimeout(1200);
  await expect(card.locator(".source")).toHaveText("Preflop chart");
  // PokerNow dims its action area when it is not the hero's turn, even if the seat mark
  // lags: the card goes back to waiting.
  await page.evaluate(() => document.querySelector(".game-decisions-ctn")?.classList.add("low"));
  await expect(card).toContainText("waiting for your turn");
  await page.evaluate(() => document.querySelector(".game-decisions-ctn")?.classList.remove("low"));

  await page.evaluate(() => document.querySelector(".you-player")?.classList.remove("decision-current"));
  await expect(card).toContainText("waiting for your turn");
});

test("after the flop the card shows Claude's answer and the panel its reasoning", async () => {
  const page = await openTable();
  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  const card = page.locator("#vamos-root .action-card");
  harness.addLogLines([
    ...HAND_START,
    `"new guy @ ${STRANGER_ID}" folds`,
    `"TestHero @ ${HERO_ID}" calls 0.20`,
    `"alex @ ${KNOWN_ID}" checks`,
    "Flop:  [K♠, 7♦, 2♣]",
  ]);
  await heroTurn(page);
  await page.evaluate(() => {
    for (const bet of document.querySelectorAll(".table-player-bet-value")) bet.remove();
    const pot = document.querySelector(".table-pot-size .normal-value");
    if (pot) pot.textContent = "0.40";
    document.querySelector(".table-cards.run-1")!.innerHTML = ["card-s card-s-K", "card-d card-s-7", "card-c card-s-2"]
      .map((cls) => `<div class="card-container ${cls} flipped"></div>`)
      .join("");
  });
  await expect(card.locator(".source")).toHaveText("VAMOS! AI (Claude Opus 5.5 · low)");
  await expect(card.locator(".action")).toHaveText("BET 0.20 (1bb)");
  await expect(card.locator(".facts")).toHaveText(/^SB · pot 2bb · Ace high$/);

  await expect(panel.locator(".advice .why")).toHaveText("alex folds to small bets.");
  await expect(panel.locator(".advice .plan")).toHaveText("Next: Check behind if alex calls and the turn is a club.");
  await expect(panel.getByRole("button", { name: "Ask deeper" })).toBeVisible();
  await expect(panel.locator(".stat.tonight .value")).toHaveText("$0.00");
  // The current hand: the hero's cards and the flop drawn as cards, and the hero's position.
  await expect(panel.locator(".hand-head .mine .pcard")).toHaveText(["A♠", "10♦"]);
  await expect(panel.locator(".hand-head .board .pcard")).toHaveCount(3);
  await expect(panel.locator(".hand-line")).toContainText("SB");
  // The hero sits at the table too; every other player has a notes box.
  await expect(panel.locator(".players li.me .who")).toHaveText("TestHero (You)");
  await expect(panel.getByRole("textbox", { name: "Notes on alex" })).toBeVisible();

  await page.evaluate(() => document.querySelector(".you-player")?.classList.remove("decision-current"));
  await expect(card).toContainText("waiting for your turn");
  await expect(panel.locator(".decision.live")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Ask deeper" })).toHaveCount(0);

  // The turn: the flop's decision stays in the panel above the new one.
  harness.addLogLines([
    `"TestHero @ ${HERO_ID}" bets 0.20`,
    `"alex @ ${KNOWN_ID}" calls 0.20`,
    "Turn: K♠, 7♦, 2♣ [9♥]",
  ]);
  await page.evaluate(() => {
    const pot = document.querySelector(".table-pot-size .normal-value");
    if (pot) pot.textContent = "0.80";
    document
      .querySelector(".table-cards.run-1")
      ?.insertAdjacentHTML("beforeend", '<div class="card-container card-h card-s-9 flipped"></div>');
    document.querySelector(".you-player")?.classList.add("decision-current");
  });
  await expect(card.locator(".action")).toHaveText("BET 0.40 (2bb)");
  await expect(panel.locator(".decision .street")).toHaveText(["Flop", "Turn"]);
  await expect(panel.locator(".decision.live .street")).toHaveText("Turn");
});

// Not a check: screenshots of the card and side panel for reviewing the design by eye.
// Run with VAMOS_SHOTS=<dir> npx playwright test -g "screenshots".
test("screenshots of the card and the side panel", async () => {
  const dir = process.env.VAMOS_SHOTS;
  test.skip(!dir, "set VAMOS_SHOTS to a folder to take screenshots");
  const page = await openTable();
  // PokerNow's table is dark grey around green felt; the card is designed for that.
  await page.addStyleTag({ content: "body { background: #2b2d31; color: #ddd; }" });
  const panel = await harness.context.newPage();
  await panel.setViewportSize({ width: 380, height: 900 });
  await panel.emulateMedia({ colorScheme: "dark" });
  await panel.goto(PANEL_URL);
  const card = page.locator("#vamos-root .action-card");
  await page.screenshot({ path: `${dir}/1-waiting.png` });
  harness.addLogLines(HAND_START);
  await heroTurn(page);
  harness.addLogLines([`"new guy @ ${STRANGER_ID}" folds`]);
  await expect(card.locator(".source")).toHaveText("Preflop chart");
  await card.screenshot({ path: `${dir}/2-chart-card.png` });
  await page.screenshot({ path: `${dir}/2-chart-table.png` });
  harness.addLogLines([
    `"TestHero @ ${HERO_ID}" calls 0.20`,
    `"alex @ ${KNOWN_ID}" checks`,
    "Flop:  [K♠, 7♦, 2♣]",
  ]);
  await page.evaluate(() => {
    for (const bet of document.querySelectorAll(".table-player-bet-value")) bet.remove();
    const pot = document.querySelector(".table-pot-size .normal-value");
    if (pot) pot.textContent = "0.40";
    document.querySelector(".table-cards.run-1")!.innerHTML = ["card-s card-s-K", "card-d card-s-7", "card-c card-s-2"]
      .map((cls) => `<div class="card-container ${cls} flipped"></div>`)
      .join("");
  });
  await expect(card.locator(".action")).toHaveText("BET 0.20 (1bb)");
  await card.screenshot({ path: `${dir}/3-claude-card.png` });
  await panel.locator(".players details summary").first().click();
  await panel.screenshot({ path: `${dir}/4-panel-dark.png`, fullPage: true });
  await panel.emulateMedia({ colorScheme: "light" });
  await panel.screenshot({ path: `${dir}/5-panel-light.png`, fullPage: true });
  await page.evaluate(() => document.querySelector(".you-player")?.classList.remove("decision-current"));
  await expect(card).toContainText("waiting for your turn");
  await page.screenshot({ path: `${dir}/6-waiting-table.png` });
});
