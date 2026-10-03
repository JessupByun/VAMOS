import {
  type ClaudeAdvice,
  type Facts,
  isClaude,
  type PlayerInfo,
  type Players,
  type SpotFacts,
} from "../../shared/protocol";
import type { SeatElement } from "../dom/seats";
import { type Store, useStore } from "../store";
import { chooseSpot, type Layout, slotStyle } from "./placement";

function Pill({ store }: { store: Store }) {
  const { status } = useStore(store);
  const label =
    status.state === "ready"
      ? `backend ready (v${status.version})${status.recording ? " · recording" : ""}`
      : status.state === "connecting"
        ? "connecting..."
        : `backend down: ${status.detail ?? "unknown"}`;
  return (
    <div class={`card ${status.state}`}>
      <span class="brand">VAMOS</span>
      <span class="status">{label}</span>
    </div>
  );
}

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

function placed(layout: Layout | null): Record<string, string> {
  if (!layout) return { right: "16px", top: "45%" };
  return slotStyle(chooseSpot(layout), layout);
}

/** Shown only while the hero is seated; the decision only on the hero's turn. */
function ActionCard({ store }: { store: Store }) {
  const { heroSeat, layout, turn, facts } = useStore(store);
  if (!heroSeat) return null;
  return (
    <div class="card-slot" style={placed(layout)}>
      <CardBody turn={turn} facts={facts} />
    </div>
  );
}

function CardBody({ turn, facts }: { turn: string | null; facts: Facts | null }) {
  if (turn === null) return <div class="action-card waiting">waiting for your turn</div>;
  // Facts computed for an earlier state of the table are never shown.
  const current = facts?.key === turn ? facts.facts : null;
  if (!current) {
    return (
      <div class="action-card">
        <div class="source math">Math</div>
        <div class="line syncing">syncing with the log...</div>
      </div>
    );
  }
  const advice = current.advice;
  if (isClaude(advice)) return <ClaudeCard facts={current} advice={advice} />;
  const chart = advice?.action ? advice : null;
  return (
    <div class="action-card">
      <div class={`source ${chart ? "chart" : "math"}`}>{chart ? "Chart" : "Math"}</div>
      <div class="action">{chart ? chart.action : current.madeHand}</div>
      {chart?.mix && <div class="line mix">{chart.mix.join(" · ")}</div>}
      {advice && !advice.action && <div class="line note">no chart for this spot</div>}
      <div class="line facts">{factsLine(current, chart !== null)}</div>
    </div>
  );
}

function claudeLabel(model: string): string {
  return `VAMOS AI (${model})`;
}

function ClaudeCard({ facts, advice }: { facts: SpotFacts; advice: ClaudeAdvice }) {
  if (advice.action === null) {
    // No answer yet, or none coming: the math stays up, with the reason when there is one.
    const failed = advice.state === "failed";
    return (
      <div class="action-card">
        <div class={`source ${failed ? "math" : "claude"}`}>
          {failed ? "Math" : claudeLabel(advice.asked)}
        </div>
        <div class={`action ${failed ? "" : "thinking"}`}>{failed ? facts.madeHand : "thinking..."}</div>
        {failed && <div class="line note">VAMOS AI: {advice.reason}</div>}
        <div class="line facts">{factsLine(facts, false)}</div>
      </div>
    );
  }
  const upgrading = advice.asked !== advice.model;
  return (
    <div class="action-card">
      <div class="source claude">{claudeLabel(advice.model)}</div>
      <div class="action">{advice.action}</div>
      {advice.mix && <div class="line mix">{advice.mix.join(" · ")}</div>}
      {upgrading && advice.state === "thinking" && (
        <div class="line note">asking {advice.asked}...</div>
      )}
      {upgrading && advice.state === "failed" && (
        <div class="line note">
          {advice.asked}: {advice.reason}
        </div>
      )}
      <div class="line facts">{factsLine(facts, true)}</div>
    </div>
  );
}

function tonight(info: PlayerInfo): string | null {
  return info.tonight && info.tonight.hands > 0
    ? `VPIP ${info.tonight.vpip}/${info.tonight.hands} tonight`
    : null;
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
      <select aria-label={`Who is ${props.info.name}?`} onChange={choose}>
        <option value="">who is this?</option>
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
  // Badges sit just under their seat, in VAMOS's own layer; PokerNow's DOM is untouched.
  const rect = seat.element.getBoundingClientRect();
  const style = { left: `${rect.left}px`, top: `${rect.bottom + 2}px`, width: `${rect.width}px` };
  let body;
  if (info.status === "unknown") {
    body = <WhoIsThis info={info} profiles={props.profiles} onAssign={props.onAssign} />;
  } else {
    const parts = [info.profile?.name ?? info.name, info.profile?.tag ?? "no profile", tonight(info)];
    body = <span class="text">{parts.filter(Boolean).join(" · ")}</span>;
  }
  return (
    <div class={`badge ${info.status}`} style={style} data-player={info.id}>
      {body}
    </div>
  );
}

export function Overlay(props: {
  store: Store;
  onAssign: (playerId: string, profile: string | null) => void;
}) {
  const { seats, players } = useStore(props.store);
  const byId = new Map(players?.players.map((p) => [p.id, p]));
  return (
    <>
      <Pill store={props.store} />
      <ActionCard store={props.store} />
      {seats.map((seat) => (
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
