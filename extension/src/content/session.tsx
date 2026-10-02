import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import type { BackendStatus, ContentToWorker, WorkerToContent } from "../shared/protocol";
import { startLogPoller } from "./log/poller";
import cardCss from "./ui/card.css?inline";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;
const ROOT_ID = "vamos-root";

/** Connects one PokerNow game page to the backend; returns a function that tears it down. */
export function startSession(gameId: string): () => void {
  const listeners = new Set<(status: BackendStatus) => void>();
  let latest: BackendStatus = { type: "backend", state: "connecting" };
  let port: chrome.runtime.Port | null = null;
  let stopped = false;
  let retryDelay = RECONNECT_MIN_MS;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  // Set when the backend went away; the next "ready" is then a fresh backend that has none
  // of this game's rows, so the poller re-sends the newest page.
  let backendLost = false;

  const show = (status: BackendStatus) => {
    latest = status;
    for (const listener of listeners) listener(status);
  };
  const send = (message: ContentToWorker): boolean => {
    if (port === null) return false;
    port.postMessage(message);
    return true;
  };
  // Pages go out only to a ready backend, so the cursor never advances past rows the
  // worker had nowhere to deliver.
  const poller = startLogPoller(gameId, (page) => latest.state === "ready" && send(page));

  // After the extension is reloaded or removed this script keeps running but can no longer
  // reach the worker; only a page reload injects the new version.
  const retire = () => {
    stopped = true;
    poller.stop();
    show({ type: "backend", state: "down", detail: "VAMOS was updated, refresh this tab" });
  };

  // The worker may be stopped (30s idle with no backend running) or restarted, which
  // disconnects this port; reconnecting wakes it and gets a fresh status.
  function connect() {
    if (!chrome.runtime?.id) return retire();
    const next = chrome.runtime.connect({ name: "content" });
    port = next;
    next.onMessage.addListener((message: WorkerToContent) => {
      if (message.type === "backend") {
        retryDelay = RECONNECT_MIN_MS;
        if (message.state === "down") backendLost = true;
        if (message.state === "ready" && backendLost) {
          backendLost = false;
          poller.resync();
        }
        show(message);
      } else if (message.type === "snapshot_request") {
        send({
          type: "dom_snapshot",
          gameId,
          capturedAt: Date.now(),
          html: document.documentElement.outerHTML,
        });
      }
    });
    next.onDisconnect.addListener(() => {
      if (port === next) port = null;
      if (stopped) return;
      if (!chrome.runtime?.id) return retire();
      // The worker stopping also closed the native port, so the backend restarts too.
      backendLost = true;
      show({ type: "backend", state: "connecting" });
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
    });
  }

  connect();

  function Pill() {
    const [status, setStatus] = useState(latest);
    useEffect(() => {
      listeners.add(setStatus);
      // A status may have landed between the first render and this subscription.
      setStatus(latest);
      return () => {
        listeners.delete(setStatus);
      };
    }, []);
    const label =
      status.state === "ready"
        ? `backend ready (v${status.version})`
        : status.state === "connecting"
          ? "connecting..."
          : `backend down: ${status.detail ?? "unknown"}`;
    return (
      <div class={`card ${status.state}`}>
        <span class="brand">VAMOS</span>
        <span class="status">{label}</span>
      </div>
    );
  }

  // A previous, orphaned copy of this script may have left its pill behind.
  document.getElementById(ROOT_ID)?.remove();
  // Shadow DOM keeps PokerNow's styles out of the pill and the pill's styles out of the page.
  const hostEl = document.createElement("div");
  hostEl.id = ROOT_ID;
  document.body.append(hostEl);
  const shadow = hostEl.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = cardCss;
  const mount = document.createElement("div");
  shadow.append(style, mount);
  render(<Pill />, mount);

  return () => {
    stopped = true;
    clearTimeout(retryTimer);
    poller.stop();
    port?.disconnect();
    port = null;
    render(null, mount);
    hostEl.remove();
  };
}
