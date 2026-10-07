import { Fragment, render } from "preact";
import { useEffect, useState } from "preact/hooks";

import { actionClass } from "../shared/actions";
import {
  type BackendStatus,
  type ClaudeAdvice,
  type ClaudeAnswer,
  type Costs,
  type HandDetail,
  type HandInfo,
  type HistoryEntry,
  type HostAck,
  isClaude,
  type LogTrouble,
  NOTES_LIMIT,
  type PanelToWorker,
  pausedWhy,
  type PlayerInfo,
  type Players,
  type PreflopAdvice,
  type ReplayAction,
  type ReplayDecision,
  type ProfileView,
  type Settings,
  type TonightRead,
  troubleText,
  type WorkerToPanel,
} from "../shared/protocol";
import "./panel.css";

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;
// A replay or a draft the backend has not answered by then is said to have failed.
const REPLY_MS = 5000;
// "This session" ends after a long pause with no call, which no call announces.
const COSTS_REFRESH_MS = 60_000;
// Ask deeper says when its answer, usually 5-14 s at medium effort, may come too late.
const ASK_DEEPER_MIN_MS = 10_000;
// The notes field counts its characters from here on.
const NOTES_COUNT_FROM = NOTES_LIMIT - 200;
const NO_REPLY = "no reply from VAMOS's backend";
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
// Why Claude's calls fail for the API key or its credits, until one gets through.
let apiProblem: string | null = null;

// Per table: the players, the hand on the table (from the backend's hand updates) and
// Claude's answers in it, in order, so earlier streets stay readable. A decision node is
// "<hand>:<street>:<actions so far>"; a new hand clears the list the moment it is dealt.
interface Decision {
  node: string;
  street: string;
  advice: ClaudeAdvice;
  /** When VAMOS stops waiting for Claude on it, in epoch milliseconds. */
  answerBy: number | null;
}
interface Hand {
  gameId: string;
  key: string;
  info: HandInfo | null;
  decisions: Decision[];
  live: string | null;
}
const hands = new Map<string, Hand>();
// Per table: this session's finished hands for the replayer, the one picked (by hand key),
// and the replays received, by "<game>:<hand key>".
const histories = new Map<string, HistoryEntry[]>();
const pickedHand = new Map<string, string>();
const replays = new Map<string, HandDetail>();
// What the menu lists, and so what previous and next step through, chosen per table and kept
// while the panel is open.
const FILTERS = {
  all: { label: "All hands", keeps: (_: HistoryEntry) => true },
  played: { label: "Played", keeps: (e: HistoryEntry) => !e.quiet },
  big: { label: "Big pots", keeps: (e: HistoryEntry) => e.big },
  ai: { label: "AI decisions", keeps: (e: HistoryEntry) => e.aiDecisions > 0 },
  showdown: { label: "Showdowns", keeps: (e: HistoryEntry) => e.showdown },
};
type HandFilter = keyof typeof FILTERS;
const handFilters = new Map<string, HandFilter>();
// By "<game>:<hand key>": when a replay was asked for, until it comes, and why one failed.
const replayAsked = new Map<string, number>();
const replayErrors = new Map<string, string>();
// Per player: where the draft was last saved this session, when Save draft was pressed until
// the backend answers, and why the last one failed.
const savedDrafts = new Map<string, string>();
const draftAsked = new Map<string, number>();
const draftErrors = new Map<string, string>();
// Players whose notes were cut at NOTES_LIMIT on the last keystroke or paste.
const notesCut = new Set<string>();
const playersByGame = new Map<string, Players>();
// Per table, why PokerNow's log is failing, while it is.
const logTroubles = new Map<string, LogTrouble>();
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

function onFacts(gameId: string, node: string | null, advice: unknown, answerBy: number | null) {
  const hand = hands.get(gameId);
  if (node === null || !isClaude(advice as ClaudeAdvice | null)) {
    // The decision passed (or a preflop rule or chart answered, or AI is off): nothing on
    // this table is live.
    if (hand) hands.set(gameId, { ...hand, live: null });
    return;
  }
  const [key = "", street = ""] = node.split(":");
  const entry = { node, street: STREETS[Number(street)] ?? "", advice: advice as ClaudeAdvice, answerBy };
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
  // What the worker last heard may be hours old.
  next.postMessage({ type: "costs_request" } satisfies PanelToWorker);
  next.onMessage.addListener((message: WorkerToPanel) => {
    retryDelay = RECONNECT_MIN_MS;
    if (message.type === "backend") {
      const becameReady = message.state === "ready" && status.state !== "ready";
      status = message;
      if (message.state === "down") {
        // No table is open: nothing shown about one is current any more.
        hands.clear();
        playersByGame.clear();
        histories.clear();
        pickedHand.clear();
        replays.clear();
        replayAsked.clear();
        replayErrors.clear();
        logTroubles.clear();
      }
      if (becameReady) resendNotes();
    } else if (message.type === "players") {
      playersByGame.set(message.gameId, message);
      lastGame = message.gameId;
    }
    else if (message.type === "settings") settings = message;
    else if (message.type === "costs") costs = message;
    else if (message.type === "api_status") apiProblem = message.problem;
    else if (message.type === "draft_saved") {
      savedDrafts.set(message.playerId, message.path);
      draftAsked.delete(message.playerId);
      draftErrors.delete(message.playerId);
    } else if (message.type === "draft_failed") {
      draftErrors.set(message.playerId, message.error);
      draftAsked.delete(message.playerId);
    } else if (message.type === "hand") {
      onHand(message);
      lastGame = message.gameId;
    } else if (message.type === "facts") {
      const facts = message.facts;
      onFacts(message.gameId, facts?.node ?? null, facts?.advice ?? null, facts?.answerBy ?? null);
    } else if (message.type === "history") histories.set(message.gameId, message.hands);
    else if (message.type === "hand_detail") replays.set(`${message.gameId}:${message.key}`, message);
    else if (message.type === "hand_detail_failed") replayErrors.set(`${message.gameId}:${message.key}`, message.error);
    else if (message.type === "log_status") {
      if (message.trouble) logTroubles.set(message.gameId, message.trouble);
      else logTroubles.delete(message.gameId);
    } else if (message.type === "ack") acks = [message, ...acks].slice(0, 20);
    notifyAll();
  });
  next.onDisconnect.addListener(() => {
    // Reading the reason marks it handled; Chrome reports an unread one as an error.
    void chrome.runtime.lastError;
    if (port === next) port = null;
    status = { type: "backend", state: "connecting" };
    // A restarted worker pauses every table's poller, so word that PokerNow answers again may
    // never come; the worker sends the problems still current once the panel is back.
    logTroubles.clear();
    notifyAll();
    setTimeout(connect, retryDelay);
    retryDelay = Math.min(retryDelay * 2, RECONNECT_MAX_MS);
  });
}

connect();
setInterval(() => {
  if (status.state === "ready") send({ type: "costs_request" });
}, COSTS_REFRESH_MS);

/** Re-renders every `ms` while `on`, for what counts down. */
function useTick(ms: number, on = true) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setTick((n) => n + 1), ms);
    return () => clearInterval(timer);
  }, [ms, on]);
}

/** Asks the backend for an earlier hand; it says so if no answer comes in time. */
function askReplay(gameId: string, key: string) {
  const id = `${gameId}:${key}`;
  replayAsked.set(id, Date.now());
  replayErrors.delete(id);
  send({ type: "hand_detail_request", gameId, key });
  setTimeout(notifyAll, REPLY_MS);
}

/** Opens an earlier hand in the replayer; the menu shows it picked. */
function openReplay(gameId: string, key: string) {
  pickedHand.set(gameId, key);
  askReplay(gameId, key);
  notifyAll();
}

/** The hands the menu's filter keeps just before and just after `key`: older and newer. */
function neighbors(gameId: string, key: string): { older?: HistoryEntry; newer?: HistoryEntry } {
  const entries = histories.get(gameId) ?? [];
  const { keeps } = FILTERS[handFilters.get(gameId) ?? "all"];
  const at = entries.findIndex((entry) => entry.key === key);
  if (at < 0) return {};
  // Newest first, as in the menu.
  return { older: entries.slice(at + 1).find(keeps), newer: entries.slice(0, at).reverse().find(keeps) };
}

function saveDraft(gameId: string, playerId: string) {
  draftAsked.set(playerId, Date.now());
  draftErrors.delete(playerId);
  send({ type: "save_draft", gameId, playerId });
  setTimeout(notifyAll, REPLY_MS);
  notifyAll();
}

/** Asked at `asked` and not answered within REPLY_MS. */
const late = (asked: number | undefined) => asked !== undefined && Date.now() - asked >= REPLY_MS;

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
  // Before any table has said who sits there, a failing log is still worth saying.
  const logTrouble = (game ? logTroubles.get(game) : logTroubles.values().next().value) ?? null;
  return { status, acks, players, settings, costs, hand, logTrouble, apiProblem };
}

const send = (message: PanelToWorker) => port?.postMessage(message);

/** "BET $0.20 (1bb)": the move and its dollars large in its action's color, its size in big blinds quieter. */
function Pick({ text }: { text: string }) {
  const split = text.match(/^(.*?)( \([^)]*\))$/);
  return (
    <p class={`pick ${actionClass(text)}`}>
      {split ? split[1] : text}
      {split && <span class="bb">{split[2]}</span>}
    </p>
  );
}

function Options({
  answer,
}: {
  answer: { mix: string[] | null; pick: number | null; deviation: boolean; pickPct: number | null };
}) {
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
  // Until 2026-10-07 Ask deeper stopped a first answer still thinking; older hands say so.
  if (answer.state === "cancelled" && later) return "Stopped when you asked deeper.";
  return live && answer.state === "thinking" ? "Thinking..." : "No answer before you acted.";
}

/** One call's answer; a deeper one is added under the first, never in its place. */
function AnswerView(props: { answer: ClaudeAnswer; deeper: boolean; live: boolean; later: boolean }) {
  const { answer, deeper, live, later } = props;
  return (
    <div class={`answer ${answer.preset ?? ""}${deeper ? " deeper" : ""}`}>
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

function DecisionView(props: { decision: Decision; live: boolean; gameId: string; paused: string | null }) {
  const { decision, live, gameId, paused } = props;
  const answers = decision.advice.answers;
  const askable = live && !answers.some((a) => a.preset === "deep");
  useTick(1000, askable && decision.answerBy !== null);
  const short = askable && decision.answerBy !== null && decision.answerBy - Date.now() < ASK_DEEPER_MIN_MS;
  return (
    <div class={live ? "decision live" : "decision"}>
      <div class="head">
        <span class="street">{decision.street}</span>
        {live && <span class="now">now</span>}
      </div>
      {answers.map((answer, i) => (
        <AnswerView key={i} answer={answer} deeper={i > 0} live={live} later={i < answers.length - 1} />
      ))}
      {askable && (
        <button
          type="button"
          class="secondary"
          disabled={paused !== null}
          title={paused ?? undefined}
          onClick={() => send({ type: "ask", gameId, preset: "deep" })}
        >
          Ask deeper
        </button>
      )}
      {askable && short && (
        <p class="hurry small">Little time left on the clock: the deeper answer may not arrive in time.</p>
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
  const result = info.resultBb === null ? (info.folded ? "folded" : null) : resultText(info.resultBb, info.result);
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

function CurrentHand({ hand, paused }: { hand: Hand; paused: string | null }) {
  return (
    <section class="card advice">
      <div class="section-head">
        <h2>Current hand</h2>
        <HistoryPicker gameId={hand.gameId} />
      </div>
      {hand.info && <HandHeader info={hand.info} />}
      {hand.decisions.length === 0 ? (
        <p class="muted small">No AI decisions yet this hand. Preflop rule and chart answers show on the card only.</p>
      ) : (
        hand.decisions.map((d) => (
          <DecisionView key={d.node} decision={d} live={d.node === hand.live} gameId={hand.gameId} paused={paused} />
        ))
      )}
    </section>
  );
}

const cardText = (cards: string[]) => cards.map((c) => `${c[0] === "T" ? "10" : c[0]}${SUITS[c[1] ?? ""] ?? ""}`).join(" ");

/** "won $0.60 (3bb)": `result` is the amount without its sign, `resultBb` gives the sign. */
function resultText(resultBb: number | null, result: string | null): string | null {
  if (resultBb === null) return null;
  if (resultBb > 0) return `won ${result ?? `${resultBb}bb`}`;
  if (resultBb < 0) return `lost ${result ?? `${-resultBb}bb`}`;
  return "even";
}

/**
 * One hand in the menu: number, cards and seat, then the result in won/lost colors; one of
 * the session's biggest pots also gives its pot (bold), a hand without action is quieter.
 */
function HistoryLabel({ entry }: { entry: HistoryEntry }) {
  const cards = entry.heroCards.length ? cardText(entry.heroCards) : "no cards";
  const result =
    entry.folded && entry.resultBb <= 0
      ? `folded${entry.resultBb < 0 ? `, lost ${entry.result}` : ""}`
      : resultText(entry.resultBb, entry.result);
  const tone = entry.resultBb > 0 ? "won" : entry.resultBb < 0 ? "lost" : "";
  return (
    <>
      {[`#${entry.number}`, cards, entry.position].filter(Boolean).join(" · ")}
      {result && (
        <>
          {" · "}
          <span class={`res ${tone}`}>{result}</span>
        </>
      )}
      {entry.big && <span class="potmark"> · pot {entry.pot}</span>}
    </>
  );
}

function pickedLabel(entry: HistoryEntry): string {
  return [`Replaying #${entry.number}`, entry.heroCards.length ? cardText(entry.heroCards) : ""].filter(Boolean).join(" · ");
}

/**
 * The menu of this session's earlier hands and its filter, in the Current hand header: no
 * space of their own.
 */
function HistoryPicker({ gameId }: { gameId: string }) {
  const entries = histories.get(gameId) ?? [];
  if (entries.length === 0) return null;
  const picked = pickedHand.get(gameId);
  const chosen = handFilters.get(gameId) ?? "all";
  const { keeps } = FILTERS[chosen];
  const kept = entries.filter(keeps).length;
  const pick = (event: Event) => {
    const key = (event.currentTarget as HTMLSelectElement).value;
    if (key) {
      openReplay(gameId, key);
    } else {
      pickedHand.delete(gameId);
      notifyAll();
    }
  };
  const filter = (event: Event) => {
    handFilters.set(gameId, (event.currentTarget as HTMLSelectElement).value as HandFilter);
    notifyAll();
  };
  return (
    <span class="history-nav">
      <select class="history-filter" aria-label="Filter earlier hands" value={chosen} onChange={filter}>
        {Object.entries(FILTERS).map(([key, { label }]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>
      <select class="history-pick" aria-label="Replay an earlier hand" value={picked ?? ""} onChange={pick}>
        <option value="">Replay an earlier hand ({kept})</option>
        {/* The hand being replayed stays listed when the filter leaves it out, so the closed
            menu still names it. */}
        {entries
          .filter((entry) => keeps(entry) || entry.key === picked)
          .map((entry) => (
            <option key={entry.key} value={entry.key} class={entry.big ? "big" : entry.quiet ? "quiet" : undefined}>
              {/* The closed menu shows the picked hand, short: the replayer below has the rest. */}
              {entry.key === picked ? pickedLabel(entry) : <HistoryLabel entry={entry} />}
            </option>
          ))}
      </select>
    </span>
  );
}

type Verdict = {
  action: string | null;
  mix: string[] | null;
  pick: number | null;
  pickPct: number | null;
  deviation: boolean;
  why?: string | null;
  plan?: string | null;
};

/** One thing VAMOS said at a decision, as its own line just before what you did. */
function VamosLine({ verdict, none, source }: { verdict?: Verdict; none?: string; source: string }) {
  const split = verdict?.action?.match(/^(.*?)( \([^)]*\))$/);
  return (
    <div class="vamos">
      <p class="vamos-head">
        <span class="brand">{BRAND}</span>
        {verdict?.action ? (
          <span class={`move ${actionClass(verdict.action)}`}>
            {split ? split[1] : verdict.action}
            {split && <span class="bb">{split[2]}</span>}
          </span>
        ) : (
          <span class="none">{none}</span>
        )}
        {source && <span class="src">{source}</span>}
      </p>
      {verdict && (verdict.mix?.length ?? 0) > 1 && <Options answer={verdict} />}
      {verdict?.why && <p class="vamos-text">{verdict.why}</p>}
      {verdict?.plan && (
        <p class="vamos-text">
          <span class="label">Next:</span> {verdict.plan}
        </p>
      )}
    </div>
  );
}

/** What VAMOS showed at one of the hero's decisions in an earlier hand. */
function ReplayAdvice({ decision }: { decision: ReplayDecision }) {
  const { advice } = decision;
  if (advice && isClaude(advice)) {
    const last = advice.answers.length - 1;
    return (
      <>
        {advice.answers.map((answer, i) => (
          <VamosLine
            key={i}
            verdict={answer.action ? answer : undefined}
            none={waiting(answer, false, i < last).replace(/\.$/, "")}
            source={`${answer.model}${i > 0 ? ", asked deeper" : ""}`}
          />
        ))}
      </>
    );
  }
  const preflop = advice as PreflopAdvice | null;
  if (preflop?.action) {
    return <VamosLine verdict={preflop} source={preflop.source === "Rule" ? "Preflop rule" : "Preflop chart"} />;
  }
  return <VamosLine none="still reading the hand" source="" />;
}

function ReplayLine({ event }: { event: ReplayAction }) {
  const colored = event.hero && event.verb !== "post";
  return (
    <div class={`act-line${event.hero ? " hero" : ""}${event.verb === "post" ? " post" : ""}`}>
      <span class="who">{event.hero ? "You" : event.player}</span>
      <span class="pos">{event.position}</span>
      <span class={colored ? `what act-${event.verb}` : "what"}>{event.text}</span>
    </div>
  );
}

const capitalized = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

/** Who was dealt in: their cards, their stack, and what each won or lost. */
function ReplayPlayers({ players }: { players: HandDetail["players"] }) {
  return (
    <div class="replay-players">
      {players.map((p) => (
        <div key={`${p.name}-${p.position}`} class={`seat${p.hero ? " hero" : ""}`}>
          <span class="who" title={p.name}>
            <span>
              {p.hero ? "You" : p.name}
              {p.position && <span class="pos">{p.position}</span>}
            </span>
            {p.made && <span class="made">{capitalized(p.made)}</span>}
          </span>
          {/* Cards never shown stay face down. */}
          <span class="hole">
            {p.cards.length > 0
              ? p.cards.map((c) => <Card key={c} card={c} />)
              : [0, 1].map((i) => <span key={i} class="pcard back" role="img" aria-label="card not shown" />)}
          </span>
          {/* What they won or lost, over the stack they started with. */}
          <span class="money">
            <span class={`net${p.netBb > 0 ? " won" : p.netBb < 0 ? " lost" : ""}`}>
              {p.netBb > 0 ? `+${p.net}` : p.netBb < 0 ? `-${p.net}` : "even"}
            </span>
            <span class="stack">stack {p.stack}</span>
          </span>
          {/* Carries the line between players across the rest of the width. */}
          <span class="fill" />
        </div>
      ))}
    </div>
  );
}

function Chevron({ to }: { to: "left" | "right" }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
      <path
        d={to === "left" ? "M10 3 5 8l5 5" : "M6 3l5 5-5 5"}
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  );
}

/** An earlier hand, street by street, with VAMOS's advice next to what you did. */
function HandReplay({ gameId, handKey }: { gameId: string; handKey: string }) {
  const id = `${gameId}:${handKey}`;
  const detail = replays.get(id);
  const failed = replayErrors.get(id) ?? (late(replayAsked.get(id)) ? NO_REPLY : null);
  const close = () => {
    pickedHand.delete(gameId);
    notifyAll();
  };
  // With the AI off, a decision no rule answered has nothing to show: said once, above the hand.
  const silent = (event: ReplayAction | ReplayDecision) => event.kind === "decision" && event.aiOff && !event.advice?.action;
  const aiOff = detail?.streets.some((street) => street.events.some(silent)) ?? false;
  // The number shows at once, while a hand stepped to is still loading.
  const number = histories.get(gameId)?.find((entry) => entry.key === handKey)?.number ?? detail?.number;
  const { older, newer } = neighbors(gameId, handKey);
  return (
    <section class="card replay" aria-label="Hand replayer">
      <div class="section-head">
        <h2>Hand replayer{number ? ` · #${number}` : ""}</h2>
        <span class="replay-tools">
          <span class="stepper">
            <button
              type="button"
              aria-label="Previous hand"
              title={older ? `Previous hand: #${older.number}` : "No earlier hand in the menu"}
              disabled={!older}
              onClick={() => older && openReplay(gameId, older.key)}
            >
              <Chevron to="left" />
            </button>
            <button
              type="button"
              aria-label="Next hand"
              title={newer ? `Next hand: #${newer.number}` : "No later hand in the menu"}
              disabled={!newer}
              onClick={() => newer && openReplay(gameId, newer.key)}
            >
              <Chevron to="right" />
            </button>
          </span>
          <button type="button" class="close" aria-label="Close the hand replayer" onClick={close}>
            ×
          </button>
        </span>
      </div>
      {!detail && failed && (
        <div class="replay-failed" role="status">
          <p class="small">Could not load this hand: {failed}.</p>
          <button type="button" class="mini" onClick={() => askReplay(gameId, handKey)}>
            Try again
          </button>
        </div>
      )}
      {!detail && !failed && <p class="muted small">Loading...</p>}
      {detail && (
        <>
          {detail.board.length > 0 && (
            <div class="hand-head">
              <div class="cards">
                <span class="board" aria-label="Board">
                  {detail.board.map((c) => (
                    <Card key={c} card={c} />
                  ))}
                </span>
                {detail.secondBoard.length > 0 && (
                  <span class="board second" title="second run">
                    {detail.secondBoard.map((c) => (
                      <Card key={`2-${c}`} card={c} />
                    ))}
                  </span>
                )}
              </div>
            </div>
          )}
          <ReplayPlayers players={detail.players} />
          {detail.partial && (
            <p class="muted small">Only Claude's answers were saved for this hand, not the preflop rule and chart answers.</p>
          )}
          {aiOff && (
            <p class="vamos-head vamos-off">
              <span class="brand">{BRAND}</span>
              <span class="none">AI was off for this hand: stats only</span>
            </p>
          )}
          <div class="timeline">
            {detail.streets.map((street) => (
              <Fragment key={street.name}>
                <div class="street-head">
                  <span class="street">{street.name}</span>
                  {street.board.length > 0 && (
                    <span class="board">
                      {street.board.map((c) => (
                        <Card key={c} card={c} />
                      ))}
                    </span>
                  )}
                  {street.potBb > 0 && <span class="pot">pot {street.pot}</span>}
                </div>
                {street.holdings.length > 0 && (
                  <div class="holdings">
                    {street.holdings.map((h) => (
                      <span key={h.name} class={`holding${h.hero ? " hero" : ""}`}>
                        <span class="who">{h.hero ? "You" : h.name}</span>
                        <span class="hole">
                          {h.cards.map((c) => (
                            <Card key={c} card={c} />
                          ))}
                        </span>
                        <span class="made">{capitalized(h.made)}</span>
                      </span>
                    ))}
                  </div>
                )}
                {street.events.map((event, i) =>
                  silent(event) ? null : event.kind === "action" ? (
                    <ReplayLine key={i} event={event} />
                  ) : (
                    <ReplayAdvice key={i} decision={event} />
                  ),
                )}
              </Fragment>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function Dial({ settings, paused }: { settings: Settings; paused: string | null }) {
  return (
    <section class="card">
      <h2>Model</h2>
      <div
        class={`segmented${paused ? " paused" : ""}`}
        role="radiogroup"
        aria-label="Model"
        title={paused ?? undefined}
      >
        {settings.presets.map((preset) => (
          <label key={preset.name} class={`${preset.name}${settings.preset === preset.name ? " on" : ""}`}>
            <input
              type="radio"
              name="preset"
              checked={settings.preset === preset.name}
              disabled={paused !== null}
              onChange={() => send({ type: "settings", preset: preset.name })}
            />
            <span class="model">{preset.label}</span>
            <span class="detail">usually {preset.seconds} s</span>
          </label>
        ))}
      </div>
      <p class="muted small">For every AI decision, before and after the flop. A change applies from the next hand.</p>
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
      <p class="muted small spend-note">
        Counted from the calls VAMOS records. The Anthropic Console is exact, and also counts anything else that
        uses your API key.
      </p>
    </section>
  );
}

const NO_PROFILE = "__none__";
const ASK_AGAIN = "__ask__";
const MATCH = "Match existing player profile";

function pct(count: number, of: number): string {
  return `${Math.round((100 * count) / of)}%`;
}

function IdentityPicker(props: { player: PlayerInfo; profiles: Players["profiles"]; paused: string | null }) {
  const { player, profiles, paused } = props;
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
    <select
      aria-label={`Match a profile for ${player.name}`}
      value={current}
      disabled={paused !== null}
      title={paused ?? undefined}
      onChange={change}
    >
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

function PlayerNotes({ player, gameId, paused }: { player: PlayerInfo; gameId: string; paused: string | null }) {
  const [, rerender] = useState(0);
  const value = drafts.get(player.id) ?? player.notes;
  const change = (event: Event) => {
    const input = event.currentTarget as HTMLInputElement;
    let text = input.value;
    // What the field shows is what is kept: the backend keeps no more either.
    if (text.length > NOTES_LIMIT) {
      text = text.slice(0, NOTES_LIMIT);
      input.value = text;
      notesCut.add(player.id);
    } else {
      notesCut.delete(player.id);
    }
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
  const limit = NOTES_LIMIT.toLocaleString("en-US");
  return (
    <>
      <input
        class="notes"
        type="text"
        value={value}
        disabled={paused !== null}
        title={paused ?? undefined}
        onInput={change}
        onBlur={flush}
        placeholder="Notes for this session, e.g. very bluffy"
        aria-label={`Notes on ${player.name}`}
      />
      {notesCut.has(player.id) ? (
        <p class="small notes-count cut" role="status">
          Cut at {limit} characters: the rest was not kept.
        </p>
      ) : (
        value.length >= NOTES_COUNT_FROM && (
          <p class="muted small notes-count">
            {value.length.toLocaleString("en-US")} of {limit} characters
          </p>
        )
      )}
    </>
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

function PlayerRow(props: { player: PlayerInfo; profiles: Players["profiles"]; gameId: string; paused: string | null }) {
  const { player, profiles, gameId, paused } = props;
  const profile = player.profile;
  const saved = savedDrafts.get(player.id);
  const saving = draftAsked.get(player.id);
  const notSaved = draftErrors.get(player.id) ?? (late(saving) ? NO_REPLY : null);
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
              disabled={paused !== null}
              title={
                paused ??
                "Write this session's read and your notes to drafts/ for you to review (saving again replaces this game's draft)"
              }
              onClick={() => saveDraft(gameId, player.id)}
            >
              Save draft
            </button>
            {player.source !== "player_id" && <IdentityPicker player={player} profiles={profiles} paused={paused} />}
          </span>
        )}
      </div>
      {!player.hero && <Identity player={player} />}
      {profile?.summary && <p class="summary">{profile.summary}</p>}
      <TonightStats t={player.tonight} />
      {profile && <ProfileStats profile={profile} />}
      {!player.hero && <PlayerNotes player={player} gameId={gameId} paused={paused} />}
      {notSaved ? (
        <p class="small draft-failed" role="status">
          Draft not saved: {notSaved}
        </p>
      ) : saving !== undefined ? (
        <p class="muted small saved">Saving the draft...</p>
      ) : (
        saved && <p class="muted small saved">Draft saved to {saved}</p>
      )}
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

function PlayersSection({ players, paused }: { players: Players; paused: string | null }) {
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
            <PlayerRow key={p.id} player={p} profiles={players.profiles} gameId={players.gameId} paused={paused} />
          ))}
        </ul>
      ) : (
        <p class="muted small">Nobody seated yet.</p>
      )}
    </section>
  );
}

/** Log rows VAMOS could not read in the shown game: said, never guessed at. */
function UnreadRows({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <p class="unread" role="status">
      {plural(count, "log line")} VAMOS could not read this session
      <span class="where">Listed in data/logs/host.log</span>
    </p>
  );
}

/** Why PokerNow's log is failing for the shown table, counted down to the next try. */
function LogProblem({ trouble }: { trouble: LogTrouble }) {
  useTick(1000);
  return (
    <p class="log-problem" role="status">
      {troubleText(trouble, Date.now())}
    </p>
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
function Switch(props: {
  label: string;
  on: boolean;
  hint: string;
  paused: string | null;
  onChange: (on: boolean) => void;
}) {
  const { label, on, hint, paused, onChange } = props;
  return (
    <button
      type="button"
      class={on ? "switch on" : "switch"}
      role="switch"
      aria-checked={on}
      disabled={paused !== null}
      title={paused ?? undefined}
      onClick={() => onChange(!on)}
    >
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
  const { status, acks, players, settings, costs, hand, logTrouble, apiProblem } = useWorkerState();
  const requestSnapshot = () => send({ type: "snapshot_request" });
  const state = status.state === "ready" ? "active" : status.state;
  // What is sent while the backend is not ready would be lost, so it cannot be changed then.
  const paused = pausedWhy(status);

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
        {status.fix && (
          <p class="fix" role="status">
            {status.fix}
          </p>
        )}
        {logTrouble && <LogProblem trouble={logTrouble} />}
        {apiProblem && (
          <p class="api-problem" role="status">
            {apiProblem}
          </p>
        )}
        {settings && (
          <div class="switches">
            <Switch
              label="AI Advice"
              on={settings.aiOn}
              hint={settings.aiOn ? "mixed preflop and AI postflop" : "off: preflop rules only, no API calls"}
              paused={paused}
              onChange={(on) => send({ type: "settings", aiOn: on })}
            />
            <Switch
              label="VAMOS! HUD"
              on={settings.showCard}
              hint={settings.showCard ? "displayed on your turn" : "hidden"}
              paused={paused}
              onChange={(on) => send({ type: "settings", showCard: on })}
            />
            <Switch
              label="Per-Player Stats"
              on={settings.showBadges}
              hint={settings.showBadges ? "under each seat" : "hidden"}
              paused={paused}
              onChange={(on) => send({ type: "settings", showBadges: on })}
            />
          </div>
        )}
        {settings && paused && <p class="paused-why">{paused}, so these controls are paused.</p>}
        {players && <UnreadRows count={players.unreadRows} />}
      </header>
      {settings && <Dial settings={settings} paused={paused} />}
      {costs && <Spend costs={costs} />}
      {hand && <CurrentHand hand={hand} paused={paused} />}
      {hand && pickedHand.has(hand.gameId) && (
        <HandReplay gameId={hand.gameId} handKey={pickedHand.get(hand.gameId) ?? ""} />
      )}
      {players && <PlayersSection players={players} paused={paused} />}
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
