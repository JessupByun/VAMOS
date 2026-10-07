import type { BackendStatus } from "../shared/protocol";

export const RETRY_MIN_MS = 1000;
export const RETRY_MAX_MS = 30000;
export const STABLE_MS = 30000;

/**
 * Backoff for relaunching the native host. The delay doubles on every failure and only
 * resets once a host has stayed up for STABLE_MS, so a host that dies right after "ready"
 * (for example on a log page it cannot handle) backs off instead of restarting every few
 * seconds.
 */
export class HostRetry {
  private delay = RETRY_MIN_MS;
  private readyAt: number | null = null;

  ready(now: number) {
    this.readyAt = now;
  }

  /** How long to wait before relaunching a host that went away at `now`. */
  failed(now: number): number {
    if (this.readyAt !== null && now - this.readyAt >= STABLE_MS) this.delay = RETRY_MIN_MS;
    this.readyAt = null;
    const wait = this.delay;
    this.delay = Math.min(this.delay * 2, RETRY_MAX_MS);
    return wait;
  }

  reset() {
    this.delay = RETRY_MIN_MS;
    this.readyAt = null;
  }
}

/** What to do when Chrome could not start the backend: not registered, or it exited at once. */
export const START_FIX = "Run uv run vamos install in the VAMOS folder, then reload VAMOS";

/**
 * The status once the backend's port closed with a table still open, relaunching in `waitMs`.
 * A backend that never answered could not start: Chrome's own words alone do not say what to
 * do, so the fix comes with them. One that had answered was running and stopped.
 */
export function hostDown(reason: string, started: boolean, waitMs: number): BackendStatus {
  const retrying = `retrying in ${Math.round(waitMs / 1000)} s`;
  if (started) return { type: "backend", state: "down", detail: `${reason} (${retrying})` };
  return {
    type: "backend",
    state: "down",
    detail: `the backend could not start (${reason.replace(/\.$/, "")}), ${retrying}`,
    fix: START_FIX,
  };
}
