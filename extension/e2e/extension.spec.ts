import { expect, type Page, test } from "@playwright/test";

import { EXTENSION_ID, GAME_ID, type Harness, launch, ORIGIN } from "./harness";

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
