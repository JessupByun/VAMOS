import { useEffect, useState } from "preact/hooks";

import type { BackendStatus, Facts, LogTrouble, Players } from "../shared/protocol";
import type { SeatElement } from "./dom/seats";
import type { Layout } from "./ui/placement";

export interface ViewState {
  status: BackendStatus;
  players: Players | null;
  seats: SeatElement[];
  /** The hero's own seat element, or null when the hero is not seated. */
  heroSeat: Element | null;
  /** Where the table's parts are on screen, for placing the action card. */
  layout: Layout;
  /** The key of the table at the hero's turn, or null when it is not the hero's turn. */
  turn: string | null;
  facts: Facts | null;
  /** Why PokerNow's log is failing for this game, or null while it answers. */
  logTrouble: LogTrouble | null;
  /** The action card shown on the table; switched in the side panel. */
  showCard: boolean;
  /** The players' badges shown under their seats; switched in the side panel. */
  showBadges: boolean;
}

export interface Store {
  get: () => ViewState;
  set: (patch: Partial<ViewState>) => void;
  subscribe: (listener: () => void) => () => void;
}

export function createStore(initial: ViewState): Store {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set: (patch) => {
      state = { ...state, ...patch };
      for (const listener of listeners) listener();
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useStore(store: Store): ViewState {
  const [, setVersion] = useState(0);
  useEffect(() => {
    const unsubscribe = store.subscribe(() => setVersion((n) => n + 1));
    // State may have changed between the first render and this subscription.
    setVersion((n) => n + 1);
    return unsubscribe;
  }, [store]);
  return store.get();
}
