import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { type BrowserContext, chromium, type Route } from "@playwright/test";

// End-to-end harness: a throwaway Chrome for Testing profile with the built extension
// loaded, the real Python native host registered inside that profile, and PokerNow served
// from local fakes so tests never touch the user's Chrome, data/, or pokernow.com.

export const EXTENSION_ID = "hflgneiimkcoebdkfiblggbfklelebdn";
export const GAME_ID = "e2etest";
export const ORIGIN = "https://www.pokernow.com";

const REPO = resolve(import.meta.dirname, "../..");
const DIST = join(REPO, "extension/dist");
const PYTHON = join(REPO, ".venv/bin/python");

const TABLE_HTML = `<div class="table"><div class="table-player table-player-1 you-player">
  <div class="table-player-name"><a>TestHero</a></div></div></div>`;

const START_HTML = `<button id="create">Create game</button>
<script>
  document.getElementById("create").addEventListener("click", () => {
    history.pushState({}, "", "/games/${GAME_ID}");
    document.body.innerHTML = ${JSON.stringify(TABLE_HTML)};
  });
</script>`;

const LOG_BODY = {
  logs: [{ at: "2026-10-02T09:00:00.000Z", created_at: 1790931600000, msg: "The game's owner created the game." }],
  infos: {},
};

function page(body: string) {
  return `<!doctype html><html><head><title>Poker Now</title></head><body>${body}</body></html>`;
}

async function fakePokerNow(route: Route) {
  const url = new URL(route.request().url());
  if (url.pathname === `/games/${GAME_ID}/log`) {
    return route.fulfill({ json: LOG_BODY });
  }
  if (url.pathname === `/games/${GAME_ID}`) {
    return route.fulfill({ contentType: "text/html", body: page(TABLE_HTML) });
  }
  if (url.pathname === "/start-game") {
    return route.fulfill({ contentType: "text/html", body: page(START_HTML) });
  }
  return route.fulfill({ status: 404, body: "not faked" });
}

export interface Harness {
  context: BrowserContext;
  dataDir: string;
  hostLog: () => string;
  recordedLogPages: () => number;
}

export async function launch(): Promise<Harness> {
  const root = mkdtempSync(join(tmpdir(), "vamos-e2e-"));
  const userDataDir = join(root, "profile");
  const dataDir = join(root, "data");

  const launcher = join(root, "vamos-host");
  writeFileSync(launcher, `#!/bin/sh\nVAMOS_DATA_DIR="${dataDir}" exec "${PYTHON}" -m vamos host "$@"\n`);
  chmodSync(launcher, 0o755);
  const hostsDir = join(userDataDir, "NativeMessagingHosts");
  mkdirSync(hostsDir, { recursive: true });
  writeFileSync(
    join(hostsDir, "com.vamos.host.json"),
    JSON.stringify({
      name: "com.vamos.host",
      description: "VAMOS backend (e2e)",
      path: launcher,
      type: "stdio",
      allowed_origins: [`chrome-extension://${EXTENSION_ID}/`],
    }),
  );

  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: "chromium",
    headless: true,
    args: [`--disable-extensions-except=${DIST}`, `--load-extension=${DIST}`],
  });
  await context.route(`${ORIGIN}/**`, fakePokerNow);

  const hostLogPath = join(dataDir, "logs/host.log");
  const pagesPath = join(dataDir, `recordings/${GAME_ID}/log_pages.jsonl`);
  return {
    context,
    dataDir,
    hostLog: () => (existsSync(hostLogPath) ? readFileSync(hostLogPath, "utf8") : ""),
    recordedLogPages: () =>
      existsSync(pagesPath) ? readFileSync(pagesPath, "utf8").trim().split("\n").length : 0,
  };
}
