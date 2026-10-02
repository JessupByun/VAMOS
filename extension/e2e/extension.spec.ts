import { expect, test } from "@playwright/test";

import { EXTENSION_ID, GAME_ID, type Harness, launch, ORIGIN } from "./harness";

let harness: Harness;

test.beforeEach(async () => {
  harness = await launch();
});

test.afterEach(async () => {
  await harness.context.close();
});

const pillStatus = (page: import("@playwright/test").Page) => page.locator("#vamos-root .status");

test("game page loaded directly shows the pill, launches the host and records log pages", async () => {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/games/${GAME_ID}`);

  await expect(pillStatus(page)).toHaveText(/backend ready \(v\d+\.\d+\.\d+\)/);
  await expect.poll(() => harness.hostLog()).toContain("host 0.1.0 started");
  await expect.poll(() => harness.recordedLogPages()).toBeGreaterThan(0);
});

test("game page reached by client-side navigation from start-game still gets the pill", async () => {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/start-game`);
  await page.click("#create");

  await expect(page).toHaveURL(`${ORIGIN}/games/${GAME_ID}`);
  await expect(pillStatus(page)).toHaveText(/backend ready/);
});

test("side panel shows the backend status of an open table", async () => {
  const game = await harness.context.newPage();
  await game.goto(`${ORIGIN}/games/${GAME_ID}`);
  await expect(pillStatus(game)).toHaveText(/backend ready/);

  const panel = await harness.context.newPage();
  await panel.goto(`chrome-extension://${EXTENSION_ID}/src/sidepanel/index.html`);
  await expect(panel.locator(".state")).toHaveText(/ready/);
});

test("host exits once the last table tab closes", async () => {
  const page = await harness.context.newPage();
  await page.goto(`${ORIGIN}/games/${GAME_ID}`);
  await expect(pillStatus(page)).toHaveText(/backend ready/);

  await page.close();
  await expect.poll(() => harness.hostLog(), { timeout: 10_000 }).toContain("host exiting");
});
