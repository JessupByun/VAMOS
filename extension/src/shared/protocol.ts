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

/** A draft was written to drafts/ for the user to review. */
export interface DraftSaved {
  type: "draft_saved";
  playerId: string;
  /** Relative to the repo, e.g. "drafts/2026-10-05-alex.md" ("-2" for a second game that day). */
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
  /** Decisions VAMOS answered in the hand. */
  decisions: number;
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
  /** e.g. "raises to $0.60 (3bb)", "calls $0.40 (2bb) (all in)": a call says what it added. */
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
  players: { name: string; position: string | null; stack: string; stackBb: number; hero: boolean }[];
  streets: ReplayStreet[];
  shows: { name: string; cards: string[]; hero: boolean }[];
  /** amount: what each collected, "$0.40 (2bb)". */
  winners: { name: string; amount: string; amountBb: number; hero: boolean }[];
  /** The hero's win or loss without a sign, "$0.60 (3bb)", and in big blinds with one. */
  result: string | null;
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

/** Content script to backend, when it starts polling a game's log (again). */
export interface ResumeRequest {
  type: "resume_request";
  gameId: string;
}

/** Where to pick the log up: after the newest row the backend already holds, else from scratch. */
export interface Resume {
  type: "resume";
  gameId: string;
  after: string | null;
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
  | ClockReport
  | ResumeRequest;
export type WorkerToContent = BackendStatus | SnapshotRequest | Players | Facts | Settings | Resume;
export type PanelToWorker =
  | SnapshotRequest
  | AssignIdentity
  | ForgetIdentity
  | { type: "settings"; preset?: Preset; aiOn?: boolean; showCard?: boolean; showBadges?: boolean }
  /** Notes on one player for this session's prompts, saved with that game's log. */
  | { type: "notes"; text: string; playerId: string; gameId?: string }
  /** Re-run the decision on the table with another preset ("Ask deeper"). */
  | { type: "ask"; gameId: string; preset: Preset }
  /** Write this session's read and notes on a player to drafts/ for the user to review. */
  | { type: "save_draft"; gameId: string; playerId: string }
  /** One earlier hand for the replayer. */
  | { type: "hand_detail_request"; gameId: string; key: string };
export type WorkerToPanel =
  | BackendStatus
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | HandInfo
  | DraftSaved
  | HandHistory
  | HandDetail;
export type HostToWorker =
  | { type: "ready"; version: string; recording: boolean }
  | HostAck
  | Players
  | Facts
  | Settings
  | Costs
  | HandInfo
  | DraftSaved
  | HandHistory
  | HandDetail
  | Resume;
