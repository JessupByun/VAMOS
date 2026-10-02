import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import type { BackendStatus, ContentToWorker, WorkerToContent } from "../shared/protocol";
import { startLogPoller } from "./log/poller";
import cardCss from "./ui/card.css?inline";

/** Connects one PokerNow game page to the backend; returns a function that tears it down. */
export function startSession(gameId: string): () => void {
  const port = chrome.runtime.connect({ name: "content" });
  const send = (message: ContentToWorker) => port.postMessage(message);
  const listeners = new Set<(status: BackendStatus) => void>();
  let latest: BackendStatus = { type: "backend", state: "connecting" };

  port.onMessage.addListener((message: WorkerToContent) => {
    if (message.type === "backend") {
      latest = message;
      for (const listener of listeners) listener(message);
    } else if (message.type === "snapshot_request") {
      send({
        type: "dom_snapshot",
        gameId,
        capturedAt: Date.now(),
        html: document.documentElement.outerHTML,
      });
    }
  });

  const stopPoller = startLogPoller(gameId, send);

  function Pill() {
    const [status, setStatus] = useState(latest);
    useEffect(() => {
      listeners.add(setStatus);
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

  // Shadow DOM keeps PokerNow's styles out of the pill and the pill's styles out of the page.
  const hostEl = document.createElement("div");
  hostEl.id = "vamos-root";
  document.body.append(hostEl);
  const shadow = hostEl.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = cardCss;
  const mount = document.createElement("div");
  shadow.append(style, mount);
  render(<Pill />, mount);

  return () => {
    stopPoller();
    port.disconnect();
    render(null, mount);
    hostEl.remove();
  };
}
