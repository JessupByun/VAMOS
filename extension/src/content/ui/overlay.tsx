import {
  type BackendStatus,
  type ClaudeAdvice,
  type Facts,
  isClaude,
  type PlayerInfo,
  type Players,
  type SpotFacts,
} from "../../shared/protocol";
import { actionClass } from "../../shared/actions";
import type { SeatElement } from "../dom/seats";
import { type Store, useStore } from "../store";
import { chooseSpot, type Layout, slotStyle } from "./placement";

const BRAND = "VAMOS!";

function factsLine(facts: SpotFacts, advised: boolean): string {
  const need = facts.need === null ? null : `need ${facts.need}%`;
  const parts = [
    facts.position,
    facts.pot && `pot ${facts.pot}`,
    !advised && facts.toCall && `to call ${facts.toCall}`,
    need,
    advised && facts.madeHand,
  ];
  return parts.filter(Boolean).join(" · ");
}

function placed(layout: Layout): Record<string, string> {
  return slotStyle(chooseSpot(layout), layout);
}

function statusText(status: BackendStatus): string {
  if (status.state === "ready") {
    return `active (v${status.version})${status.recording ? " · recording" : ""}`;
  }
  if (status.state === "connecting") return "connecting...";
  return `down: ${status.detail ?? "unknown"}`;
}

/**
 * Everything VAMOS! shows on the table lives in one spot at the top of the table: the
 * backend's status while there is nothing else to say, the hero's turn when there is.
 */
function ActionCard({ store }: { store: Store }) {
  const { status, heroSeat, layout, turn, facts, showCard } = useStore(store);
  if (!showCard) return null;
  const ready = status.state === "ready";
  let body;
  if (!ready || !heroSeat || turn === null) {
    body = (
      <div class={`action-card waiting ${status.state}`}>
        <span class="brand">{BRAND}</span>
        <span class="status">{statusText(status)}</span>
        {ready && <span class="wait">· {heroSeat ? "waiting for your turn" : "not seated"}</span>}
      </div>
    );
  } else {
    body = <CardBody turn={turn} facts={facts} />;
  }
  return (
    <div class="card-slot" style={placed(layout)}>
      {body}
    </div>
  );
}

/**
 * What the card shows for the hero's turn `turn`: the backend's error on it, its facts, or
 * nothing yet. Anything computed for an earlier state of the table is never shown.
 */
export function forTurn(
  turn: string,
  facts: Facts | null,
): { error: string } | { facts: SpotFacts } | null {
  if (facts?.key !== turn) return null;
  if (facts.error) return { error: facts.error };
  return facts.facts ? { facts: facts.facts } : null;
}

function CardBody({ turn, facts }: { turn: string; facts: Facts | null }) {
  const shown = forTurn(turn, facts);
  if (shown && "error" in shown) {
    return (
      <div class="action-card error">
        <Source kind="error" label={BRAND} />
        <Action text="No advice" />
        <div class="line note warn">{shown.error}</div>
      </div>
    );
  }
  const current = shown?.facts ?? null;
  if (!current) {
    return (
      <div class="action-card neutral">
        <Source kind="neutral" label={BRAND} />
        <div class="line syncing">reading the hand...</div>
      </div>
    );
  }
  const advice = current.advice;
  if (isClaude(advice)) return <ClaudeCard facts={current} advice={advice} />;
  if (advice === null) {
    // No answer to show (AI off, and no preflop rule or chart covers the spot): the stats alone.
    return (
      <div class="action-card neutral">
        <Source kind="neutral" label={BRAND} />
        <Action text={current.madeHand} />
        {current.aiOff && <div class="line note">AI off: stats only</div>}
        <div class="line facts">{factsLine(current, false)}</div>
      </div>
    );
  }
  // Preflop, answered at once: the push/fold chart heads-up when short, else a rule.
  return (
    <div class="action-card chart">
      <Source kind="chart" label={advice.source === "Rule" ? "Preflop rule" : "Preflop chart"} />
      {advice.action ? <Action text={advice.action} /> : <Action text={current.madeHand} />}
      {advice.mix && <Mix parts={advice.mix} pick={advice.pick} deviation={advice.deviation} />}
      <div class="line facts">{factsLine(current, advice.action !== null)}</div>
    </div>
  );
}

function Source({ kind, label }: { kind: string; label: string }) {
  return (
    <div class={`source ${kind}`}>
      <span class="dot" />
      {label}
    </div>
  );
}

/** "BET $0.20 (1bb)": the move and its dollars large, its size in big blinds a step quieter. */
function Action({ text, thinking = false }: { text: string; thinking?: boolean }) {
  if (thinking) {
    return (
      <div class="action thinking">
        thinking<span class="ellipsis">...</span>
      </div>
    );
  }
  const split = text.match(/^(.*?)( \([^)]*\))$/);
  return (
    <div class={`action ${actionClass(text)}`}>
      {split ? split[1] : text}
      {split && <span class="bb">{split[2]}</span>}
    </div>
  );
}

/**
 * Every option with its frequency; the one drawn is highlighted. A draw from a less
 * frequent option than the top one is a deliberate deviation and says so.
 */
function Mix(props: { parts: string[]; pick: number | null; deviation: boolean }) {
  const { parts, pick, deviation } = props;
  return (
    <div class="line mix">
      {parts.map((part, i) => (
        <span key={part} class={`chip ${actionClass(part)}${i === pick ? " picked" : ""}`}>
          {part}
        </span>
      ))}
      {deviation && <span class="chip deviation">deviation</span>}
    </div>
  );
}

function aiLabel(model: string): string {
  return `${BRAND} AI (${model})`;
}

function ClaudeCard({ facts, advice }: { facts: SpotFacts; advice: ClaudeAdvice }) {
  if (advice.action === null) {
    // No answer yet, or none coming: the stats stay up, with the reason when there is one.
    const failed = advice.state === "failed";
    return (
      <div class="action-card claude">
        <Source kind="claude" label={aiLabel(advice.asked)} />
        {failed ? <Action text={facts.madeHand} /> : <Action text="" thinking />}
        {failed && <div class="line note warn">no answer: {advice.reason}</div>}
        <div class="line facts">{factsLine(facts, false)}</div>
      </div>
    );
  }
  const upgrading = advice.asked !== advice.model;
  return (
    <div class="action-card claude">
      <Source kind="claude" label={aiLabel(advice.model)} />
      <Action text={advice.action} />
      {advice.mix && <Mix parts={advice.mix} pick={advice.pick} deviation={advice.deviation} />}
      {upgrading && advice.state === "thinking" && (
        <div class="line note">asking {advice.asked}...</div>
      )}
      {upgrading && advice.state === "failed" && (
        <div class="line note warn">
          {advice.asked}: {advice.reason}
        </div>
      )}
      <div class="line facts">{factsLine(facts, true)}</div>
    </div>
  );
}

function pct(count: number, of: number): string {
  return `${Math.round((100 * count) / of)}%`;
}

/** Tonight's numbers only, e.g. "VPIP 74% (14/19) · PFR 21%". */
function tonight(info: PlayerInfo): string | null {
  const t = info.tonight;
  if (!t || t.hands === 0) return null;
  return `VPIP ${pct(t.vpip, t.hands)} (${t.vpip}/${t.hands}) · PFR ${pct(t.pfr, t.hands)}`;
}

function WhoIsThis(props: {
  info: PlayerInfo;
  profiles: Players["profiles"];
  onAssign: (playerId: string, profile: string | null) => void;
}) {
  const choose = (event: Event) => {
    const value = (event.currentTarget as HTMLSelectElement).value;
    if (value) props.onAssign(props.info.id, value === "__none__" ? null : value);
  };
  return (
    <span class="who">
      <span class="name">{props.info.name}</span>
      {/* The seat is narrow: the menu says "Match profile"; its full name is for the reader. */}
      <select
        aria-label={`Match a profile for ${props.info.name}`}
        title="Match existing player profile"
        onChange={choose}
      >
        <option value="">Match profile</option>
        {props.profiles.map((p) => (
          <option key={p.key} value={p.key}>
            {p.name}
          </option>
        ))}
        <option value="__none__">No profile</option>
      </select>
    </span>
  );
}

function Badge(props: {
  seat: SeatElement;
  info: PlayerInfo | undefined;
  profiles: Players["profiles"];
  onAssign: (playerId: string, profile: string | null) => void;
}) {
  const { seat, info } = props;
  if (!info) return null;
  // Badges sit just under their seat, as wide as it, in VAMOS!'s own layer; PokerNow's DOM
  // is untouched.
  const rect = seat.element.getBoundingClientRect();
  const style = { left: `${rect.left}px`, top: `${rect.bottom + 2}px`, width: `${rect.width}px` };
  let body;
  if (info.status === "unknown") {
    body = <WhoIsThis info={info} profiles={props.profiles} onAssign={props.onAssign} />;
  } else {
    // The person (their profile's name when known, or "You") and tonight's numbers; the
    // profile itself is in the side panel.
    const parts = [info.hero ? "You" : (info.profile?.name ?? info.name), tonight(info)];
    body = <span class="text">{parts.filter(Boolean).join(" · ")}</span>;
  }
  return (
    <div class={`badge ${info.hero ? "me" : info.status}`} style={style} data-player={info.id}>
      {body}
    </div>
  );
}

export function Overlay(props: {
  store: Store;
  onAssign: (playerId: string, profile: string | null) => void;
}) {
  const { seats, players, heroSeat, showBadges } = useStore(props.store);
  const byId = new Map(players?.players.map((p) => [p.id, p]));
  // The hero's own numbers tonight, under the hero's seat like everyone else's.
  const hero = players?.players.find((p) => p.hero);
  return (
    <>
      <ActionCard store={props.store} />
      {showBadges && heroSeat && hero && (
        <Badge
          key={`hero-${hero.id}`}
          seat={{ id: hero.id, name: hero.name, element: heroSeat }}
          info={hero}
          profiles={players?.profiles ?? []}
          onAssign={props.onAssign}
        />
      )}
      {showBadges &&
        seats.map((seat) => (
        <Badge
          key={seat.id}
          seat={seat}
          info={byId.get(seat.id)}
          profiles={players?.profiles ?? []}
          onAssign={props.onAssign}
        />
      ))}
    </>
  );
}
