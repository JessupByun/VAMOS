import type { PlayerInfo, Players } from "../../shared/protocol";
import type { SeatElement } from "../dom/seats";
import { type Store, useStore } from "../store";

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
