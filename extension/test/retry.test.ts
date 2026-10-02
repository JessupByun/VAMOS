import { describe, expect, it } from "vitest";

import { HostRetry, RETRY_MAX_MS, RETRY_MIN_MS, STABLE_MS } from "../src/background/retry";

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
