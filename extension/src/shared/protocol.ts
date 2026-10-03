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

/** The opponents currently seated at the table (the hero is never reported). */
export interface SeatsReport {
  type: "seats";
  gameId: string;
  players: SeatedPlayer[];
}

/** The user's answer to "who is this?": a profile key, or null for "no profile". */
export interface AssignIdentity {
  type: "assign_identity";
  playerId: string;
  profile: string | null;
}

/** Drop an answer so the table asks "who is this?" again. */
export interface ForgetIdentity {
  type: "forget_identity";
  playerId: string;
}

export interface PlayerInfo {
  id: string;
  name: string;
  status: "profile" | "none" | "unknown";
  /** A profile's player_id (fixed) or the user's own answer (changeable). */
  source: "player_id" | "answer" | null;
  profile: { key: string; name: string; tag: string | null } | null;
  tonight: { hands: number; vpip: number } | null;
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
  equity: number;
  /** Who the equity is against, e.g. "a random hand". */
  equityVs: string;
  madeHand: string;
  /** Preflop: the charts' action. Postflop: Claude's answer, or why there is none. */
  advice: PreflopAdvice | ClaudeAdvice | null;
}

export interface ClaudeAdvice {
  /** "Math" once a call failed with no earlier answer to show. */
  source: "Claude" | "Math";
  /** e.g. "BET 1.20 (6bb)"; null until Claude answers. */
  action: string | null;
  mix: string[] | null;
  /** Claude's reasoning, with real names. */
  note: string | null;
  /** The model whose answer is shown (or, before any answer, the one asked), e.g. "Claude Sonnet 5.5". */
  model: string;
  /** The model of the latest call, e.g. "Claude Opus 5.5" while Ask Opus runs. */
  asked: string;
  state: "thinking" | "done" | "failed" | "cancelled";
  /** Why the latest call gave no answer, e.g. "timed out". */
  reason: string | null;
}

export function isClaude(advice: SpotFacts["advice"]): advice is ClaudeAdvice {
  return advice !== null && "state" in advice;
}

export interface PreflopAdvice {
  source: string;
  /** e.g. "RAISE TO 0.60 (3bb)", sampled from the mix. */
  action: string | null;
  /** e.g. ["raise 3bb 60%", "call 40%"]; null when one action is 90% or more. */
  mix: string[] | null;
}

/** Facts for the hero's turn, or null while the log has not caught up with the table. */
export interface Facts {
  type: "facts";
  gameId: string;
  key: string;
  facts: SpotFacts | null;
}

/** Seconds left on the hero's decision clock, estimated from how fast its bar shrinks. */
export interface ClockReport {
  type: "clock";
  gameId: string;
  secondsLeft: number;
}

export type Preset = "fast" | "balanced" | "deep";

export interface Settings {
  type: "settings";
  preset: Preset;
  presets: Preset[];
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
  /** Quick notes for tonight's prompts; kept in memory only. */
  | { type: "notes"; text: string }
  /** Re-run the decision on the table with another preset ("Ask Opus"). */
  | { type: "ask"; gameId: string; preset: Preset };
export type WorkerToPanel = BackendStatus | HostAck | Players | Facts | Settings | Costs;
export type HostToWorker =
  | { type: "ready"; version: string; recording: boolean }
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | { type: "error"; message: string };
