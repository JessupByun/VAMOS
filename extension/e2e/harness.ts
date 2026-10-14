import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { type BrowserContext, chromium, type Page, type Route } from "@playwright/test";

// End-to-end harness: a throwaway Chrome for Testing profile with the built extension
// loaded, the real Python native host registered inside that profile, and PokerNow served
// from local fakes so tests never touch the user's Chrome, data/, or pokernow.com.

export const EXTENSION_ID = "hflgneiimkcoebdkfiblggbfklelebdn";
export const GAME_ID = "e2etest";
export const ORIGIN = "https://www.pokernow.com";
const PAGE_SIZE = 50;

const REPO = resolve(import.meta.dirname, "../..");
const DIST = join(REPO, "extension/dist");
const PYTHON = join(REPO, ".venv/bin/python");

export const HERO_ID = "heroid0001";
export const KNOWN_ID = "known00001";
export const STRANGER_ID = "strange001";

function seat(position: number, name: string, id: string, extra = "") {
  return `<div class="table-player table-player-${position} ${extra}" style="position:absolute;left:${position * 160}px;top:300px;width:150px;height:60px">
    <div class="table-player-infos-ctn"><div class="table-player-name"><span><a href="/players/${id}">${name}</a></span></div></div></div>`;
}

const DECISIONS = `<div class="game-decisions-ctn" style="position:absolute;right:20px;bottom:20px;width:300px;height:80px"><button class="action-button fold">Fold</button></div>`;
const TABLE_HTML = `<div class="table">${seat(1, "TestHero", HERO_ID, "you-player")}${seat(2, "alex", KNOWN_ID)}${seat(3, "new guy", STRANGER_ID)}</div>${DECISIONS}`;
// The same table as a tab where the hero is not seated sees it: the hero's seat is just a seat.
const WATCHING_HTML = TABLE_HTML.replace(" you-player", "");

// Profiles get the first pseudonyms, so alex is "Player A" in every prompt.
export const CLAUDE_ANSWER = {
  reason: "Player A folds to small bets.",
  strategy: [{ action: "bet", size_pct_pot: 50, freq: 1 }],
  plan: "Check behind if Player A calls and the turn is a club.",
};

// Made-up numbers, in the shape of a real profile's frontmatter and stats.
const PROFILE = `---
name: alex
tag: Calling station
player_id: ${KNOWN_ID}
hands: 120
sessions: [A 2026-09-01, B 2026-09-08]
net: +12.40 (A +20.00, B -7.60)
last_updated: 2026-09-09
---
# alex

## Summary

Calls too much and rarely folds a pair. Bluff less, value bet thinner.

## Key stats

- VPIP 48% (58/120), PFR 12% (14/120)
`;

// PokerNow's start-game page swaps to the table with replaceState, not a page load.
const START_HTML = `<button id="create">Create game</button>
<script>
  document.getElementById("create").addEventListener("click", () => {
    history.replaceState("", "", "/games/${GAME_ID}");
    document.body.innerHTML = ${JSON.stringify(TABLE_HTML)};
  });
</script>`;

export interface LogRow {
  at: string;
  created_at: string;
  msg: string;
}

export interface RecordedPage {
  fetchedAt: number;
  body: { logs: LogRow[] };
}

function page(body: string) {
  return `<!doctype html><html><head><title>Poker Now</title></head><body>${body}</body></html>`;
}

export interface Harness {
  context: BrowserContext;
  /** Append rows to the fake game log, as players act. */
  addLogRows: (count: number) => void;
  /** Append real log lines (oldest first) to the fake game log. */
  addLogLines: (lines: string[]) => void;
  dataDir: string;
  logRows: () => LogRow[];
  /** Query strings of every /log request the extension made, in order. */
  logRequests: () => string[];
  /** The same, made from one tab. */
  logRequestsOf: (page: Page) => string[];
  /** Answer every /log request with this HTTP status from now on, or normally again with null. */
  failLog: (status: number | null) => void;
  /** Open the game in a new tab that shows the table without the hero seated. */
  openWatching: () => Promise<Page>;
  /** Show or hide a tab to VAMOS, as Chrome does when the user switches tabs or windows. */
  setVisible: (page: Page, visible: boolean) => Promise<void>;
  hostLog: () => string;
  hostStarts: () => number;
  latestHostPid: () => number | null;
  recordedPages: () => RecordedPage[];
  /**
   * Claude's canned answer from the next call on, e.g. a mixed strategy. Three keys are for
   * the test: `wait_seconds` makes Claude slow, `fail` fails the call with that reason, and
   * `usage` is the tokens Claude reports, to be billed.
   */
  setClaudeAnswer: (answer: object) => void;
  /** Claude's next replies as plain text, such as a profile it writes for Update profiles. */
  setClaudeText: (text: string) => void;
  /**
   * From the next launch on, Chrome cannot start the backend: it is not registered with
   * Chrome ("unregistered"), or it exits at once ("exits"); null works normally again.
   */
  breakHost: (how: "unregistered" | "exits" | null) => void;
  /** Close the browser, let the host exit, and delete the test's temp folder (about 6 MB). */
  close: () => Promise<void>;
}

export async function launch({ record = true }: { record?: boolean } = {}): Promise<Harness> {
  const root = mkdtempSync(join(tmpdir(), "vamos-e2e-"));
  const userDataDir = join(root, "profile");
  const dataDir = join(root, "data");
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir);
  writeFileSync(join(profilesDir, "alex.md"), PROFILE);
  // The rulebook a profile update follows, as in the repo.
  writeFileSync(join(profilesDir, "_GUIDELINES.md"), readFileSync(join(REPO, "profiles/_GUIDELINES.md")));

  // Claude's answer is canned, so no test ever reaches the API.
  const canned = join(root, "claude-answer.json");
  writeFileSync(canned, JSON.stringify(CLAUDE_ANSWER));
  const launcher = join(root, "vamos-host");
  const launcherScript = `#!/bin/sh\nVAMOS_DATA_DIR="${dataDir}" VAMOS_PROFILES_DIR="${profilesDir}" VAMOS_CANNED_CLAUDE="${canned}" ${record ? "VAMOS_RECORD=1 " : ""}exec "${PYTHON}" -m vamos host "$@"\n`;
  writeFileSync(launcher, launcherScript);
  chmodSync(launcher, 0o755);
  const hostsDir = join(userDataDir, "NativeMessagingHosts");
  mkdirSync(hostsDir, { recursive: true });
  const manifestPath = join(hostsDir, "com.vamos.host.json");
  const manifest = JSON.stringify({
    name: "com.vamos.host",
    description: "VAMOS backend (e2e)",
    path: launcher,
    type: "stdio",
    allowed_origins: [`chrome-extension://${EXTENSION_ID}/`],
  });
  writeFileSync(manifestPath, manifest);

  // Newest first, like PokerNow; created_at is epoch ms x 100 plus a sequence number.
  const rows: LogRow[] = [];
  let nextId = 179093160000000n;
  const addLogLines = (lines: string[]) => {
    for (const msg of lines) {
      nextId += 100n;
      rows.unshift({ at: new Date().toISOString(), created_at: nextId.toString(), msg });
    }
  };
  const addLogRows = (count: number) =>
    addLogLines(Array.from({ length: count }, (_, i) => `The game's ante was changed from 0 to ${i}.`));
  addLogRows(2);
  const requests: { query: string; page: Page }[] = [];
  let failStatus: number | null = null;
  const watching = new Set<Page>();

  // Mirrors PokerNow's /log: exclusive created_at bounds, newest first, at most 50 rows.
  async function fakePokerNow(route: Route) {
    const url = new URL(route.request().url());
    if (url.pathname === `/games/${GAME_ID}/log`) {
      requests.push({ query: url.search, page: route.request().frame().page() });
      if (failStatus !== null) {
        // Like a Cloudflare error page: HTML, not JSON.
        return route.fulfill({ status: failStatus, contentType: "text/html", body: page("<h1>Error</h1>") });
      }
      const after = url.searchParams.get("after_at");
      const before = url.searchParams.get("before_at");
      const logs = rows
        .filter(
          (row) =>
            (!after || BigInt(row.created_at) > BigInt(after)) &&
            (!before || BigInt(row.created_at) < BigInt(before)),
        )
        .slice(0, PAGE_SIZE);
      return route.fulfill({ json: { logs, infos: { min: null, max: null } } });
    }
    if (url.pathname === `/games/${GAME_ID}`) {
      const html = watching.has(route.request().frame().page()) ? WATCHING_HTML : TABLE_HTML;
      return route.fulfill({ contentType: "text/html", body: page(html) });
    }
    if (url.pathname === "/start-game") {
      return route.fulfill({ contentType: "text/html", body: page(START_HTML) });
    }
    return route.fulfill({ status: 404, body: "not faked" });
  }

  const context = await chromium.launchPersistentContext(userDataDir, {
    channel: "chromium",
    headless: true,
    args: [`--disable-extensions-except=${DIST}`, `--load-extension=${DIST}`],
  });
  await context.route(`${ORIGIN}/**`, fakePokerNow);

  const hostLogPath = join(dataDir, "logs/host.log");
  const pagesPath = join(dataDir, `recordings/${GAME_ID}/log_pages.jsonl`);
  const hostLog = () => (existsSync(hostLogPath) ? readFileSync(hostLogPath, "utf8") : "");
  return {
    context,
    setClaudeAnswer: (answer) => writeFileSync(canned, JSON.stringify(answer)),
    setClaudeText: (text) => writeFileSync(canned, text),
    breakHost: (how) => {
      if (how === "unregistered") rmSync(manifestPath, { force: true });
      else writeFileSync(manifestPath, manifest);
      writeFileSync(launcher, how === "exits" ? "#!/bin/sh\nexit 1\n" : launcherScript);
    },
    addLogRows,
    addLogLines,
    dataDir,
    logRows: () => [...rows],
    logRequests: () => requests.map((r) => r.query),
    logRequestsOf: (tab) => requests.filter((r) => r.page === tab).map((r) => r.query),
    failLog: (status) => {
      failStatus = status;
    },
    openWatching: async () => {
      const tab = await context.newPage();
      watching.add(tab);
      await tab.goto(`${ORIGIN}/games/${GAME_ID}`);
      return tab;
    },
    setVisible: async (tab, visible) => {
      // Headless Chrome reports every tab visible, even with another in front or its window
      // minimized. So the page's visibility is stubbed where the content script reads it: in
      // its own isolated world, which Playwright's evaluate does not reach.
      const cdp = await context.newCDPSession(tab);
      const worlds: { id: number; origin: string }[] = [];
      cdp.on("Runtime.executionContextCreated", ({ context: world }) => worlds.push(world));
      await cdp.send("Runtime.enable");
      const vamos = worlds.filter((world) => world.origin === `chrome-extension://${EXTENSION_ID}`).at(-1);
      if (vamos === undefined) throw new Error("VAMOS's content script is not running in this tab");
      const state = visible ? "visible" : "hidden";
      await cdp.send("Runtime.evaluate", {
        contextId: vamos.id,
        expression: `Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "${state}" });
          document.dispatchEvent(new Event("visibilitychange"));`,
      });
      await cdp.detach();
    },
    hostLog,
    hostStarts: () => [...hostLog().matchAll(/host \S+ started/g)].length,
    latestHostPid: () => {
      const last = [...hostLog().matchAll(/started \(pid (\d+)\)/g)].at(-1);
      return last ? Number(last[1]) : null;
    },
    recordedPages: () =>
      existsSync(pagesPath)
        ? readFileSync(pagesPath, "utf8")
            .trim()
            .split("\n")
            .map((line) => JSON.parse(line) as RecordedPage)
        : [],
    close: async () => {
      await context.close();
      // The host exits once Chrome closes its port; wait for it so its log is complete and
      // nothing is still writing in the folder.
      const pid = [...hostLog().matchAll(/started \(pid (\d+)\)/g)].map((m) => Number(m[1]));
      for (let waited = 0; waited < 3000 && pid.some(running); waited += 100) {
        await new Promise((r) => setTimeout(r, 100));
      }
      rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
    },
  };
}

function running(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
