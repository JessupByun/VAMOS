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
}

/** Facts for the hero's turn, or null while the log has not caught up with the table. */
export interface Facts {
  type: "facts";
  gameId: string;
  key: string;
  facts: SpotFacts | null;
}

export interface SnapshotRequest {
  type: "snapshot_request";
}

export interface HostAck {
  type: "ack";
  what: string;
  detail: string;
}

export type ContentToWorker = LogPage | DomSnapshot | SeatsReport | AssignIdentity | HeroTurn;
export type WorkerToContent = BackendStatus | SnapshotRequest | Players | Facts;
export type PanelToWorker = SnapshotRequest | AssignIdentity | ForgetIdentity;
export type WorkerToPanel = BackendStatus | HostAck | Players;
export type HostToWorker =
  | { type: "ready"; version: string; recording: boolean }
  | HostAck
  | Players
  | Facts
  | { type: "error"; message: string };
