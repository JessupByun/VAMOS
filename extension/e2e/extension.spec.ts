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
  await expect(pillStatus(page)).toHaveText(/backend ready \(v\d+\.\d+\.\d+\)/);
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
  await expect(pillStatus(page)).toHaveText(/backend ready/);
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
  await expect(panel.locator(".state")).toHaveText(/ready/);
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
  await expect(pillStatus(page as Page)).toHaveText(/backend ready/);
  // No rows arrived after the first page, so only the post-crash resync can explain this.
  await expect
    .poll(() => harness.recordedPages().some((p) => p.fetchedAt > killedAt && p.body.logs.length === 2))
    .toBe(true);
});

test("pill and side panel recover when Chrome stops the service worker", async () => {
  const game = await openTable();
  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  await expect(panel.locator(".state")).toHaveText(/ready/);

  const cdp = await harness.context.newCDPSession(panel);
  await cdp.send("ServiceWorker.enable");
  await cdp.send("ServiceWorker.stopAllWorkers");

  await expect.poll(() => harness.hostStarts(), { timeout: 15_000 }).toBe(2);
  await expect(pillStatus(game)).toHaveText(/backend ready/);
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
  await expect(panel.locator(".state")).toHaveText(/ready/);
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
  await expect(badge(page, KNOWN_ID)).toHaveText("alex · Calling station");
  await expect(badge(page, STRANGER_ID).locator("select")).toBeVisible();
  await expect(badge(page, HERO_ID)).toHaveCount(0);
});

test("answering who-is-this updates the badge and is remembered", async () => {
  const page = await openTable();
  await badge(page, STRANGER_ID).locator("select").selectOption({ label: "No profile" });
  await expect(badge(page, STRANGER_ID)).toHaveText("new guy · no profile");
  const answers = JSON.parse(readFileSync(join(harness.dataDir, "identity.json"), "utf8"));
  expect(answers).toEqual({ [STRANGER_ID]: null });
});

test("tonight's VPIP appears once hands are played", async () => {
  const page = await openTable();
  await expect(badge(page, KNOWN_ID)).toHaveText("alex · Calling station");
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
  await expect(badge(page, KNOWN_ID)).toHaveText("alex · Calling station · VPIP 1/1 tonight");
  await expect(badge(page, STRANGER_ID)).toContainText("new guy");
});

test("an answer can be changed or cleared from the side panel", async () => {
  const page = await openTable();
  await badge(page, STRANGER_ID).locator("select").selectOption({ label: "No profile" });
  await expect(badge(page, STRANGER_ID)).toHaveText("new guy · no profile");

  const panel = await harness.context.newPage();
  await panel.goto(PANEL_URL);
  const change = panel.getByRole("combobox", { name: "Change who new guy is" });
  await change.selectOption({ label: "alex" });
  await expect(badge(page, STRANGER_ID)).toHaveText("alex · Calling station");

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
  await expect(pillStatus(page)).toHaveText(/backend ready/);
  await expect(badge(page, KNOWN_ID)).toHaveText(/alex · Calling station/);
});
