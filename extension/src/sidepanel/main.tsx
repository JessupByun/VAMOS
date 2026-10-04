import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import {
  type BackendStatus,
  type ClaudeAdvice,
  type Costs,
  type HandInfo,
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

const noteTimers = new Map<string, ReturnType<typeof setTimeout>>();
// What the user typed, kept here too so a re-render from a backend update never drops it,
// and so the notes can be sent again to a backend that restarted.
const drafts = new Map<string, string>();

let port: chrome.runtime.Port | null = null;
let status: BackendStatus = { type: "backend", state: "connecting" };
let acks: HostAck[] = [];
let settings: Settings | null = null;
let costs: Costs | null = null;

// Per table: the players, the hand on the table (from the backend's hand updates) and
// Claude's answers in it, in order, so earlier streets stay readable. A decision node is
// "<hand>:<street>:<actions so far>"; a new hand clears the list the moment it is dealt.
interface Decision {
  node: string;
  street: string;
  advice: ClaudeAdvice;
}
interface Hand {
  gameId: string;
  key: string;
  info: HandInfo | null;
  decisions: Decision[];
  live: string | null;
}
const hands = new Map<string, Hand>();
const playersByGame = new Map<string, Players>();
// The table last heard from; a table with a live decision is shown before it.
let lastGame: string | null = null;
let retryDelay = RECONNECT_MIN_MS;
const subscribers = new Set<() => void>();

const notifyAll = () => {
  for (const notify of subscribers) notify();
};

function shownGame(): string | null {
  for (const [gameId, hand] of hands) if (hand.live) return gameId;
  return lastGame;
}

function onHand(info: HandInfo) {
  const hand = hands.get(info.gameId);
  hands.set(
    info.gameId,
    hand && hand.key === info.key
      ? { ...hand, info }
      : { gameId: info.gameId, key: info.key, info, decisions: [], live: null },
  );
}

function onFacts(gameId: string, node: string | null, advice: unknown) {
  const hand = hands.get(gameId);
  if (node === null || !isClaude(advice as ClaudeAdvice | null)) {
    // The decision passed (or a preflop chart is up): nothing on this table is live.
    if (hand) hands.set(gameId, { ...hand, live: null });
    return;
  }
  const [key = "", street = ""] = node.split(":");
  const entry = { node, street: STREETS[Number(street)] ?? "", advice: advice as ClaudeAdvice };
  const current: Hand = hand && hand.key === key ? hand : { gameId, key, info: null, decisions: [], live: null };
  const decisions = [...current.decisions.filter((d) => d.node !== node), entry].sort((a, b) =>
    a.node.localeCompare(b.node, "en", { numeric: true }),
  );
  hands.set(gameId, { ...current, decisions, live: node });
}

// Notes live in the backend's memory, so a new backend has none: send them again.
function resendNotes() {
  for (const [playerId, text] of drafts) {
    if (text.trim()) send({ type: "notes", playerId, text });
  }
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
    if (message.type === "backend") {
      const becameReady = message.state === "ready" && status.state !== "ready";
      status = message;
      if (message.state === "down") {
        // No table is open: nothing shown about one is current any more.
        hands.clear();
        playersByGame.clear();
      }
      if (becameReady) resendNotes();
    } else if (message.type === "players") {
      playersByGame.set(message.gameId, message);
      lastGame = message.gameId;
    }
    else if (message.type === "settings") settings = message;
    else if (message.type === "costs") costs = message;
    else if (message.type === "hand") {
      onHand(message);
      lastGame = message.gameId;
    } else if (message.type === "facts") {
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
  const game = shownGame();
  const hand = game ? (hands.get(game) ?? null) : null;
  const players = game ? (playersByGame.get(game) ?? null) : null;
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

const SUITS: Record<string, string> = { s: "\u2660", h: "\u2665", d: "\u2666", c: "\u2663" };

/** A playing card drawn small: "Ah" as A with a red heart. */
function Card({ card }: { card: string }) {
  const rank = card[0] === "T" ? "10" : (card[0] ?? "");
  const suit = card[1] ?? "";
  return (
    <span class={`pcard ${suit === "h" || suit === "d" ? "red" : "black"}`}>
      {rank}
      <span class="suit">{SUITS[suit]}</span>
    </span>
  );
}

function HandHeader({ info }: { info: HandInfo }) {
  const result =
    info.resultBb === null
      ? info.folded
        ? "folded"
        : null
      : info.resultBb > 0
        ? `won ${info.resultBb}bb`
        : info.resultBb < 0
          ? `lost ${-info.resultBb}bb`
          : "even";
  return (
    <div class="hand-head">
      <div class="cards">
        <span class="mine">
          {info.heroCards.map((c) => (
            <Card key={c} card={c} />
          ))}
        </span>
        {info.board.length > 0 && (
          <span class="board">
            {info.board.map((c) => (
              <Card key={c} card={c} />
            ))}
          </span>
        )}
        {info.secondBoard.length > 0 && (
          <span class="board second" title="second run">
            {info.secondBoard.map((c) => (
              <Card key={`2-${c}`} card={c} />
            ))}
          </span>
        )}
      </div>
      <p class="hand-line">
        <span class="strong">{info.madeHand ?? "no cards yet"}</span>
        {info.position && <span class="muted"> · {info.position}</span>}
        <span class="muted"> · hand #{info.number}</span>
        {result && <span class={`result ${info.resultBb && info.resultBb > 0 ? "won" : ""}`}> · {result}</span>}
      </p>
    </div>
  );
}

function CurrentHand({ hand }: { hand: Hand }) {
  return (
    <section class="card advice">
      <h2>Current hand</h2>
      {hand.info && <HandHeader info={hand.info} />}
      {hand.decisions.length === 0 ? (
        <p class="muted small">No AI decisions yet this hand; they come after the flop.</p>
      ) : (
        hand.decisions.map((d) => (
          <DecisionView key={d.node} decision={d} live={d.node === hand.live} gameId={hand.gameId} />
        ))
      )}
    </section>
  );
}

function Dial({ settings }: { settings: Settings }) {
  return (
    <section class="card">
      <h2>Model (currently used only postflop)</h2>
      <div class="segmented" role="radiogroup" aria-label="Model">
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

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";

function pct(count: number, of: number): string {
  return `${Math.round((100 * count) / of)}%`;
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


function PlayerNotes({ player }: { player: PlayerInfo }) {
  const [, rerender] = useState(0);
  const value = drafts.get(player.id) ?? player.notes;
  const change = (event: Event) => {
    const text = (event.currentTarget as HTMLInputElement).value;
    drafts.set(player.id, text);
    rerender((n) => n + 1);
    clearTimeout(noteTimers.get(player.id));
    noteTimers.set(
      player.id,
      setTimeout(() => send({ type: "notes", playerId: player.id, text }), 500),
    );
  };
  const flush = () => {
    clearTimeout(noteTimers.get(player.id));
    const text = drafts.get(player.id);
    if (text !== undefined) send({ type: "notes", playerId: player.id, text });
  };
  return (
    <input
      class="notes"
      type="text"
      value={value}
      onInput={change}
      onBlur={flush}
      placeholder="Notes for tonight, e.g. very bluffy"
      aria-label={`Notes on ${player.name}`}
    />
  );
}

function PlayerRow({ player, profiles }: { player: PlayerInfo; profiles: Players["profiles"] }) {
  const profile = player.profile;
  const t = player.tonight;
  const tonight =
    t && t.hands > 0
      ? `VPIP ${pct(t.vpip, t.hands)} · PFR ${pct(t.pfr, t.hands)} (${t.hands} hand${t.hands === 1 ? "" : "s"})`
      : "no hands yet";
  const lifetime = profile
    ? [
        profile.stats.vpip && `VPIP ${profile.stats.vpip}%`,
        profile.stats.pfr && `PFR ${profile.stats.pfr}%`,
        profile.meta.hands && `${profile.meta.hands} hands`,
        profile.meta.net && `net ${profile.meta.net}`,
      ].filter(Boolean)
    : [];
  return (
    <li class={player.hero ? "player me" : "player"}>
      <div class="row1">
        <span class="who">{player.hero ? `${player.name} (You)` : (profile?.name ?? player.name)}</span>
        {profile && profile.name !== player.name && <span class="muted nick">{player.name}</span>}
        {profile?.tag && <span class="tag">{profile.tag}</span>}
        {!player.hero && player.source !== "player_id" && (
          <span class="picker">
            <IdentityPicker player={player} profiles={profiles} />
          </span>
        )}
      </div>
      {profile?.summary && <p class="summary">{profile.summary}</p>}
      <p class="numbers">
        <span class="label">Tonight</span> {tonight}
        {lifetime.length > 0 && (
          <>
            <br />
            <span class="label">Profile</span> {lifetime.join(" · ")}
          </>
        )}
        {!player.hero && !profile && (
          <>
            <br />
            <span class="muted">{player.status === "unknown" ? "Not identified yet" : "No profile"}</span>
          </>
        )}
      </p>
      {!player.hero && <PlayerNotes player={player} />}
      {profile && (
        <details>
          <summary>Profile file</summary>
          <pre class="body">{profile.body}</pre>
        </details>
      )}
    </li>
  );
}

function PlayersSection({ players }: { players: Players }) {
  return (
    <section class="card">
      <h2>Players</h2>
      {players.players.length > 0 ? (
        <ul class="players">
          {players.players.map((p) => (
            <PlayerRow key={p.id} player={p} profiles={players.profiles} />
          ))}
        </ul>
      ) : (
        <p class="muted small">Nobody seated yet.</p>
      )}
      <p class="muted small">
        {players.profiles.length} profiles loaded from profiles/ · tonight's numbers and notes are never saved
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
  const state = status.state === "ready" ? "active" : status.state;

  return (
    <main>
      <header class="top">
        <span class="wordmark">{BRAND}</span>
        <span class={`state ${status.state}`}>
          {state}
          {status.version ? ` · v${status.version}` : ""}
          {status.recording ? " · recording log pages" : ""}
          {status.detail ? ` · ${status.detail}` : ""}
        </span>
      </header>
      {settings && <Dial settings={settings} />}
      {costs && <Spend costs={costs} />}
      {hand && <CurrentHand hand={hand} />}
      {players && <PlayersSection players={players} />}
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
