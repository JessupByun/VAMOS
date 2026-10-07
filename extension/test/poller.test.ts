import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type LogPoller,
  MAX_BACKOFF_MS,
  MAX_RETRY_AFTER_MS,
  MIN_GAP_MS,
  PAGE_SIZE,
  POLL_MS,
  startLogPoller,
} from "../src/content/log/poller";
import type { LogPage, LogTrouble } from "../src/shared/protocol";

interface Row {
  created_at: string;
  msg: string;
}

/** PokerNow's /log semantics: newest first, exclusive created_at bounds, at most `pageSize` rows. */
class FakePokerNow {
  rows: Row[] = [];
  requests: string[] = [];
  respond: ((url: URL) => Response | Promise<Response>) | null = null;
  pageSize = PAGE_SIZE;
  private next = 179093160000000n;

  add(count: number) {
    for (let i = 0; i < count; i++) {
      this.next += 100n;
      this.rows.unshift({ created_at: this.next.toString(), msg: `row ${this.next}` });
    }
  }

  page(url: URL, { ignoreBefore = false } = {}): Response {
    const after = url.searchParams.get("after_at");
    const before = ignoreBefore ? null : url.searchParams.get("before_at");
    const logs = this.rows
      .filter(
        (row) =>
          (!after || BigInt(row.created_at) > BigInt(after)) &&
          (!before || BigInt(row.created_at) < BigInt(before)),
      )
      .slice(0, this.pageSize);
    return Response.json({ logs, infos: {} });
  }

  fetch = async (input: string) => {
    this.requests.push(input);
    const url = new URL(input, "https://www.pokernow.com");
    return this.respond ? this.respond(url) : this.page(url);
  };

  query(index: number): string {
    return new URL(this.requests.at(index) ?? "", "https://www.pokernow.com").search;
  }

  lastQuery(): string {
    return this.query(-1);
  }

  newest(): string {
    return this.rows[0]?.created_at ?? "";
  }
}

let server: FakePokerNow;
let delivered: LogPage[];
let accept: boolean;
let reports: (LogTrouble | null)[];
const deliver = (page: LogPage) => {
  if (!accept) return false;
  delivered.push(page);
  return true;
};
const report = (trouble: LogTrouble | null) => reports.push(trouble);
const deliveredIds = () =>
  new Set(delivered.flatMap((p) => (p.body as { logs: Row[] }).logs.map((r) => r.created_at)));
const allIds = () => new Set(server.rows.map((r) => r.created_at));
const ticks = async (count: number) => {
  for (let i = 0; i < count; i++) await vi.advanceTimersByTimeAsync(POLL_MS);
};

/** A poller whose backend already holds every row the fake log has so far. */
async function live(): Promise<LogPoller> {
  const poller = startLogPoller("g1", deliver, report);
  poller.resume(server.newest() || null);
  await vi.advanceTimersByTimeAsync(0);
  return poller;
}

beforeEach(() => {
  vi.useFakeTimers();
  server = new FakePokerNow();
  delivered = [];
  accept = true;
  reports = [];
  vi.stubGlobal("fetch", server.fetch);
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("log poller", () => {
  it("polls nothing until resume(), then starts without a cursor and asks only for newer rows", async () => {
    server.add(3);
    const poller = startLogPoller("g1", deliver);
    await ticks(5);
    expect(server.requests).toEqual([]);

    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(server.requests[0]).toBe("/games/g1/log");
    expect(delivered).toHaveLength(1);
    // Holding nothing of the game, the backend gets the rows before that page too: here none.
    await ticks(1);
    expect(server.lastQuery()).toBe(`?before_at=${server.rows.at(-1)?.created_at}`);
    await ticks(1);
    expect(server.lastQuery()).toBe(`?after_at=${server.newest()}`);
    expect(delivered).toHaveLength(1);

    server.add(1);
    await ticks(1);
    expect(delivered).toHaveLength(2);
    expect((delivered[1]?.body as { logs: Row[] }).logs).toEqual([server.rows[0]]);
    poller.stop();
  });

  it("asks again with the same cursor when a page could not be delivered", async () => {
    server.add(2);
    accept = false;
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    await ticks(1);
    expect(server.requests).toEqual(["/games/g1/log", "/games/g1/log"]);

    accept = true;
    await ticks(1);
    expect(deliveredIds()).toEqual(allIds());
    poller.stop();
  });

  it("walks a burst larger than one page back with before_at, skipping no row", async () => {
    server.add(2);
    const cursorBefore = server.newest();
    const poller = await live();

    server.add(3 * PAGE_SIZE + 17);
    await ticks(8);

    expect(deliveredIds()).toEqual(new Set(server.rows.slice(0, -2).map((r) => r.created_at)));
    expect(server.requests.some((r) => r.includes(`after_at=${cursorBefore}&before_at=`))).toBe(true);
    expect(server.lastQuery()).toBe(`?after_at=${server.newest()}`);
    poller.stop();
  });

  it("finishes a backfill even when the last page below the cursor is empty", async () => {
    server.add(1);
    const poller = await live();

    server.add(PAGE_SIZE);
    await ticks(4);

    expect(deliveredIds()).toEqual(new Set(server.rows.slice(0, -1).map((r) => r.created_at)));
    expect(server.lastQuery()).toBe(`?after_at=${server.newest()}`);
    poller.stop();
  });

  it("keeps walking a burst back after a short page, until a page comes back empty", async () => {
    server.add(2);
    const poller = await live();
    server.add(120);
    await ticks(1); // a full page: the rows below it are walked back
    server.pageSize = 20; // were PokerNow's pages ever shorter than 50 rows
    await ticks(10);
    expect(deliveredIds()).toEqual(new Set(server.rows.slice(0, -2).map((r) => r.created_at)));
    expect(server.lastQuery()).toBe(`?after_at=${server.newest()}`);
    poller.stop();
  });

  it("stops a backfill whose page does not move, instead of fetching it forever", async () => {
    server.add(2);
    const poller = await live();
    // A log that ignored before_at would answer every step of the walk with the same page.
    server.respond = (url) => server.page(url, { ignoreBefore: true });
    server.add(PAGE_SIZE + 10);
    await ticks(6);
    const walks = server.requests.filter((r) => r.includes("before_at="));
    expect(walks).toHaveLength(1);
    expect(server.lastQuery()).toBe(`?after_at=${server.newest()}`);
    expect(console.warn).toHaveBeenCalledOnce();
    poller.stop();
  });

  it("does not let a response to a request made before resume() move the cursor", async () => {
    server.add(2);
    const poller = await live();

    let release: (() => void) | undefined;
    server.respond = (url) =>
      new Promise((resolve) => {
        release = () => resolve(server.page(url));
      });
    await ticks(1);
    expect(server.lastQuery()).toContain("after_at=");

    server.add(1);
    poller.resume(null);
    release?.();
    server.respond = null;
    await ticks(1);

    // The next request starts over, so a backend holding nothing gets the newest page.
    expect(server.lastQuery()).toBe("");
    poller.stop();
  });

  it("resumes after the newest row the backend holds, walking back so no row is skipped", async () => {
    server.add(5);
    const held = server.newest(); // the newest row, held from before a refresh
    server.add(PAGE_SIZE + 10); // more rows than one page came in meanwhile
    const poller = startLogPoller("g1", deliver);
    poller.resume(held);
    await vi.advanceTimersByTimeAsync(0);
    expect(server.lastQuery()).toBe(`?after_at=${held}`);
    await ticks(4);
    // Every row above the held one was delivered, and none at or below it.
    const above = server.rows.filter((r) => BigInt(r.created_at) > BigInt(held)).map((r) => r.created_at);
    expect(deliveredIds()).toEqual(new Set(above));
    poller.stop();
  });

  it("starting with nothing held, walks the whole game back to its start between live polls", async () => {
    server.add(3 * PAGE_SIZE + 7); // the session so far
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    server.add(4); // play goes on during the walk
    await ticks(12);
    // Every row of the game was delivered, old and new, and the walk took turns with live polls.
    expect(deliveredIds()).toEqual(allIds());
    const queries = server.requests.map((r) => new URL(r, "https://www.pokernow.com").search);
    const walks = queries.filter((q) => q.startsWith("?before_at="));
    expect(walks.length).toBeGreaterThanOrEqual(3);
    for (let i = 1; i < queries.length; i += 1) {
      expect(queries[i]?.startsWith("?before_at=") && queries[i - 1]?.startsWith("?before_at=")).toBe(false);
    }
    // History pages never count as fresh, so the backend marks no gap.
    expect(delivered.filter((p) => p.fresh)).toHaveLength(1);
    poller.stop();
  });

  it("walks the whole game back with pages shorter than 50 rows too", async () => {
    server.pageSize = 20;
    server.add(70);
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    await ticks(10);
    expect(deliveredIds()).toEqual(allIds());
    poller.stop();
  });

  it("stops a history walk whose page does not move", async () => {
    server.add(PAGE_SIZE + 10);
    server.respond = (url) => server.page(url, { ignoreBefore: true });
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    await ticks(6);
    expect(server.requests.filter((r) => r.includes("before_at="))).toHaveLength(1);
    poller.stop();
  });

  it("does not walk history when the backend already holds the game", async () => {
    server.add(2 * PAGE_SIZE);
    const held = server.newest();
    server.add(3);
    const poller = startLogPoller("g1", deliver);
    poller.resume(held);
    await ticks(5);
    expect(server.requests.some((r) => r.includes("before_at=") && !r.includes("after_at="))).toBe(false);
    poller.stop();
  });

  it("backs off on 429 and 503, honoring Retry-After, saying so once it is more than one short wait", async () => {
    server.add(1);
    let status = 429;
    let retryAfter: string | null = null;
    server.respond = () =>
      new Response("Too many requests", {
        status,
        headers: retryAfter ? { "retry-after": retryAfter } : {},
      });
    const poller = startLogPoller("g1", deliver, report);
    poller.resume(null);
    const requestsAt = async (ms: number) => {
      await vi.advanceTimersByTimeAsync(ms);
      return server.requests.length;
    };

    // t=0 429 -> wait 4s, routine and not reported; t=4s 429 -> wait 8s, reported.
    expect(await requestsAt(0)).toBe(1);
    expect(reports).toEqual([]);
    expect(await requestsAt(4000 - 1)).toBe(1);
    expect(await requestsAt(1)).toBe(2);
    expect(reports).toEqual([{ problem: "PokerNow's log is busy (HTTP 429)", retryAt: Date.now() + 8000 }]);
    // t=12s 503 with Retry-After 20 -> wait 20s (more than the doubled 16s).
    status = 503;
    retryAfter = "20";
    expect(await requestsAt(8000)).toBe(3);
    expect(reports.at(-1)).toEqual({ problem: "PokerNow's log is busy (HTTP 503)", retryAt: Date.now() + 20000 });
    expect(await requestsAt(20000 - 1)).toBe(3);
    // t=32s 503 -> doubling hits the 30s cap.
    expect(await requestsAt(1)).toBe(4);
    server.respond = null;
    expect(await requestsAt(MAX_BACKOFF_MS - 1)).toBe(4);
    // t=62s succeeds, the report clears, and the cadence drops back to POLL_MS.
    expect(await requestsAt(1)).toBe(5);
    expect(delivered).toHaveLength(1);
    expect(reports.at(-1)).toBeNull();
    expect(await requestsAt(POLL_MS)).toBe(6);
    poller.stop();
  });

  it.each([
    ["120", 120_000],
    ["3600", MAX_RETRY_AFTER_MS],
    [new Date(Date.UTC(2026, 9, 7, 12, 1, 30)).toUTCString(), 90_000],
  ])("waits as long as a Retry-After of %s asks, up to 5 minutes", async (header, wait) => {
    vi.setSystemTime(Date.UTC(2026, 9, 7, 12, 0, 0));
    server.respond = () => new Response("", { status: 429, headers: { "retry-after": header } });
    const poller = startLogPoller("g1", deliver, report);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(reports).toEqual([{ problem: "PokerNow's log is busy (HTTP 429)", retryAt: Date.now() + wait }]);
    await vi.advanceTimersByTimeAsync(wait - 1);
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.requests).toHaveLength(2);
    poller.stop();
  });

  it.each([
    ["a 404", () => new Response("Not found", { status: 404 }), "PokerNow's log is not answering (HTTP 404)"],
    [
      "a Cloudflare page",
      () => new Response("<html>Just a moment...</html>", { status: 403, headers: { "content-type": "text/html" } }),
      "PokerNow's log is not answering (HTTP 403)",
    ],
    ["a 500", () => new Response("", { status: 500 }), "PokerNow's log is not answering (HTTP 500)"],
    ["a 502", () => new Response("Bad gateway", { status: 502 }), "PokerNow's log is not answering (HTTP 502)"],
    [
      "a reply that is not JSON",
      () => new Response("<html>maintenance</html>", { status: 200 }),
      "PokerNow's log sent a reply VAMOS cannot read (not JSON)",
    ],
    [
      "a reply without log rows",
      () => Response.json({ error: "game not found" }),
      "PokerNow's log sent a reply VAMOS cannot read (no log rows)",
    ],
    [
      "no connection",
      () => Promise.reject(new TypeError("Failed to fetch")),
      "PokerNow's log could not be reached (network error)",
    ],
  ])("after %s, says why, keeps backing off, and clears once PokerNow answers", async (_, fail, problem) => {
    server.add(1);
    server.respond = fail;
    const poller = startLogPoller("g1", deliver, report);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(reports).toEqual([{ problem, retryAt: Date.now() + 2 * POLL_MS }]);
    await vi.advanceTimersByTimeAsync(2 * POLL_MS);
    expect(server.requests).toHaveLength(2);
    expect(reports.at(-1)).toEqual({ problem, retryAt: Date.now() + 4 * POLL_MS });
    await vi.advanceTimersByTimeAsync(4 * POLL_MS - 1);
    expect(server.requests).toHaveLength(2);

    server.respond = null;
    await vi.advanceTimersByTimeAsync(1);
    expect(reports.at(-1)).toBeNull();
    expect(delivered).toHaveLength(1);
    await ticks(1);
    expect(server.requests).toHaveLength(4);
    poller.stop();
  });

  it("does not take a reply without log rows for the end of a backfill", async () => {
    server.add(2);
    const poller = await live();
    server.add(70);
    await ticks(1);
    server.respond = () => Response.json({ error: "oops" });
    await ticks(1);
    server.respond = null;
    await ticks(10);
    expect(deliveredIds()).toEqual(new Set(server.rows.slice(0, -2).map((r) => r.created_at)));
    poller.stop();
  });

  it("stops polling after stop()", async () => {
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    poller.resume(null);
    await ticks(10);
    expect(server.requests).toHaveLength(1);
  });

  it("polls nothing while paused, drops what was on its way, and keeps PokerNow's gap on resume", async () => {
    server.add(2);
    const poller = await live();
    let release: (() => void) | undefined;
    server.respond = (url) =>
      new Promise((resolve) => {
        release = () => resolve(server.page(url));
      });
    await ticks(1);
    server.add(1);
    poller.pause();
    release?.();
    server.respond = null;
    await ticks(5);
    expect(server.requests).toHaveLength(2);
    expect(delivered).toHaveLength(0);

    // Resumed after the row the backend holds; the row the dropped answer had comes again.
    poller.resume(server.rows[1]?.created_at ?? null);
    await vi.advanceTimersByTimeAsync(0);
    expect(server.requests).toHaveLength(3);
    expect(deliveredIds()).toEqual(new Set([server.newest()]));

    // A resume soon after a request waits out the rest of the poll interval.
    poller.pause();
    poller.resume(server.newest());
    await vi.advanceTimersByTimeAsync(POLL_MS - 1);
    expect(server.requests).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.requests).toHaveLength(4);
    poller.stop();
  });

  it("clears its report when paused, since nothing is being retried", async () => {
    server.respond = () => new Response("", { status: 404 });
    const poller = startLogPoller("g1", deliver, report);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    expect(reports).toHaveLength(1);
    poller.pause();
    expect(reports.at(-1)).toBeNull();
    poller.stop();
  });

  it("soon() pulls the next poll forward, but never closer than the minimum gap", async () => {
    server.add(1);
    const poller = await live();
    expect(server.requests).toHaveLength(1);

    poller.soon();
    await vi.advanceTimersByTimeAsync(MIN_GAP_MS - 1);
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.requests).toHaveLength(2);

    // Well after the last request, soon() asks right away and the normal cadence follows.
    await vi.advanceTimersByTimeAsync(POLL_MS - 100);
    expect(server.requests).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(100);
    expect(server.requests).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(MIN_GAP_MS + 100);
    poller.soon();
    await vi.advanceTimersByTimeAsync(0);
    expect(server.requests).toHaveLength(4);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(server.requests).toHaveLength(5);
    poller.stop();
  });

  it("soon() does not cut a backoff short", async () => {
    server.respond = () => new Response("", { status: 429 });
    const poller = startLogPoller("g1", deliver);
    poller.resume(null);
    await vi.advanceTimersByTimeAsync(0);
    poller.soon();
    await vi.advanceTimersByTimeAsync(2 * POLL_MS - 1);
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.requests).toHaveLength(2);
    poller.stop();
  });

  it("soon() does not start a poller that is not polling", async () => {
    const poller = startLogPoller("g1", deliver);
    poller.soon();
    await ticks(3);
    expect(server.requests).toHaveLength(0);
    poller.stop();
  });
});
