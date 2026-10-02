import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MAX_BACKOFF_MS,
  MIN_GAP_MS,
  PAGE_SIZE,
  POLL_MS,
  startLogPoller,
} from "../src/content/log/poller";
import type { LogPage } from "../src/shared/protocol";

interface Row {
  created_at: string;
  msg: string;
}

/** PokerNow's /log semantics: newest first, exclusive created_at bounds, at most PAGE_SIZE rows. */
class FakePokerNow {
  rows: Row[] = [];
  requests: string[] = [];
  respond: ((url: URL) => Response | Promise<Response>) | null = null;
  private next = 179093160000000n;

  add(count: number) {
    for (let i = 0; i < count; i++) {
      this.next += 100n;
      this.rows.unshift({ created_at: this.next.toString(), msg: `row ${this.next}` });
    }
  }

  page(url: URL): Response {
    const after = url.searchParams.get("after_at");
    const before = url.searchParams.get("before_at");
    const logs = this.rows
      .filter(
        (row) =>
          (!after || BigInt(row.created_at) > BigInt(after)) &&
          (!before || BigInt(row.created_at) < BigInt(before)),
      )
      .slice(0, PAGE_SIZE);
    return Response.json({ logs, infos: {} });
  }

  fetch = async (input: string) => {
    this.requests.push(input);
    const url = new URL(input, "https://www.pokernow.com");
    return this.respond ? this.respond(url) : this.page(url);
  };

  lastQuery(): string {
    return new URL(this.requests.at(-1) ?? "", "https://www.pokernow.com").search;
  }
}

let server: FakePokerNow;
let delivered: LogPage[];
let accept: boolean;
const deliver = (page: LogPage) => {
  if (!accept) return false;
  delivered.push(page);
  return true;
};
const deliveredIds = () =>
  new Set(delivered.flatMap((p) => (p.body as { logs: Row[] }).logs.map((r) => r.created_at)));

beforeEach(() => {
  vi.useFakeTimers();
  server = new FakePokerNow();
  delivered = [];
  accept = true;
  vi.stubGlobal("fetch", server.fetch);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("log poller", () => {
  it("starts without a cursor, then asks only for rows after the newest delivered one", async () => {
    server.add(3);
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);
    expect(server.requests[0]).toBe("/games/g1/log");
    expect(delivered).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(server.lastQuery()).toBe(`?after_at=${server.rows[0]?.created_at}`);
    expect(delivered).toHaveLength(1);

    server.add(1);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(delivered).toHaveLength(2);
    expect((delivered[1]?.body as { logs: Row[] }).logs).toEqual([server.rows[0]]);
    poller.stop();
  });

  it("asks again with the same cursor when a page could not be delivered", async () => {
    server.add(2);
    accept = false;
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(server.requests).toEqual(["/games/g1/log", "/games/g1/log"]);

    accept = true;
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(deliveredIds()).toEqual(new Set(server.rows.map((r) => r.created_at)));
    poller.stop();
  });

  it("walks a burst larger than one page back with before_at, skipping no row", async () => {
    server.add(2);
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);
    const cursorBefore = server.rows[0]?.created_at;

    server.add(3 * PAGE_SIZE + 17);
    for (let i = 0; i < 8; i++) await vi.advanceTimersByTimeAsync(POLL_MS);

    expect(deliveredIds()).toEqual(new Set(server.rows.map((r) => r.created_at)));
    expect(server.requests.some((r) => r.includes(`after_at=${cursorBefore}&before_at=`))).toBe(true);
    expect(server.lastQuery()).toBe(`?after_at=${server.rows[0]?.created_at}`);
    poller.stop();
  });

  it("finishes a backfill even when the last page below the cursor is empty", async () => {
    server.add(1);
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);

    server.add(PAGE_SIZE);
    for (let i = 0; i < 4; i++) await vi.advanceTimersByTimeAsync(POLL_MS);

    expect(deliveredIds()).toEqual(new Set(server.rows.map((r) => r.created_at)));
    expect(server.lastQuery()).toBe(`?after_at=${server.rows[0]?.created_at}`);
    poller.stop();
  });

  it("does not let a response to a request made before resync() move the cursor", async () => {
    server.add(2);
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);

    let release: (() => void) | undefined;
    server.respond = (url) =>
      new Promise((resolve) => {
        release = () => resolve(server.page(url));
      });
    await vi.advanceTimersByTimeAsync(POLL_MS);
    expect(server.lastQuery()).toContain("after_at=");

    server.add(1);
    poller.resync();
    release?.();
    server.respond = null;
    await vi.advanceTimersByTimeAsync(POLL_MS);

    // The next request starts over, so a fresh backend gets the newest page.
    expect(server.lastQuery()).toBe("");
    poller.stop();
  });

  it("backs off on 429 and 503, honoring Retry-After, and recovers", async () => {
    server.add(1);
    let status = 429;
    let retryAfter: string | null = null;
    server.respond = () =>
      new Response("Too many requests", {
        status,
        headers: retryAfter ? { "retry-after": retryAfter } : {},
      });
    const poller = startLogPoller("g1", deliver);
    const requestsAt = async (ms: number) => {
      await vi.advanceTimersByTimeAsync(ms);
      return server.requests.length;
    };

    // t=0 429 -> wait 4s; t=4s 429 -> wait 8s.
    expect(await requestsAt(0)).toBe(1);
    expect(await requestsAt(4000 - 1)).toBe(1);
    expect(await requestsAt(1)).toBe(2);
    // t=12s 503 with Retry-After 20 -> wait 20s (more than the doubled 16s).
    status = 503;
    retryAfter = "20";
    expect(await requestsAt(8000)).toBe(3);
    expect(await requestsAt(20000 - 1)).toBe(3);
    // t=32s 503 -> doubling hits the 30s cap.
    expect(await requestsAt(1)).toBe(4);
    server.respond = null;
    expect(await requestsAt(MAX_BACKOFF_MS - 1)).toBe(4);
    // t=62s succeeds and the cadence drops back to POLL_MS.
    expect(await requestsAt(1)).toBe(5);
    expect(delivered).toHaveLength(1);
    expect(await requestsAt(POLL_MS)).toBe(6);
    poller.stop();
  });

  it("stops polling after stop()", async () => {
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    await vi.advanceTimersByTimeAsync(10 * POLL_MS);
    expect(server.requests).toHaveLength(1);
  });

  it("soon() pulls the next poll forward, but never closer than the minimum gap", async () => {
    server.add(1);
    const poller = startLogPoller("g1", deliver);
    await vi.advanceTimersByTimeAsync(0);
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
    await vi.advanceTimersByTimeAsync(0);
    poller.soon();
    await vi.advanceTimersByTimeAsync(2 * POLL_MS - 1);
    expect(server.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(server.requests).toHaveLength(2);
    poller.stop();
  });
});
