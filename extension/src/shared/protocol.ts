// Messages between the content script, side panel, service worker and the native host.

export const HOST_NAME = "com.vamos.host";

export type BackendState = "connecting" | "ready" | "down";

export interface BackendStatus {
  type: "backend";
  state: BackendState;
  version?: string;
  detail?: string;
  /** Raw log pages are being saved (test-table sessions only). */
  recording?: boolean;
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
  /** Headline numbers found in the profile's text, in percent, e.g. {vpip: "51", pfr: "23"}. */
  stats: Record<string, string>;
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
  /** Tonight's read, counted from tonight's log only and never saved. */
  tonight: TonightRead | null;
  /** The hero's own row. */
  hero?: boolean;
  /** The user's notes on this player tonight; sent to Claude with every hand they play. */
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

/** A draft profile was written for the user to review. */
export interface DraftSaved {
  type: "draft_saved";
  playerId: string;
  /** Relative to the repo, e.g. "data/drafts/alex-2026-10-03.md". */
  path: string;
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
}

export interface Players {
  type: "players";
  gameId: string;
  players: PlayerInfo[];
  profiles: { key: string; name: string }[];
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
  /** In big blinds, e.g. "8bb". */
  pot: string | null;
  /** Chips and big blinds, e.g. "0.40 (2bb)"; null when nothing is owed. */
  toCall: string | null;
  /** Equity needed to call, in percent. */
  need: number | null;
  madeHand: string;
  /** Preflop: the charts' action. Postflop: Claude's answer, or why there is none. */
  advice: PreflopAdvice | ClaudeAdvice | null;
}

/** One call's answer about a decision; a decision has a second one after "Ask deeper". */
export interface ClaudeAnswer {
  /** e.g. "Claude Opus 5.5 · low". */
  model: string;
  preset: Preset;
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
  /** e.g. "BET 1.20 (6bb)"; null until Claude answers. */
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
  source: string;
  /** e.g. "RAISE TO 0.60 (3bb)", drawn from the mix. */
  action: string | null;
  /** Every option with its frequency, most frequent first, e.g. ["raise 3bb 60%", "call 40%"]. */
  mix: string[] | null;
  /** Which entry of mix was drawn, and its frequency in percent. */
  pick: number | null;
  pickPct: number | null;
  /** The draw came from a less frequent option than the top one. */
  deviation: boolean;
  /** Set when the chart failed on this spot, e.g. "chart error (logged)". */
  error?: string;
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
  /** e.g. "Sonnet 5.5". */
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
}

export interface Costs {
  type: "costs";
  /** Dollars. */
  session: number;
  month: number;
  allTime: number;
  overCap: boolean;
  softCap: number;
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
  | ClockReport;
export type WorkerToContent = BackendStatus | SnapshotRequest | Players | Facts;
export type PanelToWorker =
  | SnapshotRequest
  | AssignIdentity
  | ForgetIdentity
  | { type: "settings"; preset: Preset }
  | { type: "settings_request" }
  /** Notes for tonight's prompts, on one player or the whole table; kept in memory only. */
  | { type: "notes"; text: string; playerId?: string }
  /** Re-run the decision on the table with another preset ("Ask deeper"). */
  | { type: "ask"; gameId: string; preset: Preset }
  /** Write tonight's read and notes on a player to data/drafts/ for the user to review. */
  | { type: "save_draft"; gameId: string; playerId: string };
export type WorkerToPanel =
  | BackendStatus
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | HandInfo
  | DraftSaved;
export type HostToWorker =
  | { type: "ready"; version: string; recording: boolean }
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | HandInfo
  | DraftSaved
  | { type: "error"; message: string };
