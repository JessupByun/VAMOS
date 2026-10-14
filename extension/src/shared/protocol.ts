// Messages between the content script, side panel, service worker and the native host.

export const HOST_NAME = "com.vamos.host";

export type BackendState = "connecting" | "ready" | "down";

export interface BackendStatus {
  type: "backend";
  state: BackendState;
  version?: string;
  detail?: string;
  /** What to do when Chrome could not start the backend at all. */
  fix?: string;
  /** Raw log pages are being saved (test-table sessions only). */
  recording?: boolean;
}

/** Characters kept of the notes on one player; the backend cuts at the same length. */
export const NOTES_LIMIT = 2_000;

/** Why a control that needs the backend cannot be used now, or null when it can. */
export function pausedWhy(status: BackendStatus): string | null {
  if (status.state === "ready") return null;
  return status.state === "connecting" ? "VAMOS's backend is starting" : "VAMOS's backend is not running";
}

/** One page of PokerNow's game log, forwarded untouched so the backend owns parsing. */
export interface LogPage {
  type: "log_page";
  gameId: string;
  fetchedAt: number;
  /** Fetched without a cursor: holds only the newest rows, which may not join up with earlier ones. */
  fresh: boolean;
  body: unknown;
}

export interface DomSnapshot {
  type: "dom_snapshot";
  gameId: string;
  capturedAt: number;
  html: string;
}

export interface SeatedPlayer {
  id: string;
  name: string;
}

/** The opponents currently seated at the table, and the hero's own seat when seated. */
export interface SeatsReport {
  type: "seats";
  gameId: string;
  players: SeatedPlayer[];
  hero: SeatedPlayer | null;
}

/** The user's match for a player: a profile key, or null for "no profile". */
export interface AssignIdentity {
  type: "assign_identity";
  playerId: string;
  profile: string | null;
}

/** Drop a match so the table asks again. */
export interface ForgetIdentity {
  type: "forget_identity";
  playerId: string;
}

export interface ProfileView {
  key: string;
  name: string;
  tag: string | null;
  /** A few frontmatter fields: hands, sessions (a count), net, last_updated. */
  meta: Record<string, string | number>;
  /** The net result, split by session when the frontmatter reads like "-5.10 (A -3.00, B -2.10)". */
  net: { total: string; sessions: { label: string; amount: string; date: string | null }[] } | null;
  /** The first sentence of the profile's "Summary" section. */
  summary: string | null;
  /**
   * Headline numbers found in the profile's text: vpip, pfr, threeBet, cbet, foldToFlopBet,
   * each a percent with its count when the profile gives one, e.g. {pct: "6", count: "7/114"}.
   */
  stats: Record<string, { pct: string; count: string | null }>;
  /** The profile's text, exactly as the user wrote it. */
  body: string;
}

export interface PlayerInfo {
  id: string;
  name: string;
  status: "profile" | "none" | "unknown";
  /** A profile's player_id (fixed) or the user's own answer (changeable). */
  source: "player_id" | "answer" | null;
  profile: ProfileView | null;
  /** This session's read, counted from the current game's log (rebuilt from its saved rows). */
  tonight: TonightRead | null;
  /** The hero's own row. */
  hero?: boolean;
  /** The user's notes on this player this session; sent to Claude with every hand they play. */
  notes: string;
}

export interface TonightRead {
  hands: number;
  /** Hands voluntarily played / raised preflop. */
  vpip: number;
  pfr: number;
  /** First re-raises preflop, out of hands where they acted facing exactly one raise. */
  threeBets: number;
  threeBetChances: number;
  /** Flop bets as the preflop raiser, out of flops where they acted with no bet before them. */
  cbets: number;
  cbetChances: number;
  /** Postflop bets and raises, and calls. */
  betsRaises: number;
  calls: number;
  /** Postflop folds when facing a bet, out of decisions facing one. */
  foldsToBets: number;
  facedBets: number;
}

/** One player in a profile update: how many new hands, and how writing their profile went. */
export interface ProfileUpdatePlayer {
  key: string;
  hands?: number;
  state?: "waiting" | "writing" | "written" | "failed" | "updated" | "restored";
  /** The new tag and VPIP once written, or why it failed. */
  summary?: string;
}

/**
 * Update profiles (and Create profile) as it goes: worked out ("planning", then "plan" or
 * "none"), confirmed and run ("running", then "done" or "failed"), or undone ("undone").
 * "busy" means another update is under way.
 */
export interface ProfilesUpdate {
  type: "profiles_update";
  state: "planning" | "plan" | "none" | "busy" | "running" | "done" | "failed" | "undone";
  players: ProfileUpdatePlayer[];
  create?: boolean;
  /** Rough dollars for the whole update, before it runs. */
  estimate?: number;
  /** Accounts in the new games that no profile claims (left out). */
  unknown?: number;
  /** A problem with the API key or credits. */
  problem?: string;
  /** Dollars spent so far. */
  cost?: number;
  canUndo?: boolean;
  message?: string;
}

/** A draft was written to drafts/ for the user to review. */
export interface DraftSaved {
  type: "draft_saved";
  playerId: string;
  /** Relative to the repo, e.g. "drafts/2026-10-05-alex.md" ("-2" for a second game that day). */
  path: string;
}

/** Save draft did not write the draft, and why, e.g. "could not write to the drafts folder (File exists)". */
export interface DraftFailed {
  type: "draft_failed";
  playerId: string;
  error: string;
}

/** The hand on the table from the hero's seat; sent whenever it changes. */
export interface HandInfo {
  type: "hand";
  gameId: string;
  /** The hand's ID, the first part of every decision node in it. */
  key: string;
  number: number;
  heroCards: string[];
  board: string[];
  /** Run it twice or a double board: the second board in full; empty otherwise. */
  secondBoard: string[];
  position: string | null;
  madeHand: string | null;
  folded: boolean;
  ended: boolean;
  /** Won or lost, in big blinds, once the hand is over. */
  resultBb: number | null;
  /** The same without its sign, in dollars and big blinds: "$0.60 (3bb)". */
  result: string | null;
}

/** One of this session's finished hands, in the side panel's "Replay an earlier hand" menu. */
export interface HistoryEntry {
  key: string;
  number: number;
  heroCards: string[];
  position: string | null;
  resultBb: number;
  /** Won or lost without its sign: "$0.60 (3bb)". */
  result: string;
  folded: boolean;
  /** The hero's decisions in the hand where VAMOS's AI had an answer on the card. */
  aiDecisions: number;
  /** The final pot, "$11.20 (56bb)", and in big blinds. */
  pot: string;
  potBb: number;
  /** No call, bet or raise of the hero's own: blinds posted, then folded or checked. */
  quiet: boolean;
  /** Anyone showed cards, even one card after winning without a call. */
  showdown: boolean;
  /** One of this session's biggest pots among the hands the hero played. */
  big: boolean;
}

/** The hero's finished hands this session, newest first; sent when the list changes. */
export interface HandHistory {
  type: "history";
  gameId: string;
  hands: HistoryEntry[];
}

export interface ReplayAction {
  kind: "action";
  player: string;
  position: string | null;
  hero: boolean;
  /** fold, check, call, bet, raise, allin, or post (blinds and antes), for its color. */
  verb: string;
  /** e.g. "raises to $0.60 (3bb)", "calls $0.40 (2bb), all in": a call says what it added. */
  text: string;
}

/** What VAMOS showed at one of the hero's decisions, just before the hero's action. */
export interface ReplayDecision {
  kind: "decision";
  advice: PreflopAdvice | ClaudeAdvice | null;
  aiOff: boolean;
  facts: { position?: string | null; pot?: string | null; toCall?: string | null; madeHand?: string | null };
}

export interface ReplayStreet {
  name: string;
  /** The board so far, this street's cards last. */
  board: string[];
  /** Each known hand (yours, and any shown) with what it makes on this street, yours first. */
  holdings: { name: string; hero: boolean; cards: string[]; made: string }[];
  /** In the middle when the street began: "$0.40 (2bb)", and in big blinds. */
  pot: string;
  potBb: number;
  events: (ReplayAction | ReplayDecision)[];
}

/** One earlier hand for the replayer, street by street. */
export interface HandDetail {
  type: "hand_detail";
  gameId: string;
  key: string;
  number: number;
  ended: boolean;
  /** Played before every decision was recorded: only Claude's answers were kept. */
  partial: boolean;
  heroCards: string[];
  board: string[];
  secondBoard: string[];
  /** The hero first, then who stayed in, then who folded; with the cards known: the hero's own, and anyone's shown. */
  players: {
    name: string;
    position: string | null;
    stack: string;
    stackBb: number;
    hero: boolean;
    cards: string[];
    /** What the hand made on the final board, when both cards are known. */
    made: string | null;
    folded: boolean;
    /** What the player won or lost: unsigned "$0.60 (3bb)", and signed in big blinds. */
    net: string;
    netBb: number;
  }[];
  streets: ReplayStreet[];
  /** The hero's win or loss without a sign, "$0.60 (3bb)", and in big blinds with one. */
  result: string | null;
  resultBb: number | null;
}

/** An earlier hand the backend could not replay, and why. */
export interface HandDetailFailed {
  type: "hand_detail_failed";
  gameId: string;
  key: string;
  error: string;
}

export interface Players {
  type: "players";
  gameId: string;
  players: PlayerInfo[];
  profiles: { key: string; name: string }[];
  /** This game's log rows VAMOS could not read; each is logged in data/logs/host.log. */
  unreadRows: number;
  /** Files VAMOS could not use as they are: a profile, or a damaged identity map. */
  problems: string[];
}

/** What the table shows at the hero's turn; amounts stay raw text for the backend to parse. */
export interface TableView {
  heroId: string;
  heroCards: string[];
  board: string[];
  /** The pot without this street's bets (`.table-pot-size .main-value`). */
  pot: string;
  /** Every seat's bet on this street. */
  bets: string[];
}

/** The hero's turn started or changed (`table`), or ended (null). */
export interface HeroTurn {
  type: "turn";
  gameId: string;
  /** Echoed by the facts reply, so the card only shows facts for what is on the table now. */
  key: string;
  table: TableView | null;
}

export interface SpotFacts {
  node: string;
  position: string | null;
  /** In dollars and big blinds, e.g. "$1.60 (8bb)". */
  pot: string | null;
  /** In dollars and big blinds, e.g. "$0.40 (2bb)"; null when nothing is owed. */
  toCall: string | null;
  /** Equity needed to call, in percent. */
  need: number | null;
  madeHand: string;
  /** A preflop rule's or the push/fold chart's action when one answers at once; otherwise
   * Claude's answer (before or after the flop), or why there is none; null when AI is off. */
  advice: PreflopAdvice | ClaudeAdvice | null;
  /** Set when AI is off and no preflop rule or chart answered: the card shows the facts only. */
  aiOff?: boolean;
  /** With Claude asked: when VAMOS stops waiting for an answer, in epoch milliseconds. */
  answerBy?: number;
}

/** One call's answer about a decision; a decision has a second one after "Ask deeper". */
export interface ClaudeAnswer {
  /** e.g. "Claude Opus 5.5 · low"; without the effort for answers logged before it was kept. */
  model: string;
  preset: Preset | null;
  state: "thinking" | "done" | "failed" | "cancelled";
  /** Why this call gave no answer, e.g. "timed out". */
  reason: string | null;
  action: string | null;
  mix: string[] | null;
  pick: number | null;
  pickPct: number | null;
  deviation: boolean;
  why: string | null;
  plan: string | null;
}

export interface ClaudeAdvice {
  source: "Claude";
  /** e.g. "BET $1.20 (6bb)"; null until Claude answers. */
  action: string | null;
  /** Every option Claude gave with its frequency, most frequent first. */
  mix: string[] | null;
  /** Which entry of mix was drawn, and its frequency in percent. */
  pick: number | null;
  pickPct: number | null;
  /** The draw came from a less frequent option than the top one. */
  deviation: boolean;
  /** The one fact that decides the spot, with real names. */
  why: string | null;
  /** The next street, or the answer to a raise; null when nothing follows. */
  plan: string | null;
  /** The model and effort of the answer shown (or, before any, of the call), e.g. "Claude Opus 5.5 · low". */
  model: string;
  /** The model and effort of the latest call, e.g. "Claude Opus 5.5 · medium" while Ask deeper runs. */
  asked: string;
  /** The presets behind the answer shown and the latest call. */
  preset: Preset;
  askedPreset: Preset;
  state: "thinking" | "done" | "failed" | "cancelled";
  /** Why the latest call gave no answer, e.g. "timed out". */
  reason: string | null;
  /** Every call about this decision in the order asked: the first answer, then a deeper one. */
  answers: ClaudeAnswer[];
}

export function isClaude(advice: SpotFacts["advice"]): advice is ClaudeAdvice {
  return advice !== null && "state" in advice;
}

export interface PreflopAdvice {
  /** "Chart" (heads-up push/fold) or "Rule" (a clear fold, check or raise). */
  source: string;
  /** e.g. "RAISE TO $0.60 (3bb)", drawn from the mix. */
  action: string | null;
  /** Every option with its frequency, most frequent first, e.g. ["raise $0.60 (3bb) 60%", "call 40%"]. */
  mix: string[] | null;
  /** Which entry of mix was drawn, and its frequency in percent. */
  pick: number | null;
  pickPct: number | null;
  /** The draw came from a less frequent option than the top one. */
  deviation: boolean;
}

/** Facts for the hero's turn, or null while the log has not caught up with the table. */
export interface Facts {
  type: "facts";
  gameId: string;
  key: string;
  facts: SpotFacts | null;
  /** Set when the backend failed on this spot, so the card says so instead of waiting. */
  error?: string;
}

/** Seconds left on the hero's decision clock, estimated from how fast its bar shrinks. */
export interface ClockReport {
  type: "clock";
  gameId: string;
  secondsLeft: number;
}

export type Preset = "standard" | "deep";

export interface PresetInfo {
  name: Preset;
  /** e.g. "Opus 5.5 · low thinking". */
  label: string;
  /** The model's thinking effort, e.g. "medium". */
  effort: string | null;
  /** Measured seconds to an answer, e.g. "2-5". */
  seconds: string;
}

export interface Settings {
  type: "settings";
  preset: Preset;
  presets: PresetInfo[];
  /** The preset "Ask deeper" uses. */
  deep: Preset;
  /** Claude's advice on or off; off makes no API calls at all. */
  aiOn: boolean;
  /** The action card on the table shown or hidden. */
  showCard: boolean;
  /** The players' badges under their seats shown or hidden. */
  showBadges: boolean;
}

/** Why polling a game's log is failing, and when the next request goes out (epoch ms). */
export interface LogTrouble {
  /** In plain words, e.g. "PokerNow's log is not answering (HTTP 404)". */
  problem: string;
  retryAt: number;
}

/** The tab polling a game's log, to the worker, the side panel and the game's other tabs. */
export interface LogStatus {
  type: "log_status";
  gameId: string;
  /** Null once PokerNow answers again. */
  trouble: LogTrouble | null;
}

/** "PokerNow's log is not answering (HTTP 404), retrying in 8 s", counted down to `now`. */
export function troubleText(trouble: LogTrouble, now: number): string {
  const seconds = Math.ceil((trouble.retryAt - now) / 1000);
  let wait = "retrying now";
  if (seconds >= 120) wait = `retrying in ${Math.ceil(seconds / 60)} min`;
  else if (seconds > 0) wait = `retrying in ${seconds} s`;
  return `${trouble.problem}, ${wait}`;
}

/** The hero's turn began in another tab on the game: the polling tab fetches the log now. */
export interface PollSoon {
  type: "poll_soon";
  gameId: string;
}

/** Another tab on the game polls its log from now on: this one stops. */
export interface PollStop {
  type: "poll_stop";
  gameId: string;
}

/** A game's tab was shown or hidden; the worker hands polling to a tab the user looks at. */
export interface TabVisibility {
  type: "visibility";
  gameId: string;
  visible: boolean;
}

/**
 * Content script to worker, when it would start polling a game's log (again). The worker
 * passes it on to the backend only from the one tab per game that polls.
 */
export interface ResumeRequest {
  type: "resume_request";
  gameId: string;
}

/** Where to pick the log up: after the newest row the backend already holds, else from scratch. */
export interface Resume {
  type: "resume";
  gameId: string;
  after: string | null;
  /**
   * Added by the worker when polling moved here from another tab on the game that is still
   * open: when that tab was told to stop (epoch ms), so the first request keeps clear of its last.
   */
  handedOverAt?: number;
  /** When PokerNow last asked the tab polling until now to wait until (epoch ms), if it did. */
  retryAt?: number;
  /** Why it asked, so this tab says so while it keeps to the wait. */
  problem?: string;
}

/** Why Claude's calls fail for the API key or its credits, in plain words with the fix; null once one gets through. */
export interface ApiStatus {
  type: "api_status";
  problem: string | null;
}

type Figure = "session" | "month" | "allTime";

/** Play at the table only: the developer's test replays are left out. */
export interface Costs {
  type: "costs";
  /** Dollars. */
  session: number;
  month: number;
  allTime: number;
  overCap: boolean;
  softCap: number;
  /** Of each figure, what was estimated for calls stopped before Claude's first reply. */
  estimated: Record<Figure, { dollars: number; calls: number }>;
  /** Per figure, the calls stopped before Claude's first reply with nothing comparable to estimate from. */
  unknown: Record<Figure, number>;
}

const FIGURES: [Figure, string][] = [
  ["session", "this session"],
  ["month", "this month"],
  ["allTime", "all time"],
];

/**
 * The figures where `count` is not 0, those in a row with the same value merged, each with
 * the figures it is for ("" when it is all three).
 */
function byFigure<T>(value: (figure: Figure) => T, count: (value: T) => number): { value: T; where: string }[] {
  const groups: { value: T; same: string; names: string[] }[] = [];
  for (const [figure, name] of FIGURES) {
    const v = value(figure);
    if (count(v) === 0) continue;
    const last = groups.at(-1);
    if (last?.same === JSON.stringify(v)) last.names.push(name);
    else groups.push({ value: v, same: JSON.stringify(v), names: [name] });
  }
  const alike = groups.length === 1 && groups[0]?.names.length === FIGURES.length;
  return groups.map((g) => ({ value: g.value, where: alike ? "" : ` ${g.names.join(" and ")}` }));
}

const inList = (parts: string[]) =>
  parts.length < 2 ? parts.join("") : `${parts.slice(0, -1).join(", ")}, and ${parts.at(-1)}`;
const callCount = (count: number) => `${count} call${count === 1 ? "" : "s"}`;
const about = (dollars: number) => (dollars > 0 && dollars < 0.005 ? "under $0.01" : `about $${dollars.toFixed(2)}`);

/**
 * How much of the costs is estimated, for calls stopped before Claude answered, and how many
 * such calls had nothing comparable to estimate them from; null when there are none.
 */
export function estimateNote(costs: Costs): string | null {
  const notes: string[] = [];
  const estimated = byFigure((figure) => costs.estimated[figure], (e) => e.calls);
  if (estimated.length > 0) {
    const parts = estimated.map(({ value, where }, i) =>
      i === 0
        ? `${about(value.dollars)} estimated for ${callCount(value.calls)} stopped before Claude answered${where}`
        : `${about(value.dollars)} for ${value.calls}${where}`,
    );
    notes.push(`Includes ${inList(parts)}.`);
  }
  const unknown = byFigure((figure) => costs.unknown[figure], (count) => count);
  if (unknown.length > 0) {
    const parts = unknown.map(({ value, where }, i) =>
      i === 0 ? `${callCount(value)} stopped before Claude answered${where}` : `${value}${where}`,
    );
    notes.push(`Leaves out ${inList(parts)}: nothing comparable to estimate from.`);
  }
  return notes.length > 0 ? notes.join(" ") : null;
}

export interface SnapshotRequest {
  type: "snapshot_request";
}

export interface HostAck {
  type: "ack";
  what: string;
  detail: string;
}

export type ContentToWorker =
  | LogPage
  | DomSnapshot
  | SeatsReport
  | AssignIdentity
  | HeroTurn
  | ClockReport
  | ResumeRequest
  | LogStatus
  | TabVisibility;
export type WorkerToContent =
  | BackendStatus
  | SnapshotRequest
  | Players
  | Facts
  | Settings
  | Resume
  | LogStatus
  | PollSoon
  | PollStop;
export type PanelToWorker =
  | SnapshotRequest
  | AssignIdentity
  | ForgetIdentity
  | { type: "settings"; preset?: Preset; aiOn?: boolean; showCard?: boolean; showBadges?: boolean }
  /** Notes on one player for this session's prompts, saved with that game's log. */
  | { type: "notes"; text: string; playerId: string; gameId?: string }
  /** Re-run the decision on the table with another preset ("Ask deeper"). */
  /** `node` is the decision Ask deeper was clicked on; a later one is not asked about. */
  | { type: "ask"; gameId: string; preset: Preset; node?: string }
  /** Write this session's read and notes on a player to drafts/ for the user to review. */
  | { type: "save_draft"; gameId: string; playerId: string }
  /** One earlier hand for the replayer. */
  | { type: "hand_detail_request"; gameId: string; key: string }
  /** The cost figures as of now: "this session" ends after a long pause with no call. */
  | { type: "costs_request" }
  /** What Update profiles would do, or Create profile for one player under `name`. */
  | { type: "profiles_plan"; create?: { name: string; playerId: string } }
  /** Run the update last planned. */
  | { type: "profiles_update" }
  /** Put back the profiles the last update replaced. */
  | { type: "profiles_undo" };
export type WorkerToPanel =
  | BackendStatus
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | ApiStatus
  | HandInfo
  | DraftSaved
  | DraftFailed
  | HandHistory
  | HandDetail
  | HandDetailFailed
  | ProfilesUpdate
  | LogStatus;
export type HostToWorker =
  | { type: "ready"; version: string; recording: boolean }
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | ApiStatus
  | HandInfo
  | DraftSaved
  | DraftFailed
  | HandHistory
  | HandDetail
  | HandDetailFailed
  | ProfilesUpdate
  | Resume;
