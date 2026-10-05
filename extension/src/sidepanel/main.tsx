import { render } from "preact";
import { useEffect, useState } from "preact/hooks";

import { actionClass } from "../shared/actions";
import {
  type BackendStatus,
  type ClaudeAdvice,
  type ClaudeAnswer,
  type Costs,
  type HandInfo,
  type HostAck,
  isClaude,
  type PanelToWorker,
  type PlayerInfo,
  type Players,
  type ProfileView,
  type Settings,
  type TonightRead,
  type WorkerToPanel,
} from "../shared/protocol";
import "./panel.css";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;
const BRAND = "VAMOS!";
const STREETS = ["Preflop", "Flop", "Turn", "River"];

const noteTimers = new Map<string, ReturnType<typeof setTimeout>>();
// What the user typed, kept here too so a re-render from a backend update never drops it,
// and so the notes can be sent again to a backend that restarted; with the game it was on.
const drafts = new Map<string, string>();
const draftGames = new Map<string, string>();

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
// Where each player's draft was last saved this session.
const savedDrafts = new Map<string, string>();
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

// The backend saves notes with each game; typed ones are sent again in case a restart
// landed between typing and saving.
function resendNotes() {
  for (const [playerId, text] of drafts) {
    if (text.trim()) send({ type: "notes", playerId, text, gameId: draftGames.get(playerId) });
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
    else if (message.type === "draft_saved") savedDrafts.set(message.playerId, message.path);
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

/** "BET 0.20 (1bb)": the move large in its action's color, its size in big blinds quieter. */
function Pick({ text }: { text: string }) {
  const split = text.match(/^(.*?)( \([^)]*\))$/);
  return (
    <p class={`pick ${actionClass(text)}`}>
      {split ? split[1] : text}
      {split && <span class="bb">{split[2]}</span>}
    </p>
  );
}

function Options({ answer }: { answer: ClaudeAnswer }) {
  if (!answer.mix) return null;
  return (
    <div class="chips">
      {answer.mix.map((part, i) => (
        <span key={part} class={`chip ${actionClass(part)}${i === answer.pick ? " picked" : ""}`}>
          {part}
        </span>
      ))}
      {answer.deviation && <span class="chip deviation">deviation: drew the {answer.pickPct}% option</span>}
    </div>
  );
}

function waiting(answer: ClaudeAnswer, live: boolean, later: boolean): string {
  if (answer.state === "failed") return `No answer: ${answer.reason}`;
  if (answer.state === "cancelled" && later) return "Stopped when you asked deeper.";
  return live && answer.state === "thinking" ? "Thinking..." : "No answer before you acted.";
}

/** One call's answer; a deeper one is added under the first, never in its place. */
function AnswerView(props: { answer: ClaudeAnswer; deeper: boolean; live: boolean; later: boolean }) {
  const { answer, deeper, live, later } = props;
  return (
    <div class={`answer ${answer.preset}${deeper ? " deeper" : ""}`}>
      {deeper && <p class="asked">Asked deeper</p>}
      <div class="source">
        <span class="dot" />
        {BRAND} AI ({answer.model})
      </div>
      {answer.action ? (
        <>
          <Pick text={answer.action} />
          <Options answer={answer} />
          {answer.why && <p class="why">{answer.why}</p>}
          {answer.plan && (
            <p class="plan">
              <span class="label">Next:</span> {answer.plan}
            </p>
          )}
        </>
      ) : (
        <p class="muted waiting">{waiting(answer, live, later)}</p>
      )}
    </div>
  );
}

function answersOf(advice: ClaudeAdvice): ClaudeAnswer[] {
  if (advice.answers?.length) return advice.answers;
  const { model, preset, state, reason, action, mix, pick, pickPct, deviation, why, plan } = advice;
  return [{ model, preset, state, reason, action, mix, pick, pickPct, deviation, why, plan }];
}

function DecisionView(props: { decision: Decision; live: boolean; gameId: string }) {
  const { decision, live, gameId } = props;
  const answers = answersOf(decision.advice);
  return (
    <div class={live ? "decision live" : "decision"}>
      <div class="head">
        <span class="street">{decision.street}</span>
        {live && <span class="now">now</span>}
      </div>
      {answers.map((answer, i) => (
        <AnswerView key={i} answer={answer} deeper={i > 0} live={live} later={i < answers.length - 1} />
      ))}
      {live && !answers.some((a) => a.preset === "deep") && (
        <button
          type="button"
          class="secondary"
          onClick={() => send({ type: "ask", gameId, preset: "deep" })}
        >
          Ask deeper
        </button>
      )}
    </div>
  );
}

const SUITS: Record<string, string> = { s: "♠", h: "♥", d: "♦", c: "♣" };

const SUIT_NAMES: Record<string, string> = { s: "spades", h: "hearts", d: "diamonds", c: "clubs" };
const RANK_NAMES: Record<string, string> = { A: "ace", K: "king", Q: "queen", J: "jack", T: "10" };

/** A playing card drawn small, every card the same size: "Ah" as A over a red heart. */
function Card({ card }: { card: string }) {
  const rank = card[0] === "T" ? "10" : (card[0] ?? "");
  const suit = card[1] ?? "";
  const name = `${RANK_NAMES[card[0] ?? ""] ?? rank} of ${SUIT_NAMES[suit] ?? suit}`;
  return (
    <span class={`pcard ${suit === "h" || suit === "d" ? "red" : "black"}`} role="img" aria-label={name}>
      <span class="rank" aria-hidden="true">
        {rank}
      </span>
      <span class="suit" aria-hidden="true">
        {SUITS[suit]}
      </span>
    </span>
  );
}

/** `count` card spots: the cards dealt so far face up, the rest face down until dealt. */
function Cards({ cards, count }: { cards: string[]; count: number }) {
  return (
    <>
      {Array.from({ length: Math.max(count, cards.length) }, (_, i) => {
        const card = cards[i];
        return card ? (
          <Card key={card} card={card} />
        ) : (
          <span key={`back-${i}`} class="pcard back" role="img" aria-label="not dealt yet" />
        );
      })}
    </>
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
  const tone = info.resultBb === null ? "" : info.resultBb > 0 ? "won" : info.resultBb < 0 ? "lost" : "";
  return (
    <div class="hand-head">
      <div class="cards">
        <span class="mine">
          <Cards cards={info.heroCards} count={2} />
        </span>
        <span class="board">
          <Cards cards={info.board} count={5} />
        </span>
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
        {result && <span class={`result ${tone}`}> · {result}</span>}
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
        <p class="muted small">No AI decisions yet this hand.</p>
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
      <h2>Model</h2>
      <div class="segmented" role="radiogroup" aria-label="Model">
        {settings.presets.map((preset) => (
          <label key={preset.name} class={`${preset.name}${settings.preset === preset.name ? " on" : ""}`}>
            <input
              type="radio"
              name="preset"
              checked={settings.preset === preset.name}
              onChange={() => send({ type: "settings", preset: preset.name })}
            />
            <span class="model">{preset.label}</span>
            <span class="detail">usually {preset.seconds} s</span>
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
    ["tonight", "This session", costs.session],
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
        <p class="over small">Past this session's {dollars(costs.softCap)} soft cap; advice keeps coming.</p>
      )}
    </section>
  );
}

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";
const MATCH = "Match existing player profile";

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
    <select aria-label={`Match a profile for ${player.name}`} value={current} onChange={change}>
      {player.status === "unknown" && <option value="">{MATCH}</option>}
      {profiles.map((p) => (
        <option key={p.key} value={p.key}>
          {p.name}
        </option>
      ))}
      <option value={NO_PROFILE}>No profile</option>
      {player.status !== "unknown" && <option value={ASK_AGAIN}>Unmatch (ask again)</option>}
    </select>
  );
}

function PlayerNotes({ player, gameId }: { player: PlayerInfo; gameId: string }) {
  const [, rerender] = useState(0);
  const value = drafts.get(player.id) ?? player.notes;
  const change = (event: Event) => {
    const text = (event.currentTarget as HTMLInputElement).value;
    drafts.set(player.id, text);
    draftGames.set(player.id, gameId);
    rerender((n) => n + 1);
    clearTimeout(noteTimers.get(player.id));
    noteTimers.set(
      player.id,
      setTimeout(() => send({ type: "notes", playerId: player.id, text, gameId }), 500),
    );
  };
  const flush = () => {
    clearTimeout(noteTimers.get(player.id));
    const text = drafts.get(player.id);
    if (text !== undefined) send({ type: "notes", playerId: player.id, text, gameId });
  };
  return (
    <input
      class="notes"
      type="text"
      value={value}
      onInput={change}
      onBlur={flush}
      placeholder="Notes for this session, e.g. very bluffy"
      aria-label={`Notes on ${player.name}`}
    />
  );
}

/** One number in a player's grid: a small label, the value, and its count when it has one. */
function Stat(props: { label: string; value: string; count?: string | null; tone?: string }) {
  return (
    <div class="st">
      <span class="k">{props.label}</span>
      <span class={`v ${props.tone ?? ""}`}>{props.value}</span>
      {props.count && <span class="n">{props.count}</span>}
    </div>
  );
}

/** A rate with its count, or a dash when there was no chance yet. */
function rate(count: number, of: number): { value: string; count: string | null } {
  return of > 0 ? { value: pct(count, of), count: `${count}/${of}` } : { value: "-", count: null };
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function TonightStats({ t }: { t: TonightRead | null }) {
  const hands = t?.hands ?? 0;
  return (
    <div class="group">
      <div class="group-head">
        <span class="label">Current session</span>
        <span class="muted">{hands > 0 ? plural(hands, "hand") : "no hands yet"}</span>
      </div>
      {t && hands > 0 && (
        <div class="statgrid">
          <Stat label="VPIP" {...rate(t.vpip, t.hands)} />
          <Stat label="PFR" {...rate(t.pfr, t.hands)} />
          <Stat label="3-bet" {...rate(t.threeBets, t.threeBetChances)} />
          <Stat label="C-bet" {...rate(t.cbets, t.cbetChances)} />
          <Stat label="Bet : call" value={`${t.betsRaises} : ${t.calls}`} count="postflop" />
          <Stat label="Fold to bet" {...rate(t.foldsToBets, t.facedBets)} />
        </div>
      )}
    </div>
  );
}

function tone(amount: string): string {
  const value = Number.parseFloat(amount);
  return Number.isNaN(value) || value === 0 ? "" : value > 0 ? "pos" : "neg";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-30" as "Sep 30"; anything else as written. */
function shortDate(date: string): string {
  const m = date.match(/^\d{4}-(\d{2})-(\d{2})$/);
  const month = m ? MONTHS[Number(m[1]) - 1] : undefined;
  return m && month ? `${month} ${Number(m[2])}` : date;
}

// The profile's headline numbers, in the same order as tonight's where both have them.
const PROFILE_STATS: [string, string][] = [
  ["vpip", "VPIP"],
  ["pfr", "PFR"],
  ["threeBet", "3-bet"],
  ["cbet", "C-bet"],
  ["foldToFlopBet", "Fold to flop bet"],
];

function ProfileStats({ profile }: { profile: ProfileView }) {
  const { meta, stats, net } = profile;
  const head = [
    meta.hands !== undefined && `${meta.hands} hands`,
    typeof meta.sessions === "number" && plural(meta.sessions, "session"),
    meta.last_updated !== undefined && `updated ${shortDate(String(meta.last_updated))}`,
  ].filter(Boolean);
  const shown = PROFILE_STATS.filter(([key]) => stats[key]);
  if (shown.length === 0 && !net && head.length === 0) return null;
  return (
    <div class="group">
      <div class="group-head">
        <span class="label">Profile</span>
        <span class="muted">{head.join(" · ")}</span>
      </div>
      {(shown.length > 0 || net) && (
        <div class="statgrid">
          {shown.map(([key, label]) => (
            <Stat key={key} label={label} value={`${stats[key]?.pct}%`} count={stats[key]?.count} />
          ))}
          {net && <Stat label="Net" value={net.total} tone={tone(net.total)} />}
        </div>
      )}
      {net && net.sessions.length > 0 && (
        <div class="sessions" aria-label="Net by session">
          {net.sessions.map((s) => (
            <span key={s.label} class="session">
              <span class="name">{s.label}</span>
              {s.date && <span class="date">{shortDate(s.date)}</span>}
              <span class={`amount ${tone(s.amount)}`}>{s.amount}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Who the player is to VAMOS!: their profile and how it was matched, or an unmapped guest. */
function Identity({ player }: { player: PlayerInfo }) {
  if (player.profile) {
    return (
      <p class="alias">
        current in-game alias: <em>{player.name}</em>
        <span class="muted">
          {" "}
          · {player.source === "player_id" ? "matched by the PokerNow ID in the profile" : "matched by you"}
        </span>
      </p>
    );
  }
  return (
    <p class="alias">
      {player.status === "none" ? (
        <>
          You marked <em>{player.name}</em> as having no profile.
        </>
      ) : (
        <>
          Guest with no profile: <em>{player.name}</em> is not matched to anyone yet.
        </>
      )}{" "}
      <span class="muted">Claude sees only this session's numbers and your notes.</span>
    </p>
  );
}

function PlayerRow(props: { player: PlayerInfo; profiles: Players["profiles"]; gameId: string }) {
  const { player, profiles, gameId } = props;
  const profile = player.profile;
  const saved = savedDrafts.get(player.id);
  const kind = player.hero ? "me" : profile ? "mapped" : "guest";
  return (
    <li class={`player ${kind}`}>
      <div class="ident">
        <span class="who">{player.hero ? player.name : (profile?.name ?? player.name)}</span>
        {player.hero && <span class="you">You</span>}
        {profile?.tag && <span class="tag">{profile.tag}</span>}
        {!player.hero && !profile && (
          <span class="tag guest">{player.status === "none" ? "No profile" : "Unmapped guest"}</span>
        )}
        {!player.hero && (
          <span class="tools">
            <button
              type="button"
              class="mini"
              title="Write this session's read and your notes to data/drafts/ for you to review"
              onClick={() => send({ type: "save_draft", gameId, playerId: player.id })}
            >
              Save draft
            </button>
            {player.source !== "player_id" && <IdentityPicker player={player} profiles={profiles} />}
          </span>
        )}
      </div>
      {!player.hero && <Identity player={player} />}
      {profile?.summary && <p class="summary">{profile.summary}</p>}
      <TonightStats t={player.tonight} />
      {profile && <ProfileStats profile={profile} />}
      {!player.hero && <PlayerNotes player={player} gameId={gameId} />}
      {saved && <p class="muted small saved">Draft saved to {saved}</p>}
      {profile && (
        <details class="file">
          <summary>
            Profile file <span class="path">profiles/{profile.key}.md</span>
          </summary>
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
      <p class="muted small note-top">
        {plural(players.profiles.length, "profile")} loaded from history · click <span class="kbd">Save draft</span>{" "}
        to keep this game's read and notes for history
      </p>
      {players.problems.length > 0 && (
        <ul class="problems">
          {players.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {players.players.length > 0 ? (
        <ul class="players">
          {players.players.map((p) => (
            <PlayerRow key={p.id} player={p} profiles={players.profiles} gameId={players.gameId} />
          ))}
        </ul>
      ) : (
        <p class="muted small">Nobody seated yet.</p>
      )}
    </section>
  );
}

/** The wordmark's poker chip, edged along the action colors. */
function Chip() {
  return (
    <svg class="chip-mark" viewBox="0 0 24 24" aria-hidden="true">
      <defs>
        <linearGradient id="ladder" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="var(--call)" />
          <stop offset="0.4" stop-color="var(--bet)" />
          <stop offset="0.7" stop-color="var(--raise)" />
          <stop offset="1" stop-color="var(--allin)" />
        </linearGradient>
      </defs>
      <circle cx="12" cy="12" r="10" fill="none" stroke="url(#ladder)" stroke-width="3" stroke-dasharray="4.2 3.65" />
      <circle cx="12" cy="12" r="6" fill="none" stroke="url(#ladder)" stroke-width="1.5" />
      <text x="12" y="15.6" text-anchor="middle" font-size="9" font-weight="900" fill="currentColor">
        V
      </text>
    </svg>
  );
}

/** An on/off switch with its current meaning spelled out under it. */
function Switch(props: { label: string; on: boolean; hint: string; onChange: (on: boolean) => void }) {
  const { label, on, hint, onChange } = props;
  return (
    <button type="button" class={on ? "switch on" : "switch"} role="switch" aria-checked={on} onClick={() => onChange(!on)}>
      <span class="track">
        <span class="knob" />
      </span>
      <span class="text">
        <span class="name">{label}</span>
        <span class="hint">{hint}</span>
      </span>
    </button>
  );
}

function Panel() {
  const { status, acks, players, settings, costs, hand } = useWorkerState();
  const requestSnapshot = () => send({ type: "snapshot_request" });
  const state = status.state === "ready" ? "active" : status.state;

  return (
    <main>
      <header class="top">
        <div class="brandrow">
          <Chip />
          <span class="wordmark">{BRAND}</span>
          <span class={`state ${status.state}`}>
            {state}
            {status.version ? ` · v${status.version}` : ""}
            {status.recording ? " · recording log pages" : ""}
            {status.detail ? ` · ${status.detail}` : ""}
          </span>
        </div>
        {settings && (
          <div class="switches">
            <Switch
              label="AI advice"
              on={settings.aiOn}
              hint={settings.aiOn ? "Claude answers the close spots" : "off: no API calls, facts only"}
              onChange={(on) => send({ type: "settings", aiOn: on })}
            />
            <Switch
              label="Card on table"
              on={settings.showCard}
              hint={settings.showCard ? "shown on your turn" : "hidden"}
              onChange={(on) => send({ type: "settings", showCard: on })}
            />
            <Switch
              label="Badges on table"
              on={settings.showBadges}
              hint={settings.showBadges ? "under each seat" : "hidden"}
              onChange={(on) => send({ type: "settings", showBadges: on })}
            />
          </div>
        )}
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
