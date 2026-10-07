import { describe, expect, it } from "vitest";

import { HostRetry, hostDown, RETRY_MAX_MS, RETRY_MIN_MS, STABLE_MS, START_FIX } from "../src/background/retry";

describe("host retry backoff", () => {
  it("doubles on consecutive failures up to the cap", () => {
    const retry = new HostRetry();
    const waits = Array.from({ length: 8 }, (_, i) => retry.failed(i));
    expect(waits.slice(0, 5)).toEqual([1000, 2000, 4000, 8000, 16000]);
    expect(waits.at(-1)).toBe(RETRY_MAX_MS);
  });

  it("keeps backing off when a host dies soon after becoming ready", () => {
    const retry = new HostRetry();
    retry.failed(0);
    retry.ready(1000);
    expect(retry.failed(3000)).toBe(2000);
    retry.ready(4000);
    expect(retry.failed(5000)).toBe(4000);
  });

  it("resets once a host stayed up long enough", () => {
    const retry = new HostRetry();
    retry.failed(0);
    retry.failed(1);
    retry.ready(10);
    expect(retry.failed(10 + STABLE_MS)).toBe(RETRY_MIN_MS);
  });

  it("starts over after reset()", () => {
    const retry = new HostRetry();
    retry.failed(0);
    retry.failed(1);
    retry.reset();
    expect(retry.failed(2)).toBe(RETRY_MIN_MS);
  });
});

describe("the status after the backend stops", () => {
  it("says how to fix a backend Chrome could not start, with Chrome's own words kept", () => {
    expect(hostDown("Specified native messaging host not found.", false, 2000)).toEqual({
      type: "backend",
      state: "down",
      detail: "the backend could not start (Specified native messaging host not found), retrying in 2s",
      fix: START_FIX,
    });
    expect(START_FIX).toBe("Run uv run vamos install in the VAMOS folder, then reload VAMOS");
    // Exiting before it ever answered is a failed start too.
    expect(hostDown("Native host has exited.", false, 4000).fix).toBe(START_FIX);
  });

  it("keeps the plain reason for a backend that was running", () => {
    expect(hostDown("Native host has exited.", true, 1000)).toEqual({
      type: "backend",
      state: "down",
      detail: "Native host has exited. (retrying in 1s)",
    });
  });
});
