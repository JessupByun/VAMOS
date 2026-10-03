import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import {
  type BackendStatus,
  type ClaudeAdvice,
  type Costs,
  type HostAck,
  isClaude,
  type PanelToWorker,
  type PlayerInfo,
  type Players,
  type Preset,
  type Settings,
  type WorkerToPanel,
} from "../shared/protocol";
import "./panel.css";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;

let port: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "connecting" };
let acks: HostAck[] = [];
let players: Players | null = null;
let settings: Settings | null = null;
let costs: Costs | null = null;
// Claude's latest answer, and whether its decision is still on the table.
interface LatestClaude {
  gameId: string;
  advice: ClaudeAdvice;
  live: boolean;
}
let claude: LatestClaude | null = null;
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
    else if (message.type === "settings") settings = message;
    else if (message.type === "costs") costs = message;
    else if (message.type === "facts") {
      const advice = message.facts?.advice ?? null;
      if (isClaude(advice)) claude = { gameId: message.gameId, advice, live: true };
      else if (claude) claude = { ...claude, live: false };
    } else acks = [message, ...acks].slice(0, 20);
    notifyAll();
  });
  next.onDisconnect.addListener(() => {
    // Reading the reason marks it handled; Chrome reports an unread one as an error.
    void chrome.runtime.lastError;
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
  return { status, acks, players, settings, costs, claude };
}

const send = (message: PanelToWorker) => port?.postMessage(message);

const PRESETS: Record<Preset, { model: string; detail: string }> = {
  balanced: { model: "Sonnet 5.5", detail: "about 3 seconds" },
  deep: { model: "Opus 5.5", detail: "about 10 seconds" },
};

/** "BET 0.20 (1bb)": the move large, its size in big blinds a step quieter. */
function Pick({ text }: { text: string }) {
  const split = text.match(/^(.*?)( \([^)]*\))$/);
  return (
    <p class="pick">
      {split ? split[1] : text}
      {split && <span class="bb">{split[2]}</span>}
    </p>
  );
}

function Advice({ claude }: { claude: LatestClaude }) {
  const { advice, live, gameId } = claude;
  const answered = advice.action !== null;
  const upgrading = answered && advice.asked !== advice.model;
  return (
    <section class={live ? "card advice" : "card advice past"}>
      <h2>{live ? "This decision" : "Your last decision"}</h2>
      <p class="source">VAMOS AI ({answered ? advice.model : advice.asked})</p>
      {answered ? (
        <>
          <Pick text={advice.action ?? ""} />
          {advice.mix && (
            <div class="chips">
              {advice.mix.map((part) => (
                <span key={part} class="chip">
                  {part}
                </span>
              ))}
            </div>
          )}
          {advice.why && <p class="why">{advice.why}</p>}
          {advice.plan && (
            <p class="plan">
              <span class="label">Next:</span> {advice.plan}
            </p>
          )}
        </>
      ) : (
        <p class="muted waiting">
          {advice.state !== "thinking"
            ? `No answer: ${advice.reason}`
            : live
              ? "Thinking..."
              : "No answer before you acted."}
        </p>
      )}
      {live && upgrading && advice.state === "thinking" && <p class="muted">Asking {advice.asked}...</p>}
      {upgrading && advice.state === "failed" && (
        <p class="muted">
          {advice.asked}: {advice.reason}
        </p>
      )}
      {live && !advice.model.includes("Opus") && !advice.asked.includes("Opus") && (
        <button type="button" class="secondary" onClick={() => send({ type: "ask", gameId, preset: "deep" })}>
          Ask Opus
        </button>
      )}
    </section>
  );
}

function Dial({ settings }: { settings: Settings }) {
  return (
    <section class="card">
      <h2>Model after the flop</h2>
      <div class="segmented" role="radiogroup" aria-label="Model after the flop">
        {settings.presets.map((preset) => (
          <label key={preset} class={settings.preset === preset ? "on" : ""}>
            <input
              type="radio"
              name="preset"
              checked={settings.preset === preset}
              onChange={() => send({ type: "settings", preset })}
            />
            <span class="model">{PRESETS[preset]?.model ?? preset}</span>
            <span class="detail">{PRESETS[preset]?.detail}</span>
          </label>
        ))}
      </div>
      <p class="muted small">A change applies from the next hand.</p>
    </section>
  );
}

const dollars = (amount: number) => `$${amount.toFixed(2)}`;

function Spend({ costs }: { costs: Costs }) {
  const stats: [string, string, number][] = [
    ["tonight", "Tonight", costs.session],
    ["month", "This month", costs.month],
    ["all", "All time", costs.allTime],
  ];
  return (
    <section class="card">
      <h2>Claude costs</h2>
      <div class="stats">
        {stats.map(([key, label, amount]) => (
          <div key={key} class={`stat ${key}`}>
            <span class="value">{dollars(amount)}</span>
            <span class="label">{label}</span>
          </div>
        ))}
      </div>
      {costs.overCap && (
        <p class="over small">Past tonight's {dollars(costs.softCap)} soft cap; advice keeps coming.</p>
      )}
    </section>
  );
}

let notesTimer: ReturnType<typeof setTimeout> | undefined;

function Notes() {
  const [text, setText] = useState("");
  const change = (event: Event) => {
    const value = (event.currentTarget as HTMLTextAreaElement).value;
    setText(value);
    clearTimeout(notesTimer);
    notesTimer = setTimeout(() => send({ type: "notes", text: value }), 600);
  };
  return (
    <section class="card">
      <h2>Notes for tonight</h2>
      <textarea rows={3} value={text} onInput={change} placeholder="e.g. alex is tilted after losing a big pot" />
      <p class="muted small">Sent with every Claude call tonight; never saved.</p>
    </section>
  );
}

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";

function TableRow({ player, profiles }: { player: PlayerInfo; profiles: Players["profiles"] }) {
  const detail = player.profile
    ? (player.profile.tag ?? "no tag")
    : player.status === "none"
      ? "no profile"
      : "unknown, answer on the table";
  // Answers recognized through a profile's player_id are changed in that file, not here.
  if (player.source !== "answer") {
    return (
      <li>
        <span class="who">{player.name}</span>
        <span class="what">
          {player.profile ? `${player.profile.name} · ${detail}` : detail}
        </span>
      </li>
    );
  }
  const change = (event: Event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    if (value === ASK_AGAIN) {
      send({ type: "forget_identity", playerId: player.id });
    } else {
      send({ type: "assign_identity", playerId: player.id, profile: value === NO_PROFILE ? null : value });
    }
  };
  return (
    <li>
      <span class="who">{player.name}</span>
      <select aria-label={`Change who ${player.name} is`} value={player.profile?.key ?? NO_PROFILE} onChange={change}>
        {profiles.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
        <option value={NO_PROFILE}>No profile</option>
        <option value={ASK_AGAIN}>Forget answer</option>
      </select>
    </li>
  );
}

function Panel() {
  const { status, acks, players, settings, costs, claude } = useWorkerState();
  const requestSnapshot = () => send({ type: "snapshot_request" });

  return (
    <main>
      <header class="top">
        <span class="wordmark">VAMOS</span>
        <span class={`state ${status.state}`}>
          {status.state}
          {status.version ? ` · v${status.version}` : ""}
          {status.recording ? " · recording log pages" : ""}
          {status.detail ? ` · ${status.detail}` : ""}
        </span>
      </header>
      {claude && <Advice claude={claude} />}
      {settings && <Dial settings={settings} />}
      <Notes />
      {costs && <Spend costs={costs} />}
      {players && (
        <section class="card">
          <h2>Table</h2>
          {players.players.length > 0 ? (
            <ul class="table">
              {players.players.map((p) => (
                <TableRow key={p.id} player={p} profiles={players.profiles} />
              ))}
            </ul>
          ) : (
            <p class="muted small">Nobody seated yet.</p>
          )}
          <p class="muted small">{players.profiles.length} profiles loaded from profiles/</p>
          {players.problems.length > 0 && (
            <ul class="problems">
              {players.problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
        </section>
      )}
      {status.recording && (
        <section class="card">
          <h2>Recording</h2>
          <button type="button" class="secondary" onClick={requestSnapshot}>
            Save DOM snapshot
          </button>
          <ul class="acks">
            {acks.map((ack, index) => (
              <li key={index}>
                {ack.what}: {ack.detail}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

const app = document.getElementById("app");
if (app) render(<Panel />, app);
