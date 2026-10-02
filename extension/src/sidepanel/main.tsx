import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import type { BackendStatus, HostAck, PanelToWorker, WorkerToPanel } from "../shared/protocol";
import "./panel.css";

// The worker replies the moment the port connects, so the listener must be attached here,
// synchronously, not later in a component effect, or the first status is dropped.
const port = chrome.runtime.connect({ name: "panel" });
let status: BackendStatus = { type: "backend", state: "connecting" };
let acks: HostAck[] = [];
const subscribers = new Set<() => void>();

port.onMessage.addListener((message: WorkerToPanel) => {
  if (message.type === "backend") status = message;
  else acks = [message, ...acks].slice(0, 20);
  for (const notify of subscribers) notify();
});

function useWorkerState() {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const notify = () => setVersion((n) => n + 1);
    subscribers.add(notify);
    // A message may have landed between the first render and this subscription.
    notify();
    return () => {
      subscribers.delete(notify);
    };
  }, []);
  return { status, acks };
}

function Panel() {
  const { status, acks } = useWorkerState();
  const requestSnapshot = () => port.postMessage({ type: "snapshot_request" } satisfies PanelToWorker);

  return (
    <main>
      <h1>VAMOS</h1>
      <section>
        <h2>Backend</h2>
        <p class={`state ${status.state}`}>
          {status.state}
          {status.version ? ` · v${status.version}` : ""}
          {status.detail ? ` · ${status.detail}` : ""}
        </p>
      </section>
      <section>
        <h2>Recording</h2>
        <button type="button" onClick={requestSnapshot}>
          Save DOM snapshot
        </button>
        <ul>
          {acks.map((ack, index) => (
            <li key={index}>
              {ack.what}: {ack.detail}
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

const app = document.getElementById("app");
if (app) render(<Panel />, app);
