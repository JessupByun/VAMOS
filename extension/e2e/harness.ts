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

const PROFILE = `---\nname: alex\ntag: Calling station\nplayer_id: ${KNOWN_ID}\n---\n# alex\n`;

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
  hostLog: () => string;
  hostStarts: () => number;
  latestHostPid: () => number | null;
  recordedPages: () => RecordedPage[];
}

export async function launch(): Promise<Harness> {
  const root = mkdtempSync(join(tmpdir(), "vamos-e2e-"));
  const userDataDir = join(root, "profile");
  const dataDir = join(root, "data");
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir);
  writeFileSync(join(profilesDir, "alex.md"), PROFILE);

  const launcher = join(root, "vamos-host");
  writeFileSync(
    launcher,
    `#!/bin/sh\nVAMOS_DATA_DIR="${dataDir}" VAMOS_PROFILES_DIR="${profilesDir}" VAMOS_RECORD=1 exec "${PYTHON}" -m vamos host "$@"\n`,
  );
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
  const requests: string[] = [];

  // Mirrors PokerNow's /log: exclusive created_at bounds, newest first, at most 50 rows.
  async function fakePokerNow(route: Route) {
    const url = new URL(route.request().url());
    if (url.pathname === `/games/${GAME_ID}/log`) {
      requests.push(url.search);
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
      return route.fulfill({ contentType: "text/html", body: page(TABLE_HTML) });
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
    addLogRows,
    addLogLines,
    dataDir,
    logRows: () => [...rows],
    logRequests: () => [...requests],
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
  };
}
