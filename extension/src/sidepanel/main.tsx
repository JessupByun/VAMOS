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
  type Settings,
  type WorkerToPanel,
} from "../shared/protocol";
import "./panel.css";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;
const BRAND = "VAMOS!";
const STREETS = ["Preflop", "Flop", "Turn", "River"];

let port: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "connecting" };
let acks: HostAck[] = [];
let players: Players | null = null;
let settings: Settings | null = null;
let costs: Costs | null = null;

// Claude's answers for the current hand, in order, so earlier streets stay readable. A node
// is "<hand>:<street>:<actions so far>"; a new hand starts a new list.
interface Decision {
  node: string;
  street: string;
  advice: ClaudeAdvice;
}
interface Hand {
  gameId: string;
  key: string;
  decisions: Decision[];
  live: string | null;
}
let hand: Hand | null = null;
let retryDelay = RECONNECT_MIN_MS;
const subscribers = new Set<() => void>();

const notifyAll = () => {
  for (const notify of subscribers) notify();
};

function onFacts(gameId: string, node: string | null, advice: unknown) {
  if (node === null || !isClaude(advice as ClaudeAdvice | null)) {
    // The decision passed (or a preflop chart is up): nothing on the table is live.
    if (hand) hand = { ...hand, live: null };
    return;
  }
  const [key = "", street = ""] = node.split(":");
  const entry = { node, street: STREETS[Number(street)] ?? "", advice: advice as ClaudeAdvice };
  const same = hand && hand.gameId === gameId && hand.key === key;
  const kept = same && hand ? hand.decisions.filter((d) => d.node !== node) : [];
  const decisions = [...kept, entry].sort((a, b) => a.node.localeCompare(b.node, "en", { numeric: true }));
  hand = { gameId, key, decisions, live: node };
}

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
      onFacts(message.gameId, message.facts?.node ?? null, message.facts?.advice ?? null);
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
  return { status, acks, players, settings, costs, hand };
}

const send = (message: PanelToWorker) => port?.postMessage(message);

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

function Options({ advice }: { advice: ClaudeAdvice }) {
  if (!advice.mix) return null;
  return (
    <div class="chips">
      {advice.mix.map((part, i) => (
        <span key={part} class={i === advice.pick ? "chip picked" : "chip"}>
          {part}
        </span>
      ))}
      {advice.deviation && <span class="chip deviation">deviation: drew the {advice.pickPct}% option</span>}
    </div>
  );
}

function DecisionView(props: { decision: Decision; live: boolean; gameId: string }) {
  const { decision, live, gameId } = props;
  const { advice } = decision;
  const answered = advice.action !== null;
  const upgrading = answered && advice.asked !== advice.model;
  return (
    <div class={live ? "decision live" : "decision"}>
      <div class="head">
        <span class="street">{decision.street}</span>
        <span class="source">
          {BRAND} AI ({answered ? advice.model : advice.asked})
        </span>
        {live && <span class="now">now</span>}
      </div>
      {answered ? (
        <>
          <Pick text={advice.action ?? ""} />
          <Options advice={advice} />
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
    </div>
  );
}

function Decisions({ hand }: { hand: Hand }) {
  const live = hand.live !== null;
  return (
    <section class={live ? "card advice" : "card advice past"}>
      <h2>This hand's decisions</h2>
      {hand.decisions.map((d) => (
        <DecisionView key={d.node} decision={d} live={d.node === hand.live} gameId={hand.gameId} />
      ))}
    </section>
  );
}

function Dial({ settings }: { settings: Settings }) {
  return (
    <section class="card">
      <h2>Model after the flop</h2>
      <div class="segmented" role="radiogroup" aria-label="Model after the flop">
        {settings.presets.map((preset) => (
          <label key={preset.name} class={settings.preset === preset.name ? "on" : ""}>
            <input
              type="radio"
              name="preset"
              checked={settings.preset === preset.name}
              onChange={() => send({ type: "settings", preset: preset.name })}
            />
            <span class="model">{preset.label}</span>
            <span class="detail">
              {preset.effort ? `${preset.effort} thinking · ` : ""}
              {preset.seconds} s
            </span>
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
      <h2>API costs</h2>
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
      <p class="muted small">Sent with every AI call tonight; never saved.</p>
    </section>
  );
}

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";
const META_LABELS: Record<string, string> = {
  hands: "hands in profile",
  sessions: "sessions",
  net: "net",
  last_updated: "updated",
};

function pct(count: number, of: number): string {
  return `${Math.round((100 * count) / of)}%`;
}

function TonightStats({ player }: { player: PlayerInfo }) {
  const t = player.tonight;
  if (!t || t.hands === 0) return <span class="muted">no hands yet tonight</span>;
  return (
    <span class="tonight">
      VPIP {pct(t.vpip, t.hands)} <span class="muted">({t.vpip}/{t.hands})</span> · PFR {pct(t.pfr, t.hands)}{" "}
      <span class="muted">({t.pfr}/{t.hands})</span>
    </span>
  );
}

function IdentityPicker({ player, profiles }: { player: PlayerInfo; profiles: Players["profiles"] }) {
  const change = (event: Event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    if (!value) return;
    if (value === ASK_AGAIN) {
      send({ type: "forget_identity", playerId: player.id });
    } else {
      send({ type: "assign_identity", playerId: player.id, profile: value === NO_PROFILE ? null : value });
    }
  };
  const current = player.profile?.key ?? (player.status === "none" ? NO_PROFILE : "");
  return (
    <select aria-label={`Change who ${player.name} is`} value={current} onChange={change}>
      {player.status === "unknown" && <option value="">who is this?</option>}
      {profiles.map((p) => (
        <option key={p.key} value={p.key}>
          {p.name}
        </option>
      ))}
      <option value={NO_PROFILE}>No profile</option>
      {player.status !== "unknown" && <option value={ASK_AGAIN}>Forget answer</option>}
    </select>
  );
}

function TableRow({ player, profiles }: { player: PlayerInfo; profiles: Players["profiles"] }) {
  const profile = player.profile;
  const meta = profile ? Object.entries(profile.meta) : [];
  return (
    <li>
      <details>
        <summary>
          <span class="who">{profile?.name ?? player.name}</span>
          {profile && profile.name !== player.name && <span class="muted"> ({player.name})</span>}
          <span class="what">
            <TonightStats player={player} />
          </span>
        </summary>
        <div class="profile">
          {profile ? (
            <>
              <p>
                <span class="tag">{profile.tag ?? "no tag"}</span>
                {meta.map(([key, value]) => (
                  <span key={key} class="meta">
                    {META_LABELS[key] ?? key}: {String(value)}
                  </span>
                ))}
              </p>
              <pre class="body">{profile.body}</pre>
            </>
          ) : (
            <p class="muted">
              {player.status === "none" ? "No profile: Claude plays them on judgment alone." : "Not identified yet."}
            </p>
          )}
          {/* A profile's player_id is changed in that file, not here. */}
          {player.source !== "player_id" && <IdentityPicker player={player} profiles={profiles} />}
        </div>
      </details>
    </li>
  );
}

function Table({ players }: { players: Players }) {
  return (
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
      <p class="muted small">
        {players.profiles.length} profiles loaded from profiles/ · tonight's numbers are never saved
      </p>
      {players.problems.length > 0 && (
        <ul class="problems">
          {players.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Panel() {
  const { status, acks, players, settings, costs, hand } = useWorkerState();
  const requestSnapshot = () => send({ type: "snapshot_request" });

  return (
    <main>
      <header class="top">
        <span class="wordmark">{BRAND}</span>
        <span class={`state ${status.state}`}>
          {status.state}
          {status.version ? ` · v${status.version}` : ""}
          {status.recording ? " · recording log pages" : ""}
          {status.detail ? ` · ${status.detail}` : ""}
        </span>
      </header>
      {players && <Table players={players} />}
      {hand && <Decisions hand={hand} />}
      <Notes />
      {settings && <Dial settings={settings} />}
      {costs && <Spend costs={costs} />}
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
