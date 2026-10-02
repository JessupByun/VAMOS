import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import type {
  BackendStatus,
  HostAck,
  PanelToWorker,
  PlayerInfo,
  Players,
  WorkerToPanel,
} from "../shared/protocol";
import "./panel.css";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;

let port: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "connecting" };
let acks: HostAck[] = [];
let players: Players | null = null;
let retryDelay = RECONNECT_MIN_MS;
const subscribers = new Set<() => void>();

const notifyAll = () => {
  for (const notify of subscribers) notify();
};

// The worker replies the moment the port connects, so the listener is attached here,
// synchronously, not later in a component effect, or the first status is dropped. The
// worker is stopped after 30s idle when no backend runs, which closes this port; the
// panel reconnects so it never freezes on a stale state.
function connect() {
  const next = chrome.runtime.connect({ name: "panel" });
  port = next;
  next.onMessage.addListener((message: WorkerToPanel) => {
    retryDelay = RECONNECT_MIN_MS;
    if (message.type === "backend") status = message;
    else if (message.type === "players") players = message;
    else acks = [message, ...acks].slice(0, 20);
    notifyAll();
  });
  next.onDisconnect.addListener(() => {
    if (port === next) port = null;
    status = { type: "backend", state: "connecting" };
    notifyAll();
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
  });
}

connect();

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
  return { status, acks, players };
}

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";

function TableRow({ player, profiles }: { player: PlayerInfo; profiles: Players["profiles"] }) {
  const label = player.profile
    ? `${player.profile.name} (${player.profile.tag ?? "no tag"})`
    : player.status === "none"
      ? "no profile"
      : "unknown, answer on the table";
  // Answers recognized through a profile's player_id are changed in that file, not here.
  if (player.source !== "answer") {
    return (
      <li>
        {player.name}: {label}
      </li>
    );
  }
  const change = (event: Event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    if (value === ASK_AGAIN) {
      port?.postMessage({ type: "forget_identity", playerId: player.id } satisfies PanelToWorker);
    } else {
      const profile = value === NO_PROFILE ? null : value;
      port?.postMessage({ type: "assign_identity", playerId: player.id, profile } satisfies PanelToWorker);
    }
  };
  return (
    <li>
      {player.name}:{" "}
      <select aria-label={`Change who ${player.name} is`} value={player.profile?.key ?? NO_PROFILE} onChange={change}>
        {profiles.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
        <option value={NO_PROFILE}>No profile</option>
        <option value={ASK_AGAIN}>Ask again on the table</option>
      </select>
    </li>
  );
}

function Panel() {
  const { status, acks, players } = useWorkerState();
  const requestSnapshot = () =>
    port?.postMessage({ type: "snapshot_request" } satisfies PanelToWorker);

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
      {players && (
        <section>
          <h2>Profiles</h2>
          <p>{players.profiles.length} loaded from profiles/</p>
          {players.problems.length > 0 && (
            <ul class="problems">
              {players.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          <h2>Table</h2>
          <ul>
            {players.players.map((p) => (
              <TableRow key={p.id} player={p} profiles={players.profiles} />
            ))}
          </ul>
        </section>
      )}
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
